"""MemoryService fact extraction mixin."""

import asyncio
import difflib
import hashlib
import inspect
import json
import re
from datetime import UTC, datetime
from typing import Protocol, cast

from ....core.metrics import (
    MEMORY_EXTRACT_DROPPED_TOTAL,
    MEMORY_EXTRACT_FACTS,
    MEMORY_EXTRACT_TOTAL,
)
from .._common import logger
from .._extractor import (
    _assertive_user_text,
    _source_supports_current_assertion,
    _structured_relation_is_supported,
    extract_facts,
)
from .._parsers import _is_semantic_duplicate
from . import settings

_EXPLICIT_REPLACEMENT_RE = re.compile(
    r"(?:\b(?:moved\s+to|changed\s+to|updated\s+to|replaced|instead|"
    r"no\s+longer|withdrawn)\b|(?:改为|更新为|替代|取代|不再|撤回))",
    re.IGNORECASE,
)
_RELATION_CURRENT_RE = re.compile(
    r"\b[\w-]+\s+(?:now|currently)\s+(?:owns|leads|prefers|is\s+responsible\s+for|is\s+assigned\s+to)\b|"
    r"[\u3400-\u9fffA-Za-z0-9_-]{2,20}(?:现在|当前)(?:负责|担任|拥有|首选|偏好)",
    re.IGNORECASE,
)


def _states_explicit_replacement(text: str) -> bool:
    """Return true only for source text that explicitly revises prior state."""
    return bool(_EXPLICIT_REPLACEMENT_RE.search(text) or _RELATION_CURRENT_RE.search(text))


def _source_event_is_older(incoming: str | None, current: object) -> bool:
    """Compare business timestamps without treating parse failures as authority."""
    if not incoming or not current:
        return False

    def _parse(value: object) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    try:
        return _parse(incoming).astimezone(UTC) < _parse(current).astimezone(UTC)
    except (TypeError, ValueError):
        return False


def _keys_have_compatible_attributes(left: str, right: str) -> bool:
    """Only fuzzy-match facts inside the same structured identity boundary."""
    left_parts = [part.casefold() for part in left.split(".") if part]
    right_parts = [part.casefold() for part in right.split(".") if part]
    left_structured = len(left_parts) >= 3
    right_structured = len(right_parts) >= 3
    if left_structured != right_structured:
        return False
    if not left_structured:
        return True
    # category/scope, entity path, and predicate are all identity-bearing.
    return (
        left_parts[0] == right_parts[0]
        and left_parts[1:-1] == right_parts[1:-1]
        and left_parts[-1] == right_parts[-1]
    )


def _slug(value: str | None) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "_", str(value or "").casefold(), flags=re.UNICODE).strip(
        "_"
    )


def _observation_field(name: str, value: object) -> str:
    if name in {"project_id", "subject", "predicate"}:
        return _slug(str(value or ""))
    # Atomic values and people's names retain punctuation; e.g. versions 1.2
    # and 1-2 are distinct even when they share the same identity slug.
    return " ".join(str(value or "").split()).casefold()


def _verbatim_value_is_supported(value: str, evidence_quote: str | None) -> bool:
    """Return true when a normalized atomic value is explicit in the quote."""
    if not evidence_quote:
        return False

    def _canonical(text: str) -> str:
        folded = text.casefold()
        substitutions = (
            (r"\b(?:not (?:been )?assigned|unassigned)\b", " unassigned "),
            (r"\b(?:approve|approved|approval)\b", " approval "),
            (r"\b(?:answer|answers|response|responses)\b|回答", " response "),
            (r"\benglish\b|英文|英语", " language_en "),
            (r"\bchinese\b|中文|汉语|漢語", " language_zh "),
        )
        for pattern, replacement in substitutions:
            folded = re.sub(pattern, replacement, folded, flags=re.IGNORECASE)
        return folded

    canonical_value = _canonical(value)
    canonical_quote = _canonical(evidence_quote)
    normalized_value = "".join(char for char in canonical_value if char.isalnum())
    normalized_quote = "".join(char for char in canonical_quote if char.isalnum())
    if normalized_value and normalized_value in normalized_quote:
        return True
    value_tokens = set(re.findall(r"[\w\u3400-\u9fff]+", canonical_value))
    quote_tokens = set(re.findall(r"[\w\u3400-\u9fff]+", canonical_quote))
    return bool(value_tokens and value_tokens.issubset(quote_tokens))


