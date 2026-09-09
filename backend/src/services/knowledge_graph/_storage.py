"""Entity and relation persistence logic."""

import asyncio
import logging
import math
import re

from ...core import database as db
from ...core.config import settings
from ...core.database import get_write_connection
from ..assertion_validation import current_supporting_clauses, normalize_assertion_text
from ._vectorstore import get_entity_vectorstore

logger = logging.getLogger(__name__)

ENTITY_TYPES = frozenset(
    {"person", "project", "topic", "organization", "tool", "concept", "location"}
)
RELATION_PREDICATES = frozenset(
    {
        "works_on",
        "uses",
        "prefers",
        "related_to",
        "member_of",
        "leads",
        "discussed_in",
        "decided",
        "mentions",
    }
)

_RELATION_EVIDENCE_PATTERNS = {
    "works_on": r"\b(?:works?|worked|working)\s+on\b|负责|参与",
    "uses": r"\b(?:uses?|used|using)\b|使用|采用",
    "prefers": r"\b(?:prefers?|preferred)\b|偏好|首选",
    "member_of": r"\b(?:member\s+of|belongs?\s+to|joined)\b|成员|加入",
    "leads": r"\b(?:leads?|led|owns?|owner|responsible\s+for)\b|负责|负责人",
    "decided": r"\b(?:decided|approved|rejected|resolved)\b|决定|批准|拒绝|决议",
    "related_to": (
        r"\b(?:related\s+to|associated\s+with|depends?\s+on|blocked\s+by)\b|相关|依赖|阻塞"
    ),
    "discussed_in": r"\b(?:discussed\s+in|covered\s+in)\b|在.+讨论|见于",
    "mentions": r"\b(?:mentions?|mentioned)\b|提到|谈及",
}

_DIRECT_RELATION_CUES = {
    "works_on": r"(?:works?|worked|working)\s+on|负责|参与",
    "uses": r"uses?|used|using|使用|采用",
    "prefers": r"prefers?|preferred|偏好|首选",
    "member_of": r"(?:is\s+)?(?:a\s+)?member\s+of|belongs?\s+to|joined|属于|成员|加入",
    "decided": r"decided|approved|rejected|resolved|决定|批准|拒绝|决议",
    "mentions": r"mentions?|mentioned|提到|谈及",
    "discussed_in": r"discussed\s+in|covered\s+in|在.+讨论|见于",
    "related_to": (
        r"(?:is\s+)?related\s+to|associated\s+with|depends?\s+on|"
        r"(?:was\s+)?blocked\s+by|相关|依赖|阻塞"
    ),
}

_LEAD_RELATION_PATTERNS = (
    re.compile(
        r"\b(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+"
        r"(?:now\s+|currently\s+)?(?:owns?|leads?|is\s+responsible\s+for)\s+"
        r"(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+replaced\s+.+?\s+"
        r"as\s+(?:the\s+)?owner\s+of\s+(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s\uFF0C\u3002\uFF1B;])"
        r"(?P<owner>[\u3400-\u9fffA-Za-z0-9_-]{2,20})"
        r"(?:现在|当前)?负责(?P<target>[^\uFF0C\u3002\uFF1B;]+)"
    ),
)


def _normalized_relation_text(value: object) -> str:
    return normalize_assertion_text(value)


def _entity_pattern(value: object) -> str:
    return r"\s+".join(re.escape(part) for part in str(value).strip().split())


def _direct_relation_is_supported(rel: dict, clause: str, predicate: str) -> bool:
    """Require a local subject → predicate → object assertion."""
    cue = _DIRECT_RELATION_CUES.get(predicate)
    if not cue:
        return False
    subject = _entity_pattern(rel.get("subject"))
    obj = _entity_pattern(rel.get("object"))
    # Tight adjacency prevents a relation from borrowing another subject's
    # object later in the same sentence ("Alice uses X and Bob uses Y").
    pattern = (
        rf"(?<!\w){subject}(?!\w)\s*"
        rf"(?:(?:now|currently)\s+)?(?:{cue})\s*"
        rf"(?:(?:the|a|an)\s+)?(?<!\w){obj}(?!\w)"
    )
    return re.search(pattern, clause, re.IGNORECASE) is not None


