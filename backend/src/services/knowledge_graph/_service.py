"""KnowledgeGraphService - entity/relation orchestration."""

import asyncio
import logging
import re as _re

from ...core import database as db
from ...core.database import get_write_connection
from . import settings
from ._parsing import _parse_entities_json
from ._storage import _store_entities, _store_relations
from ._vectorstore import get_entity_vectorstore

logger = logging.getLogger(__name__)

# Simple PII patterns — comprehensive detection would use a dedicated library
# like presidio, but these catch the most common accidental leaks.
_PII_PATTERNS = [
    _re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),  # email
    _re.compile(r"^\d{3}-\d{2}-\d{4}$"),  # SSN (US)
    _re.compile(r"^\d{4}\s\d{4}\s\d{4}\s\d{4}$"),  # credit card
]


def _looks_like_pii(name: str) -> bool:
    """Return True if the name looks like structured PII rather than a real entity."""
    stripped = name.strip()
    # Phone number: at least 10 digit characters within a reasonable length
    # window.  Avoids false positives like "RFC-2616" (only 4 digits).
    if (
        _re.fullmatch(r"^\+?1?\s*[\d\-() ]{10,14}$", stripped)
        and sum(c.isdigit() for c in stripped) >= 10
    ):
        return True
    return any(pat.match(stripped) for pat in _PII_PATTERNS)


def _batch_get_entities_with_relations(
    conn,
    entity_ids: list[int],
    relations_limit: int,
    *,
    user_id: str,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
    excluded_session_ids: set[str] | None = None,
    strict_scope: bool = True,
) -> dict[int, dict]:
    """Fetch entities and their relations in two batch queries.

    Returns a mapping of entity_id -> {"entity": row_dict, "relations": [row_dict, ...]}.
    """
    if not entity_ids:
        return {}

    placeholders = ",".join("?" * len(entity_ids))

    entities = conn.execute(
        f"SELECT * FROM memory_entities WHERE id IN ({placeholders}) AND user_id=?",
        [*entity_ids, user_id],
    ).fetchall()

    from ...core.database.knowledge_graph import _decode_aliases

    entity_map: dict[int, dict] = {}
    for row in entities:
        record = dict(row)
        record["aliases"] = _decode_aliases(record.get("aliases"))
        entity_map[record["id"]] = record

    if not entity_map:
        return {}

    relation_where = (
        f"(subject_id IN ({placeholders}) OR object_id IN ({placeholders})) "
        "AND user_id=? "
        "AND (valid_from IS NULL OR julianday(valid_from)<=julianday('now')) "
        "AND (valid_to IS NULL OR julianday(valid_to)>julianday('now'))"
    )
    relation_values: list[object] = [*entity_ids, *entity_ids, user_id]
    if excluded_session_ids:
        excluded = sorted(excluded_session_ids)
        relation_where += (
            " AND (source_session IS NULL OR source_session NOT IN ("
            + ",".join("?" * len(excluded))
            + "))"
        )
        relation_values.extend(excluded)
    relations = conn.execute(
        "SELECT * FROM memory_relations WHERE " + relation_where,
        relation_values,
    ).fetchall()

    # Resolve names for any entities referenced by relations that are not in entity_map
    referenced_ids: set[int] = set()
    for rel in relations:
        referenced_ids.add(rel["subject_id"])
        referenced_ids.add(rel["object_id"])
    all_entity_ids = referenced_ids | set(entity_map.keys())
    missing_ids = referenced_ids - set(entity_map.keys())
    name_lookup: dict[int, str] = {eid: row["name"] for eid, row in entity_map.items()}
    if missing_ids:
        extra_placeholders = ",".join("?" * len(missing_ids))
        extra_rows = conn.execute(
            f"SELECT id, name FROM memory_entities WHERE id IN ({extra_placeholders}) "
            "AND user_id=?",
            [*missing_ids, user_id],
        ).fetchall()
        for row in extra_rows:
            name_lookup[row["id"]] = row["name"]

    scope_map: dict[int, dict[str, set[int]]] = {
        entity_id: {"meeting": set(), "file": set()} for entity_id in all_entity_ids
    }
    if all_entity_ids:
        scope_placeholders = ",".join("?" * len(all_entity_ids))
        for row in conn.execute(
            f"SELECT entity_id,scope_type,scope_id FROM entity_scopes "
            f"WHERE entity_id IN ({scope_placeholders})",
            list(all_entity_ids),
        ).fetchall():
            if row["scope_type"] in {"meeting", "file"}:
                scope_map[int(row["entity_id"])][row["scope_type"]].add(int(row["scope_id"]))

    meeting_scope = set(meeting_ids or [])
    file_scope = set(file_ids or [])
    scope_active = bool(meeting_scope or file_scope)

    def _entity_matches_scope(entity_id: int) -> bool:
        if not scope_active:
            return True
        scopes = scope_map.get(entity_id, {"meeting": set(), "file": set()})
        entity_meetings = scopes["meeting"]
        entity_files = scopes["file"]
        if not entity_meetings and not entity_files:
            return not strict_scope
        return bool(
            (meeting_scope and entity_meetings & meeting_scope)
            or (file_scope and entity_files & file_scope)
        )

    # Group only relations whose two endpoints are visible in the requested
    # scope. A globally accumulated entity must not pull an edge from another
    # meeting into a scoped answer.
    rels_by_entity: dict[int, list[dict]] = {eid: [] for eid in entity_map}
    for rel in relations:
        sid = rel["subject_id"]
        oid = rel["object_id"]
        if sid not in name_lookup or oid not in name_lookup:
            continue
        if not _entity_matches_scope(sid) or not _entity_matches_scope(oid):
            continue
        for home_id, other_id in ((sid, oid), (oid, sid)):
            if home_id in rels_by_entity:
                enriched = dict(rel)
                enriched["other_name"] = name_lookup.get(other_id, "")
                rels_by_entity[home_id].append(enriched)

    return {
        eid: {"entity": entity, "relations": rels_by_entity[eid][:relations_limit]}
        for eid, entity in entity_map.items()
    }