_OWNER_PATTERNS = (
    re.compile(
        r"\b[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?\s+(?:now\s+|currently\s+)?owns\s+"
        r"(?:the\s+)?(?P<target>[^.;]+)",
    ),
    re.compile(
        r"\b[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?\s+replaced\s+.+?\s+as\s+"
        r"(?:the\s+)?owner\s+of\s+(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(r"[\u3400-\u9fffA-Za-z0-9_-]{2,20}(?:现在|当前)?负责(?P<target>[^。\uff1b;]+)"),
)


def _owner_target(value: str, predicate: str | None, key: str) -> str | None:
    owner_like = _slug(predicate) in {"owner", "assignee", "responsible", "负责人"} or bool(
        re.search(r"(?:^|[._])owner(?:$|[._])|负责人", key, re.IGNORECASE)
    )
    if not owner_like and not re.search(r"\b(?:owns|owner of)\b|负责", value, re.IGNORECASE):
        return None
    for pattern in _OWNER_PATTERNS:
        match = pattern.search(value)
        if match:
            target = re.sub(
                r"\b(?:now|currently|today)\b|(?:现在|当前)$", "", match.group("target"), flags=re.I
            ).strip(" ,")
            target = re.sub(r"^(?:project|the\s+project)\s+", "", target, flags=re.I)
            return target or None
    return None


def _stable_fact_key(
    key: str,
    *,
    value: str,
    project_id: str | None,
    subject: str | None,
    predicate: str | None,
) -> str:
    """Build a stable subject/predicate identity independent of the current value."""
    parts = [part for part in key.split(".") if part]
    family = parts[0].casefold() if parts else ""
    project = _slug(project_id)
    if not project and family == "project" and len(parts) >= 3:
        project = _slug(parts[1])

    target = _owner_target(value, predicate, key)
    resolved_predicate = "owner" if target else _slug(predicate)
    # Controlled business vocabulary, including qualified model labels.
    # Keep unrecognized predicates intact rather than guessing equivalence.
    predicate_leaf = _slug(str(predicate or "").rsplit(".", 1)[-1])
    if not target and predicate_leaf in {
        "retained_for",
        "retention_period",
        "retention_duration",
        "retention_policy",
        "retention_days",
        "retention_policy_days",
        "retention_period_days",
    }:
        resolved_predicate = "retention_period"
    resolved_subject = _slug(target or subject)
    if target and project and resolved_subject:
        # Models often append the meeting/project scope to the owned item
        # ("threat model in the Security Review"). Scope belongs in
        # ``project_id`` and must not fragment the logical fact identity.
        for separator in ("_in_the_", "_in_", "_at_the_", "_at_", "_during_the_", "_during_"):
            suffix = f"{separator}{project}"
            if resolved_subject.endswith(suffix):
                resolved_subject = resolved_subject[: -len(suffix)].rstrip("_")
                break
    if target and project and resolved_subject.startswith(project + "_"):
        resolved_subject = resolved_subject[len(project) + 1 :]

    if project and resolved_predicate:
        # ``project.atlas.owner`` is the compact identity when the project
        # itself is the subject. Sub-resources retain their own subject path.
        if not resolved_subject or resolved_subject == project:
            return f"project.{project}.{resolved_predicate}"[:200]
        return f"project.{project}.{resolved_subject}.{resolved_predicate}"[:200]
    if target and resolved_subject:
        return f"topic.{resolved_subject}.owner"[:200]
    return key


def _scoped_fact_key(
    key: str,
    *,
    project_id: str | None,
    subject: str | None,
    predicate: str | None,
) -> str:
    """Backward-compatible wrapper for project-scoped structured identities."""
    return _stable_fact_key(
        key,
        value="",
        project_id=project_id,
        subject=subject,
        predicate=predicate,
    )


class _ExtractionHost(Protocol):
    def search_important(
        self,
        user_id: str,
        min_importance: int = 3,
        limit: int = 10,
    ) -> list[dict]: ...

    async def search_semantic(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        min_importance: float = 1,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
    ) -> list: ...

    async def _resolve_contradiction(
        self,
        *,
        existing_key: str,
        existing_value: str,
        new_key: str,
        new_value: str,
    ) -> str: ...

    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        *,
        source: str = "manual",
        importance: float = 3,
        expires_at: str | None = None,
        category: str | None = None,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        confidence: float | None = None,
        evidence_message_ids: list[int] | None = None,
        evidence_excerpt: str | None = None,
        evidence_refs: list[dict] | None = None,
        conflicts_with: list[str] | None = None,
        supersedes: list[str] | None = None,
        fact_type: str = "fact",
        assertion_status: str = "confirmed",
        project_id: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        action_status: str | None = None,
        assignee: str | None = None,
        due_at: str | None = None,
        expected_revision: int | None = None,
    ) -> None: ...