def _relation_is_supported(rel: dict, evidence_text: str | None) -> bool:
    """Require both endpoints and a supported relation cue in source evidence."""
    if not evidence_text:
        return False
    subject = _normalized_relation_text(rel.get("subject"))
    obj = _normalized_relation_text(rel.get("object"))
    if not subject or not obj:
        return False
    predicate = str(rel.get("predicate") or "related_to").lower().strip()
    pattern = _RELATION_EVIDENCE_PATTERNS.get(predicate)
    clauses = current_supporting_clauses(
        evidence_text,
        terms=(rel.get("subject"), rel.get("object")),
        cue_pattern=pattern,
    )
    if not clauses:
        return False
    if predicate == "leads":
        parsed = [
            (match.group("owner"), match.group("target"))
            for relation_pattern in _LEAD_RELATION_PATTERNS
            for clause in clauses
            for match in relation_pattern.finditer(clause)
        ]
        return any(
            subject == _normalized_relation_text(owner) and obj == _normalized_relation_text(target)
            for owner, target in parsed
        )
    return any(_direct_relation_is_supported(rel, clause, predicate) for clause in clauses)


# Cosine-similarity-style threshold above which two entity surface forms are
# treated as aliases of the same canonical entity. The entity vector store
# returns L2 distances (lower=better), so we convert this to a max-distance
# bound assuming normalized embeddings: cos = 1 - L2^2 / 2, so for the
# threshold above the max L2 ~= sqrt(2 * (1 - threshold)).
def _get_alias_merge_max_l2() -> float:
    """Recompute the L2 threshold from the current config value so runtime
    settings changes take effect without restart."""
    return math.sqrt(2.0 * (1.0 - settings.ENTITY_ALIAS_MERGE_THRESHOLD))


_ENTITY_NAME_MAX_LENGTH = 100


def _sanitize_entity_name(name: str) -> str:
    """Reject or truncate entity names with unsafe characters (MEDIUM-9)."""
    if not name or not name.strip():
        raise ValueError("Entity name must not be empty")
    if any(ord(c) < 32 for c in name):
        raise ValueError(f"Entity name contains control characters: {name!r}")
    if len(name) > _ENTITY_NAME_MAX_LENGTH:
        name = name[:_ENTITY_NAME_MAX_LENGTH]
    return name.strip()


def _resolve_canonical(
    *,
    name: str,
    entity_type: str,
    user_id: str,
    llm_aliases: list[str] | None = None,
) -> str | None:
    """Return the canonical name of an existing similar entity, or None.

    Performs a semantic similarity lookup against the entity vector store and
    returns the closest hit whose distance is within the alias-merge bound and
    whose ``entity_type`` matches.  When *llm_aliases* are provided, exact alias
    matches on existing entities are treated as a strong merge signal even when
    the vector distance is borderline (M-7).

    Returns ``None`` when there is no acceptable candidate.
    """
    try:
        vs = get_entity_vectorstore()
    except Exception:
        from ...core.metrics import KG_CANONICAL_RESOLVE_FAILED_TOTAL

        KG_CANONICAL_RESOLVE_FAILED_TOTAL.inc()
        logger.warning("Entity vector store unavailable; alias merge disabled", exc_info=True)
        return None
    try:
        hits = vs.similarity_search(name, user_id, top_k=3, fetch_multiplier=1)
    except Exception:
        from ...core.metrics import KG_CANONICAL_RESOLVE_FAILED_TOTAL

        KG_CANONICAL_RESOLVE_FAILED_TOTAL.inc()
        logger.warning("Alias similarity search failed for '%s'", name, exc_info=True)
        return None

    # M-7: Build a lookup set of LLM-provided aliases (lowercased) for fuzzy matching.
    alias_lookup: set[str] = set()
    if llm_aliases:
        alias_lookup = {a.strip().lower() for a in llm_aliases if a and a.strip()}

    for hit in hits:
        hit_name = (hit.get("name") or "").strip().lower()
        hit_type = (hit.get("entity_type") or "").strip().lower()
        hit_score = hit.get("score")
        if not hit_name or hit_name == name:
            continue
        if hit_type and hit_type != entity_type:
            continue
        if hit_score is None:
            continue
        score = float(hit_score)
        max_l2 = _get_alias_merge_max_l2()
        if score <= max_l2:
            return hit_name
        # M-7: Slightly above the L2 threshold — check if LLM aliases match the
        # existing entity name, which is a strong signal that they're the same.
        if (
            alias_lookup
            and score <= max_l2 * 1.3
            and (hit_name in alias_lookup or name.lower() in alias_lookup)
        ):
            logger.info(
                "KG merge via LLM alias match: '%s' → '%s' (l2=%.4f, threshold=%.4f)",
                name,
                hit_name,
                score,
                max_l2,
            )
            return hit_name
    return None