_extraction_semaphore: asyncio.Semaphore | None = None


def _get_extraction_semaphore() -> asyncio.Semaphore:
    global _extraction_semaphore
    if _extraction_semaphore is None:
        _extraction_semaphore = asyncio.Semaphore(2)
    return _extraction_semaphore


class KnowledgeGraphService:
    """Extracts, stores, and retrieves entities and relations."""

    async def extract_entities(
        self,
        user_id: str,
        question: str,
        answer: str,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        evidence_message_ids: list[int] | None = None,
        evidence_text: str | None = None,
        raise_on_error: bool = False,
    ) -> dict:
        """Extract entities and relations from a Q&A turn, persist to DB + Chroma.

        Returns {"entities_added": int, "relations_added": int}.
        Skips silently when KNOWLEDGE_GRAPH_ENABLED is False.
        """
        if not settings.KNOWLEDGE_GRAPH_ENABLED:
            return {"entities_added": 0, "relations_added": 0}
        if settings.MEMORY_EXTRACTION_MODE == "precise":
            return {"entities_added": 0, "relations_added": 0}
        try:
            from ..llm import (
                cached_retry_invoke,
                escape_prompt_data,
                get_entity_extraction_prompt,
                get_extraction_llm,
            )

            llm = get_extraction_llm()
            prompt_template = get_entity_extraction_prompt()
            prompt = prompt_template.format(
                question=escape_prompt_data(question),
                answer=escape_prompt_data(answer),
            )
            async with _get_extraction_semaphore():
                response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                return {"entities_added": 0, "relations_added": 0}

            parsed = _parse_entities_json(content)
            if not parsed:
                return {"entities_added": 0, "relations_added": 0}

            # Filter out entities that look like PII (emails, phone numbers, etc.)
            # to avoid inadvertently storing third-party personal data in the KG.
            entities = [e for e in parsed["entities"] if not _looks_like_pii(e.get("name", ""))]
            if len(entities) < len(parsed["entities"]):
                logger.debug(
                    "Filtered %d PII-like entities from KG extraction",
                    len(parsed["entities"]) - len(entities),
                )

            if not entities:
                return {"entities_added": 0, "relations_added": 0}

            entities_added = await _store_entities(
                user_id,
                entities,
                session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
            )
            # A generated answer is never authoritative relation evidence.
            # Source-grounded callers provide retrieved text; otherwise only
            # the user's message may support a persisted graph edge.
            relations_added = await _store_relations(
                user_id,
                parsed["relations"],
                session_id,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text or question,
            )

            if entities_added or relations_added:
                logger.debug(
                    "KG: +%d entities, +%d relations for user %s",
                    entities_added,
                    relations_added,
                    user_id,
                )
            return {"entities_added": entities_added, "relations_added": relations_added}
        except Exception:
            logger.warning("Entity extraction failed for user %s", user_id, exc_info=True)
            if raise_on_error:
                raise
            return {"entities_added": 0, "relations_added": 0}

    async def get_entity_context(
        self,
        user_id: str,
        query: str,
        top_k: int = 3,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        excluded_session_ids: set[str] | None = None,
    ) -> str:
        """Semantic search over entities + relation expansion.

        When ``meeting_ids`` or ``file_ids`` are provided, entities are filtered
        to those that intersect the scope. Entities with no scope metadata are
        treated as global: they pass the filter unless ``SCOPED_MEMORY_STRICT``
        is enabled, in which case they are excluded.
        """
        if not settings.KNOWLEDGE_GRAPH_ENABLED:
            return ""
        try:
            from ...core.config import settings as core_settings

            vs = get_entity_vectorstore()
            scope_active = bool(meeting_ids or file_ids)
            oversample = (
                getattr(core_settings, "MEMORY_SEARCH_OVERSAMPLE_FACTOR", 5) if scope_active else 2
            )
            results = await asyncio.to_thread(
                vs.similarity_search, query, user_id, top_k, fetch_multiplier=oversample
            )
            if not results:
                return ""

            if scope_active:
                strict = getattr(core_settings, "SCOPED_MEMORY_STRICT", False)
                meeting_scope = set(meeting_ids or [])
                file_scope = set(file_ids or [])

                def _matches(r: dict) -> bool:
                    r_mids = set(r.get("meeting_ids") or [])
                    r_fids = set(r.get("file_ids") or [])
                    if not r_mids and not r_fids:
                        return not strict
                    if meeting_scope and r_mids & meeting_scope:
                        return True
                    if file_scope and r_fids & file_scope:
                        return True
                    if not r_mids and file_scope:
                        # Entity has only file scope, none of which match
                        return False
                    if not r_fids and meeting_scope:
                        return False
                    return False

                results = [r for r in results if _matches(r)]
                if not results:
                    return ""

            # Collect unique entity IDs, then batch-fetch entities and relations
            entity_ids: list[int] = []
            seen_ids: set[int] = set()
            for r in results:
                eid = r.get("entity_id")
                if eid is not None and eid not in seen_ids:
                    seen_ids.add(eid)
                    entity_ids.append(eid)

            if not entity_ids:
                return ""

            with db.get_connection() as conn:
                batch = _batch_get_entities_with_relations(
                    conn,
                    entity_ids,
                    settings.ENTITY_RELATIONS_LIMIT,
                    user_id=user_id,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                    excluded_session_ids=excluded_session_ids,
                    strict_scope=getattr(core_settings, "SCOPED_MEMORY_STRICT", True),
                )

            scope_active = bool(meeting_ids or file_ids)
            lines: list[str] = []
            for eid in entity_ids:
                entry = batch.get(eid)
                if not entry:
                    continue
                entity = entry["entity"]
                relations = entry["relations"]

                # Drop legacy pre-scope entities from scoped queries: the
                # vector metadata filter let them through as "global", but the
                # DB flag tells us they predate the scope column and should
                # not pollute meeting-specific context.
                if scope_active and entity.get("is_legacy_scope"):
                    continue

                line = f"- {entity['entity_type'].capitalize()}: **{entity['name']}**"
                if entity.get("description"):
                    line += f" ({entity['description']})"
                if relations:
                    rel_parts = [f"{rel['predicate']} {rel['other_name']}" for rel in relations[:5]]
                    line += f" [relations: {', '.join(rel_parts)}]"
                lines.append(line)

            return "\n".join(lines) if lines else ""
        except Exception as e:
            logger.warning("Entity context lookup failed: %s", e)
            return ""

    def get_entities(
        self,
        user_id: str,
        entity_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List entities for a user, sorted by mention_count desc."""
        with db.get_connection() as conn:
            return db.list_entities(
                conn,
                user_id=user_id,
                entity_type=entity_type,
                limit=limit,
                offset=offset,
            )

    def get_entity_with_relations(self, user_id: str, name: str) -> dict | None:
        """Get an entity and all its direct relations."""
        with db.get_connection() as conn:
            entity = db.get_entity_by_name(conn, user_id=user_id, name=name.strip().lower())
            if not entity:
                return None
            relations = db.list_entity_relations(
                conn,
                entity_id=entity["id"],
                limit=settings.ENTITY_RELATIONS_LIMIT,
            )
        return {"entity": dict(entity), "relations": relations}

    def delete_entity(self, user_id: str, name: str) -> bool:
        """Delete an entity by name (cascades to its relations). Returns True if deleted.

        The SQL delete and vector-cleanup outbox entry share one transaction,
        so a process crash cannot leave an untracked orphan vector.
        """
        with get_write_connection() as conn:
            entity = db.get_entity_by_name(conn, user_id=user_id, name=name.strip().lower())
            if not entity:
                return False
            if entity.get("embedding_id"):
                conn.execute(
                    "INSERT OR IGNORE INTO pending_vector_deletions "
                    "(collection, embedding_id) VALUES ('entity', ?)",
                    (entity["embedding_id"],),
                )
            db.delete_entity(conn, entity_id=entity["id"])

        if entity.get("embedding_id"):
            from ..memory._service._crud import cleanup_pending_vector_deletions

            try:
                cleanup_pending_vector_deletions(collections={"entity"})
            except Exception:
                logger.warning(
                    "Immediate entity vector cleanup failed for %s; durable job remains queued",
                    entity["embedding_id"],
                    exc_info=True,
                )
        return True

    def delete_entities(self, user_id: str, names: list[str]) -> tuple[list[str], list[str]]:
        """Delete multiple entities atomically and durably queue vector cleanup."""
        unique_names = list(dict.fromkeys(name.strip().lower() for name in names))
        deleted: list[str] = []
        embedding_ids: list[str] = []
        with get_write_connection() as conn:
            for name in unique_names:
                entity = db.get_entity_by_name(conn, user_id=user_id, name=name)
                if not entity:
                    continue
                embedding_id = entity.get("embedding_id")
                if embedding_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO pending_vector_deletions "
                        "(collection, embedding_id) VALUES ('entity', ?)",
                        (embedding_id,),
                    )
                    embedding_ids.append(embedding_id)
                db.delete_entity(conn, entity_id=entity["id"])
                deleted.append(name)

        if embedding_ids:
            from ..memory._service._crud import cleanup_pending_vector_deletions

            try:
                cleanup_pending_vector_deletions(collections={"entity"})
            except Exception:
                logger.warning(
                    "Immediate batch entity vector cleanup failed; durable jobs remain queued",
                    exc_info=True,
                )
        missing = [name for name in unique_names if name not in deleted]
        return deleted, missing

    def merge_entities(
        self,
        user_id: str,
        source_names: list[str],
        target_name: str,
    ) -> bool:
        """Merge source entities into target: reassign relations, delete sources.

        Uses a single write connection (and thus a single transaction) for
        both reading and writing to prevent TOCTOU races under concurrency.
        """
        try:
            target_name_norm = target_name.strip().lower()

            # Single write connection — reads acquire a reserved lock so
            # concurrent merges on the same entities serialize correctly.
            with get_write_connection() as conn:
                target = db.get_entity_by_name(conn, user_id=user_id, name=target_name_norm)
                if target is None:
                    return False
                source_rows = [
                    db.get_entity_by_name(conn, user_id=user_id, name=n.strip().lower())
                    for n in source_names
                    if n.strip().lower() != target_name_norm
                ]
                source_rows = [r for r in source_rows if r]

                if not source_rows:
                    return False

                # Queue vector deletion before deleting the authoritative row;
                # both writes commit or roll back together.
                source_embedding_ids: list[str] = []
                for src in source_rows:
                    db.reassign_entity_relations(
                        conn,
                        source_id=src["id"],
                        target_id=target["id"],
                        user_id=user_id,
                    )
                    if src.get("embedding_id"):
                        conn.execute(
                            "INSERT OR IGNORE INTO pending_vector_deletions "
                            "(collection, embedding_id) VALUES ('entity', ?)",
                            (src["embedding_id"],),
                        )
                        source_embedding_ids.append(src["embedding_id"])
                    db.delete_entity(conn, entity_id=src["id"])

            if source_embedding_ids:
                from ..memory._service._crud import cleanup_pending_vector_deletions

                try:
                    cleanup_pending_vector_deletions(collections={"entity"})
                except Exception:
                    logger.warning(
                        "Immediate merged-entity vector cleanup failed; durable jobs remain queued",
                        exc_info=True,
                    )

            return True
        except Exception:
            logger.warning("Entity merge failed", exc_info=True)
            return False

    def get_entity_provenance(self, user_id: str, name: str) -> dict | None:
        """Return entity origin info: session_id, meeting_ids, relation_count.

        Used by the chat response pipeline to cite where an entity came from.
        """
        with db.get_connection() as conn:
            entity = db.get_entity_by_name(conn, user_id=user_id, name=name.strip().lower())
            if not entity:
                return None
            relations = db.list_entity_relations(
                conn,
                entity_id=entity["id"],
                limit=settings.ENTITY_RELATIONS_LIMIT,
            )
        return {
            "name": entity["name"],
            "type": entity["entity_type"],
            "session_id": entity.get("first_seen_session"),
            "meeting_ids": entity.get("meeting_ids") or [],
            "relation_count": len(relations),
        }

    def sync_missing_entity_vectors(self) -> int:
        """Re-index entities whose embedding_id is NULL (Chroma write had failed).

        Returns count of entities re-indexed.
        """
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, user_id, name, entity_type, description "
                "FROM memory_entities WHERE embedding_id IS NULL"
            ).fetchall()

        count = 0
        for row in rows:
            # sqlite3.Row supports subscript access only, not dict.get(); the
            # SELECT above already includes ``description``, so we read it
            # directly. ``description`` may be NULL — pass through as None.
            try:
                vs = get_entity_vectorstore()
                embedding_id = vs.upsert(
                    row["id"],
                    row["user_id"],
                    row["name"],
                    row["entity_type"],
                    row["description"],
                )
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE memory_entities SET embedding_id=? WHERE id=?",
                        (embedding_id, row["id"]),
                    )
                count += 1
            except Exception:
                logger.debug("Failed to re-index entity %s", row["id"], exc_info=True)
        if count:
            logger.info("Re-indexed %d missing entity vectors", count)
        return count