class _MemoryExtractionMixin:
    async def store_extracted_fact(
        self,
        user_id: str,
        *,
        key: str,
        value: str,
        importance: float,
        category: str | None,
        expires_at: str | None,
        confidence: float = 0.75,
        fact_type: str = "fact",
        project_id: str | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        evidence_quote: str | None = None,
        action_status: str | None = None,
        assignee: str | None = None,
        due_at: str | None = None,
        question: str = "",
        answer: str = "",
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        evidence_message_ids: list[int] | None = None,
        evidence_text: str | None = None,
        evidence_refs: list[dict] | None = None,
        seed_candidates: list[dict] | None = None,
        _cas_retry: bool = True,
    ) -> bool:
        """Resolve one extracted assertion against lexical and vector neighbours."""
        from ....core.untrusted_material import (
            clean_review_paragraph,
            has_embedded_directive,
            is_separate_clean_quote,
        )

        source_tainted = has_embedded_directive(evidence_text)
        if source_tainted and not is_separate_clean_quote(evidence_quote, evidence_text):
            # Store the full surrounding paragraph rather than a decontextualized
            # fragment. The review_only branch below still forbids confirmation.
            paragraph = clean_review_paragraph(evidence_quote, evidence_text)
            if paragraph is not None:
                evidence_quote = paragraph
        review_only = source_tainted and is_separate_clean_quote(evidence_quote, evidence_text)
        if has_embedded_directive(evidence_quote) or (source_tainted and not review_only):
            logger.warning("Rejected directive-bearing extraction evidence")
            return False
        from ....core.memory_admission import (
            REFERENCE_FAMILIES,
            explicitly_requested_memory,
            is_domain_state,
        )

        if (
            key.split(".", 1)[0] in REFERENCE_FAMILIES
            and fact_type == "fact"
            and not explicitly_requested_memory(question)
            and not is_domain_state(
                {
                    "key": key,
                    "predicate": predicate,
                    "project_id": project_id,
                    "assignee": assignee,
                    "action_status": action_status,
                }
            )
        ):
            return False
        if explicitly_requested_memory(question):
            category = "explicit_memory"
        host = cast(_ExtractionHost, self)
        from ....core import database as db
        from ....core.project_resolution import resolve_assertion_project

        source_project = project_id
        if fact_type != "preference" and key.split(".", 1)[0] != "profile":

            def _resolve_project():
                with db.get_connection() as conn:
                    return resolve_assertion_project(
                        conn, user_id, project_id, evidence_quote or ""
                    )

            project_id, source_project = await asyncio.to_thread(_resolve_project)
        key = _stable_fact_key(
            key,
            value=value,
            project_id=source_project,
            subject=subject,
            predicate=predicate,
        )
        if project_id and source_project and project_id != source_project:
            prefix = f"project.{_slug(source_project)}."
            if key.startswith(prefix):
                key = f"project.{_slug(project_id)}.{key[len(prefix) :]}"[:200]
        candidate_map = {
            str(candidate.get("key")): dict(candidate)
            for candidate in (seed_candidates or [])
            if candidate.get("key")
        }
        try:
            semantic = await host.search_semantic(
                user_id,
                query=f"{key}: {value}",
                limit=20,
                min_importance=settings.MEMORY_MIN_IMPORTANCE,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
            )
            for entry in semantic:
                metadata = dict(entry.metadata or {})
                candidate_map.setdefault(
                    entry.key,
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "semantic_score": entry.semantic_score,
                        "assertion_status": metadata.get("assertion_status"),
                        "project_id": metadata.get("project_id"),
                        "subject": metadata.get("subject"),
                        "predicate": metadata.get("predicate"),
                        "object_value": metadata.get("object_value"),
                        "revision": metadata.get("revision"),
                        "evidence_refs": metadata.get("evidence_refs"),
                        "evidence_message_ids": metadata.get("evidence_message_ids"),
                    },
                )
        except Exception:
            logger.debug("Semantic memory candidate lookup failed", exc_info=True)

        # Semantic recall is candidate generation, not authority. Always read
        # the canonical key from SQL so a missed vector result cannot turn an
        # update into an unchecked last-write-wins insert.
        def _load_exact() -> dict | None:
            with db.get_connection() as conn:
                return db.get_memory_full(conn, user_id=user_id, key=key)

        exact = await asyncio.to_thread(_load_exact)
        if isinstance(exact, dict):
            candidate_map[key] = exact

        candidates = list(candidate_map.values())
        for candidate in candidates:
            candidate["canonical_key"] = _stable_fact_key(
                str(candidate.get("key") or ""),
                value=str(candidate.get("value") or ""),
                project_id=candidate.get("project_id"),
                subject=candidate.get("subject"),
                predicate=candidate.get("predicate"),
            )
        key_folded = key.casefold()
        matched: dict | None = None
        for candidate in candidates:
            existing_key = str(candidate.get("key") or "")
            if (
                existing_key.casefold() == key_folded
                or str(candidate.get("canonical_key") or existing_key).casefold() == key_folded
            ):
                matched = candidate
                break
        if matched is None:
            for candidate in candidates:
                existing_key = str(candidate.get("canonical_key") or candidate.get("key") or "")
                if not _keys_have_compatible_attributes(key, existing_key):
                    continue
                lexical = difflib.SequenceMatcher(None, key_folded, existing_key.casefold()).ratio()
                if lexical >= 0.85 or _is_semantic_duplicate(key, [existing_key]):
                    matched = candidate
                    break
        compatible_candidates = [
            candidate
            for candidate in candidates
            if _keys_have_compatible_attributes(
                key, str(candidate.get("canonical_key") or candidate.get("key") or "")
            )
        ]
        if matched is None and compatible_candidates:
            nearest = max(
                compatible_candidates,
                key=lambda candidate: float(candidate.get("semantic_score") or 0.0),
            )
            if float(nearest.get("semantic_score") or 0.0) >= settings.MEMORY_DEDUP_THRESHOLD:
                matched = nearest

        # Generated assistant text is not provenance.  Store only the user's
        # explicit statement and retrieved source evidence that independently
        # grounded the accepted fact.
        evidence_excerpt = (
            evidence_quote
            or "\n".join(
                part for part in (_assertive_user_text(question), evidence_text or "") if part
            ).strip()[:2000]
        )
        conflicts_with: list[str] | None = None
        supersedes: list[str] | None = None
        key_parts = [part for part in key.split(".") if part]
        key_family = key_parts[0].lower() if key_parts else "fact"
        inferred_fact_type = {
            "profile": "preference",
            "decision": "decision",
            "todo": "action_item",
            "project": "project_fact",
        }.get(key_family, "fact")
        resolved_fact_type = fact_type if fact_type != "fact" else inferred_fact_type
        # Model confidence is not calibrated evidence. Only a value literally
        # supported by a source quote is auto-confirmed; translated or inferred
        # assertions remain pending for review.
        candidate_fact = {
            "key": key,
            "value": value,
            "fact_type": resolved_fact_type,
            "project_id": source_project,
            "subject": subject,
            "predicate": predicate,
            "object_value": object_value,
            "assignee": assignee,
        }
        relation_supported = _structured_relation_is_supported(
            candidate_fact,
            evidence_quote or "",
        )
        assertion_status = (
            "confirmed"
            if relation_supported
            and _source_supports_current_assertion(candidate_fact, evidence_quote or "")
            and (
                _verbatim_value_is_supported(value, evidence_quote)
                or _verbatim_value_is_supported(object_value or "", evidence_quote)
            )
            else "pending"
        )
        if review_only:
            assertion_status = "pending"
            if matched is not None:
                # Retain the confirmed current fact; never let suspicious-source
                # material retire it through automatic contradiction resolution.
                conflicts_with = [str(matched["key"])]
                suffix = hashlib.sha256((evidence_quote or value).encode()).hexdigest()[:10]
                key = f"{key[:175]}.__candidate__.{suffix}"
                matched = None
        if matched is not None:
            existing_key = str(matched.get("key") or "")
            existing_value = str(matched.get("value") or "")
            # Models may render the same atomic assertion as a sentence on one
            # attempt and as its object on the next. Preserve the prior display
            # when the complete structured identity/value and proven assertion
            # agree; provenance and business-time changes are still checked below.
            if (
                assertion_status == "confirmed"
                and matched.get("assertion_status") == "confirmed"
                and existing_key.casefold() == key_folded
                and object_value
                and all(
                    _observation_field(field, incoming)
                    == _observation_field(field, matched.get(field))
                    for field, incoming in (
                        ("project_id", project_id),
                        ("subject", subject),
                        ("predicate", predicate),
                        ("object_value", object_value),
                    )
                )
            ):
                value = existing_value
            if existing_value.strip().casefold() == value.strip().casefold():
                # A repeated observation is useful provenance. Re-upsert the
                # same value to merge file/meeting scopes and preserve another
                # immutable version instead of silently discarding it.
                if existing_key.casefold() == key_folded:
                    key = existing_key
                else:
                    # Migrate a legacy value-dependent identity to the stable
                    # subject/predicate key without leaving two active facts.
                    supersedes = [existing_key]
                if matched.get("assertion_status") == "confirmed":
                    assertion_status = "confirmed"
                previous_ids = matched.get("evidence_message_ids")
                if isinstance(previous_ids, str):
                    try:
                        previous_ids = json.loads(previous_ids)
                    except (TypeError, ValueError):
                        previous_ids = []
                evidence_message_ids = (
                    sorted(
                        {
                            int(item)
                            for item in [*(previous_ids or []), *(evidence_message_ids or [])]
                            if isinstance(item, int) or str(item).isdigit()
                        }
                    )
                    or None
                )
                previous_refs = matched.get("evidence_refs")
                if isinstance(previous_refs, str):
                    try:
                        previous_refs = json.loads(previous_refs)
                    except (TypeError, ValueError):
                        previous_refs = []
                unique_refs: dict[str, dict] = {}
                for ref in [*(previous_refs or []), *(evidence_refs or [])]:
                    if isinstance(ref, dict):
                        unique_refs[json.dumps(ref, sort_keys=True, ensure_ascii=False)] = ref
                evidence_refs = list(unique_refs.values()) or None
            else:
                authoritative_text = evidence_quote or evidence_text or ""
                same_identity = (
                    existing_key.casefold() == key_folded
                    or str(matched.get("canonical_key") or existing_key).casefold() == key_folded
                )
                if same_identity and _source_event_is_older(valid_from, matched.get("valid_from")):
                    # A late-arriving old meeting is useful evidence, but it
                    # cannot replace the materialized current value. Preserve
                    # it as a reviewable candidate until bitemporal backfill is
                    # explicitly resolved.
                    resolution = "contradiction"
                elif same_identity and _states_explicit_replacement(authoritative_text):
                    # Explicit source-language revision markers are stronger
                    # evidence than a second probabilistic LLM classification.
                    resolution = "update"
                else:
                    try:
                        resolution = await host._resolve_contradiction(
                            existing_key=existing_key,
                            existing_value=existing_value,
                            new_key=key,
                            new_value=value,
                        )
                    except Exception:
                        MEMORY_EXTRACT_DROPPED_TOTAL.labels(reason="contradiction_failed").inc()
                        logger.warning(
                            "Contradiction resolution failed for key %s", key, exc_info=True
                        )
                        return False
                if resolution == "contradiction":
                    conflicts_with = [existing_key]
                    assertion_status = "disputed"
                elif resolution == "complement":
                    conflicts_with = [existing_key]
                    assertion_status = "pending"
                elif existing_key.casefold() != key_folded:
                    supersedes = [existing_key]
                if resolution != "update" and same_identity:
                    suffix = hashlib.sha256(value.encode()).hexdigest()[:10]
                    key = f"{key[:175]}.__candidate__.{suffix}"

        set_kwargs = {
            "source": "auto_extracted",
            "importance": importance,
            "expires_at": (
                None
                if resolved_fact_type in {"decision", "action_item", "project_fact"}
                else expires_at
            ),
            "category": category,
            "session_id": session_id,
            "meeting_ids": meeting_ids,
            "file_ids": file_ids,
            "confidence": confidence,
            "evidence_message_ids": evidence_message_ids,
            "evidence_excerpt": evidence_excerpt or None,
            "evidence_refs": evidence_refs,
            "conflicts_with": conflicts_with,
            "supersedes": supersedes,
            "expected_revision": (
                int(matched["revision"])
                if matched is not None
                and str(matched.get("key") or "").casefold() == key.casefold()
                and matched.get("revision") is not None
                else (-1 if matched is None else None)
            ),
        }
        set_kwargs.update(
            {
                "fact_type": resolved_fact_type,
                "assertion_status": assertion_status,
                "project_id": project_id
                or (
                    key_parts[1]
                    if len(key_parts) > 1 and key_family in {"project", "decision", "todo"}
                    else None
                ),
                "subject": subject or (key_parts[1] if len(key_parts) > 1 else None),
                "predicate": predicate or (".".join(key_parts[2:]) if len(key_parts) > 2 else None),
                "object_value": object_value or value,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "action_status": action_status
                or ("open" if resolved_fact_type == "action_item" else None),
                "assignee": assignee,
                "due_at": due_at,
            }
        )
        # Preserve compatibility for lightweight protocol implementations and
        # third-party extensions compiled against the pre-provenance API.
        if matched is not None and not supersedes and not conflicts_with:

            def normalized_field(name, val):
                if name in {"subject", "predicate", "project_id", "object_value", "assignee"}:
                    return _observation_field(name, val)
                if name in {"evidence_refs", "evidence_message_ids"}:
                    if isinstance(val, str):
                        try:
                            val = json.loads(val)
                        except ValueError:
                            return val
                    return json.dumps(val or [], sort_keys=True, ensure_ascii=False)
                if name in {"file_ids", "meeting_ids"}:
                    if isinstance(val, str):
                        val = [int(v) for v in val.split(",") if v.strip()]
                    return tuple(sorted(val or []))
                return val or None

            stable_fields = (
                "fact_type",
                "assertion_status",
                "project_id",
                "subject",
                "predicate",
                "object_value",
                "valid_from",
                "valid_to",
                "action_status",
                "assignee",
                "due_at",
                "evidence_excerpt",
                "evidence_refs",
                "evidence_message_ids",
                "meeting_ids",
                "file_ids",
            )
            if (
                matched.get("key") == key
                and matched.get("value") == value
                and not matched.get("archived_at")
                and all(
                    normalized_field(name, matched.get(name))
                    == normalized_field(name, set_kwargs.get(name))
                    for name in stable_fields
                )
            ):
                # Exactly the same observation adds neither state nor provenance.
                return False
        parameters = inspect.signature(host.set).parameters
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            set_kwargs = {name: value for name, value in set_kwargs.items() if name in parameters}
        try:
            await asyncio.to_thread(host.set, user_id, key, value, **set_kwargs)
        except db.MemoryRevisionConflictError:
            if not _cas_retry:
                raise
            logger.info("Retrying extracted fact %s after a concurrent revision", key)
            # Re-run candidate loading and contradiction handling against the
            # now-authoritative row. A blind retry with a refreshed revision
            # would defeat CAS and could overwrite a genuinely newer value.
            return await self.store_extracted_fact(
                user_id,
                key=key,
                value=value,
                importance=importance,
                category=category,
                expires_at=expires_at,
                confidence=confidence,
                fact_type=fact_type,
                project_id=project_id,
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                valid_from=valid_from,
                valid_to=valid_to,
                evidence_quote=evidence_quote,
                action_status=action_status,
                assignee=assignee,
                due_at=due_at,
                question=question,
                answer=answer,
                session_id=session_id,
                meeting_ids=meeting_ids,
                file_ids=file_ids,
                evidence_message_ids=evidence_message_ids,
                evidence_text=evidence_text,
                evidence_refs=evidence_refs,
                seed_candidates=None,
                _cas_retry=False,
            )
        return True

    async def auto_extract_facts(
        self,
        user_id: str,
        question: str,
        answer: str,
        context: str | None = None,
        session_id: str | None = None,
        meeting_ids: list[int] | None = None,
        file_ids: list[int] | None = None,
        evidence_message_ids: list[int] | None = None,
        evidence_text: str | None = None,
        source_event_time: str | None = None,
        evidence_refs: list[dict] | None = None,
    ) -> int:
        """Extract key facts with importance scoring, deduplication, and TTL."""
        if not settings.MEMORY_AUTO_EXTRACT:
            MEMORY_EXTRACT_TOTAL.labels(status="skipped").inc()
            return 0
        _mode_limits = {
            "precise": 1,
            "balanced": settings.MEMORY_MAX_FACTS_PER_TURN,
            "aggressive": 5,
        }
        _max_facts = _mode_limits.get(
            settings.MEMORY_EXTRACTION_MODE, settings.MEMORY_MAX_FACTS_PER_TURN
        )
        try:
            from ...llm import (
                cached_retry_invoke,
                escape_prompt_data,
                get_extraction_llm,
                get_fact_extraction_prompt,
            )

            host = cast(_ExtractionHost, self)
            existing_ctx = ""
            existing: list[dict] = []
            if settings.MEMORY_EXTRACTION_INCLUDE_EXISTING:
                existing = await asyncio.to_thread(
                    host.search_important, user_id, min_importance=2, limit=20
                )
                if existing:
                    lines = ["User profile (existing memories):"]
                    for m in existing:
                        lines.append(f"- {m['key']}: {m['value']}")
                    existing_ctx = "\n".join(lines) + "\n\n"

            llm = get_extraction_llm()
            prompt_template = get_fact_extraction_prompt()
            prompt = prompt_template.format(
                question=escape_prompt_data(question),
                answer=escape_prompt_data(answer),
                user_context=escape_prompt_data(existing_ctx),
            )
            # H-MEM-3: Route through traffic_controller to cap LLM concurrency.
            from ...traffic_control import traffic_controller

            if traffic_controller is not None:
                async with traffic_controller:
                    response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
                    traffic_controller.record_success()
            else:
                response = await asyncio.to_thread(cached_retry_invoke, llm, prompt)
            content = response.content
            if isinstance(content, list):
                # H-13: Extract text from multi-modal content blocks instead
                # of silently discarding the response.
                text_parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(text_parts)
                if not content.strip():
                    MEMORY_EXTRACT_TOTAL.labels(status="non_text_response").inc()
                    return 0

            extract_kwargs = {
                "content": content,
                "question": question,
                "answer": answer,
                "max_facts": _max_facts,
            }
            if "evidence_text" in inspect.signature(extract_facts).parameters:
                extract_kwargs["evidence_text"] = evidence_text
            facts = extract_facts(**extract_kwargs)
            if not facts:
                MEMORY_EXTRACT_TOTAL.labels(status="success").inc()
                MEMORY_EXTRACT_FACTS.observe(0)
                return 0

            stored = 0
            for fact in facts:
                importance = min(fact.importance, settings.MEMORY_AUTO_EXTRACT_INITIAL_IMPORTANCE)
                if await self.store_extracted_fact(
                    user_id,
                    key=fact.key,
                    value=fact.value,
                    importance=importance,
                    expires_at=fact.expires_at,
                    category=fact.category,
                    confidence=fact.confidence,
                    fact_type=fact.fact_type,
                    project_id=fact.project_id,
                    subject=fact.subject,
                    predicate=fact.predicate,
                    object_value=fact.object_value,
                    valid_from=fact.valid_from or source_event_time,
                    valid_to=fact.valid_to,
                    evidence_quote=fact.evidence_quote,
                    action_status=fact.action_status,
                    assignee=fact.assignee,
                    due_at=fact.due_at,
                    question=question,
                    answer=answer,
                    session_id=session_id,
                    meeting_ids=meeting_ids,
                    file_ids=file_ids,
                    evidence_message_ids=evidence_message_ids,
                    evidence_text=evidence_text,
                    evidence_refs=evidence_refs,
                    seed_candidates=existing,
                ):
                    stored += 1
                    existing.append({"key": fact.key, "value": fact.value})

            logger.debug("Extracted facts from conversation turn")
            MEMORY_EXTRACT_TOTAL.labels(status="success").inc()
            MEMORY_EXTRACT_FACTS.observe(stored)
            return stored
        except Exception:
            logger.warning("Fact extraction failed", exc_info=True)
            MEMORY_EXTRACT_TOTAL.labels(status="error").inc()
            # Durable extraction jobs must see the failure so their retry and
            # dead-letter policy can run; swallowing it marked lost facts as a
            # successful job.
            raise