async def _store_entities(
    user_id: str,
    entities: list[dict],
    session_id: str | None,
    *,
    meeting_ids: list[int] | None = None,
    file_ids: list[int] | None = None,
) -> int:
    added = 0
    for ent in entities:
        try:
            name = _sanitize_entity_name(ent.get("name") or "")
        except ValueError:
            continue
        entity_type = (ent.get("type") or ent.get("entity_type") or "concept").lower().strip()
        description = (ent.get("description") or "").strip() or None
        raw_aliases = ent.get("aliases") or []
        llm_aliases: list[str] = (
            [s for s in raw_aliases if isinstance(s, str)] if isinstance(raw_aliases, list) else []
        )

        name = name.lower()
        if not name:
            continue
        if entity_type not in ENTITY_TYPES:
            from ...core.metrics import KG_UNKNOWN_TYPE_TOTAL

            KG_UNKNOWN_TYPE_TOTAL.labels(raw_type=entity_type[:32]).inc()
            logger.warning(
                "Unknown entity_type from LLM: %r → falling back to concept (entity=%s)",
                entity_type,
                name,
            )
            entity_type = "concept"

        # Look for a near-duplicate canonical entity; if found, treat the new
        # surface form as an alias of the existing canonical row.
        # _resolve_canonical does a similarity_search → embed_query, which the
        # sync embedder refuses inside a coroutine; offload it.
        canonical_name = name
        merged_aliases: list[str] = list(llm_aliases)
        existing_canonical = await asyncio.to_thread(
            _resolve_canonical,
            name=name,
            entity_type=entity_type,
            user_id=user_id,
            llm_aliases=llm_aliases,
        )
        if existing_canonical:
            canonical_name = existing_canonical
            if name not in merged_aliases:
                merged_aliases.insert(0, name)

        try:
            # M-6: Run entity upsert and pending vector deletion in a single
            # transaction so a crash between the two doesn't leave orphan state.
            # HIGH-8: When alias-merge resolves to a different canonical entity,
            # check if a row exists under the old name. If so, remap all
            # relations from the old entity to the canonical one, then delete
            # the old entity row to avoid duplicates.
            with get_write_connection() as conn:
                if existing_canonical:
                    old_row = conn.execute(
                        "SELECT id, embedding_id FROM memory_entities WHERE name=? AND user_id=?",
                        (name, user_id),
                    ).fetchone()
                    # HIGH-8: Remap relations and delete the old entity row.
                    if old_row:
                        canonical_row = conn.execute(
                            "SELECT id FROM memory_entities WHERE name=? AND user_id=?",
                            (existing_canonical, user_id),
                        ).fetchone()
                        if canonical_row and canonical_row["id"] != old_row["id"]:
                            db.reassign_entity_relations(
                                conn,
                                source_id=int(old_row["id"]),
                                target_id=int(canonical_row["id"]),
                                user_id=user_id,
                            )
                            # Queue old entity's vector for deletion (only when
                            # the entity is actually being merged away).
                            if old_row["embedding_id"]:
                                conn.execute(
                                    "INSERT OR IGNORE INTO pending_vector_deletions "
                                    "(collection, embedding_id) VALUES ('entity', ?)",
                                    (old_row["embedding_id"],),
                                )
                            db.delete_entity(conn, entity_id=int(old_row["id"]))
                            logger.info(
                                "HIGH-8: Merged entity '%s' (id=%s) into canonical "
                                "'%s' (id=%s), remapped relations",
                                name,
                                old_row["id"],
                                existing_canonical,
                                canonical_row["id"],
                            )

                entity_id = db.upsert_entity(
                    conn,
                    user_id=user_id,
                    name=canonical_name,
                    entity_type=entity_type,
                    description=description,
                    session_id=session_id,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                    aliases=merged_aliases,
                )
            # Merge persisted scope (accumulated across prior upserts) and pass
            # it to the vector store so similarity search reflects every meeting
            # the entity has been seen in. Source of truth is the
            # ``entity_scopes`` junction table (migration #32).
            merged_mids: list[int] | None = meeting_ids
            merged_fids: list[int] | None = file_ids
            try:
                from ...core.database._scopes import get_scopes

                with db.get_connection() as conn:
                    persisted_mids, persisted_fids = get_scopes(
                        conn, kind="entity", owner_id=entity_id
                    )
                merged_mids = persisted_mids or meeting_ids
                merged_fids = persisted_fids or file_ids
            except Exception:
                logger.debug(
                    "Could not re-read merged scope for entity %s", canonical_name, exc_info=True
                )

            # Read existing embedding_id so we can queue it for cleanup if
            # the Chroma upsert fails and leaves a stale vector behind (M-H3).
            old_embedding_id: str | None = None
            try:
                with db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT embedding_id FROM memory_entities WHERE id=?",
                        (entity_id,),
                    ).fetchone()
                    if row:
                        old_embedding_id = row["embedding_id"]
            except Exception:
                logger.debug(
                    "Failed to look up old embedding for entity %s",
                    entity_id,
                    exc_info=True,
                )

            # Index in vector store (best-effort). HIGH-5: On failure, queue
            # the new upsert for retry via pending_vector_deletions (reused as
            # a general pending-vector-ops table) and log a clear warning so
            # operators can spot vector/DB drift early.
            try:
                vs = get_entity_vectorstore()
                # vs.upsert calls Chroma's sync add_documents which embeds via
                # the (sync) embedder; running it inline blocks the event loop
                # AND the embedder's async-context guard refuses sync embedding
                # calls from within a coroutine. Offload per CLAUDE.md's rule
                # that Chroma indexing must always go through to_thread.
                embedding_id = await asyncio.to_thread(
                    vs.upsert,
                    entity_id,
                    user_id,
                    canonical_name,
                    entity_type,
                    description,
                    meeting_ids=merged_mids,
                    file_ids=merged_fids,
                )
                with get_write_connection() as conn:
                    conn.execute(
                        "UPDATE memory_entities SET embedding_id=? WHERE id=?",
                        (embedding_id, entity_id),
                    )
            except Exception:
                from ...core.metrics import KG_VECTOR_UPSERT_FAILED_TOTAL

                KG_VECTOR_UPSERT_FAILED_TOTAL.inc()
                logger.warning(
                    "HIGH-5: Failed to index entity '%s' (id=%s) in vector store — "
                    "SQL row is committed but vector is missing; queuing for retry",
                    canonical_name,
                    entity_id,
                    exc_info=True,
                )
                # Queue the new entity for a retry upsert so the startup
                # reconciler (reconcile_missing_entity_vectors) will pick it
                # up on the next boot.
                try:
                    with get_write_connection() as conn:
                        conn.execute(
                            "UPDATE memory_entities SET embedding_id=NULL WHERE id=?",
                            (entity_id,),
                        )
                except Exception:
                    logger.debug(
                        "Failed to clear embedding_id for entity %s",
                        entity_id,
                        exc_info=True,
                    )
                # If we had a previous vector, queue it for cleanup — the SQL
                # row now has updated content but Chroma still holds the stale
                # old vector.
                if old_embedding_id:
                    try:
                        with get_write_connection() as conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO pending_vector_deletions "
                                "(collection, embedding_id) VALUES ('entity', ?)",
                                (old_embedding_id,),
                            )
                    except Exception:
                        logger.debug(
                            "Failed to queue entity vector deletion for %s",
                            canonical_name,
                            exc_info=True,
                        )
            added += 1
        except Exception:
            logger.warning("Failed to store entity '%s'", name, exc_info=True)
    return added


def reconcile_missing_entity_vectors(user_id: str | None = None) -> int:
    """Re-index entities that have no embedding_id in the vector store.

    Called at startup or on-demand to compensate for vector store failures
    during entity creation (M-5). Returns the number of entities re-indexed.
    """
    with db.get_connection() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT id, name, entity_type, description, user_id "
                "FROM memory_entities WHERE user_id=? AND embedding_id IS NULL",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, entity_type, description, user_id "
                "FROM memory_entities WHERE embedding_id IS NULL"
            ).fetchall()

    if not rows:
        return 0

    reindexed = 0
    for row in rows:
        try:
            vs = get_entity_vectorstore()
            entity_id = row["id"]
            eid_user_id = row["user_id"]
            from ...core.database._scopes import get_scopes

            with db.get_connection() as conn:
                mids, fids = get_scopes(conn, kind="entity", owner_id=entity_id)
            embedding_id = vs.upsert(
                entity_id,
                eid_user_id,
                row["name"],
                row["entity_type"],
                row["description"],
                meeting_ids=mids,
                file_ids=fids,
            )
            with get_write_connection() as conn:
                conn.execute(
                    "UPDATE memory_entities SET embedding_id=? WHERE id=?",
                    (embedding_id, entity_id),
                )
            reindexed += 1
        except Exception:
            logger.debug(
                "Failed to re-index entity %s (%s)",
                row["name"],
                row["id"],
                exc_info=True,
            )

    if reindexed:
        logger.info("Re-indexed %d/%d entities with missing vectors", reindexed, len(rows))
    return reindexed


async def _store_relations(
    user_id: str,
    relations: list[dict],
    session_id: str | None,
    evidence_message_ids: list[int] | None = None,
    evidence_text: str | None = None,
) -> int:
    added = 0
    failures: list[str] = []
    for rel in relations:
        subject = (rel.get("subject") or "").strip().lower()
        predicate = (rel.get("predicate") or "related_to").lower().strip()
        obj = (rel.get("object") or "").strip().lower()

        if not subject or not obj:
            continue
        if predicate not in RELATION_PREDICATES:
            predicate = "related_to"
        if not _relation_is_supported({**rel, "predicate": predicate}, evidence_text):
            logger.info(
                "Skipping relation without local evidence: %s -> %s -> %s",
                subject,
                predicate,
                obj,
            )
            continue

        try:
            with db.get_connection() as conn:
                subj_row = db.get_entity_by_name(conn, user_id=user_id, name=subject)
                obj_row = db.get_entity_by_name(conn, user_id=user_id, name=obj)
            if not subj_row or not obj_row:
                continue
            if subj_row["id"] == obj_row["id"]:
                continue  # Prevent self-loops at the service layer too

            with get_write_connection() as conn:
                db.upsert_relation(
                    conn,
                    user_id=user_id,
                    subject_id=subj_row["id"],
                    predicate=predicate,
                    object_id=obj_row["id"],
                    source_session=session_id,
                    confidence=float(rel.get("confidence", 0.75)),
                    evidence_message_ids=evidence_message_ids,
                )
            added += 1
        except Exception:
            failures.append(f"{subject}:{predicate}:{obj}")
            logger.warning("Failed to store relation '%s' -> '%s'", subject, obj, exc_info=True)
    if failures:
        raise RuntimeError("failed to persist relations: " + ", ".join(failures))
    return added
