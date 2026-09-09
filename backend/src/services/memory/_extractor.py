"""Single-pass helpers for parsing and validating LLM-extracted memory facts."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from ...core.config import settings
from ..assertion_validation import clause_is_current_assertion, split_assertion_clauses
from ._parsers import _compute_expiry, _is_fact_supported, _parse_fact_json

logger = logging.getLogger(__name__)

_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:who|what|when|where|why|how|which|is|are|was|were|do|does|did|can|"
    r"could|would|should|will)\b|(?:谁|什么|何时|什么时候|哪里|为何|为什么|怎么|如何|是否))",
    re.IGNORECASE,
)
_QUESTION_SUFFIX_RE = re.compile(r"(?:谁|什么|何时|哪里|为什么|怎么|如何|是否|吗|呢)\s*$")


def _assertive_user_text(text: str) -> str:
    """Return user text only when it plausibly asserts, rather than asks, a fact."""
    clauses = [
        match.group(0).strip()
        for match in re.finditer(r"[^\n.!?\u3002\uff01\uff1f]+[.!?\u3002\uff01\uff1f]*", text)
        if match.group(0).strip()
    ]
    assertions = [
        clause
        for clause in clauses
        if not _QUESTION_PREFIX_RE.match(clause)
        and not _QUESTION_SUFFIX_RE.search(clause.rstrip(".!?\u3002\uff01\uff1f"))
        and not clause.endswith(("?", "\uff1f"))
    ]
    return "\n".join(assertions)


def _normalized_literal(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    substitutions = (
        (r"\benglish\b|英文|英语", " language_en "),
        (r"\bchinese\b|中文|汉语|漢語", " language_zh "),
        (r"用户|使用者|用戶", " user "),
        (r"\b(?:not (?:been )?assigned|unassigned)\b|未分配|尚未分配|无人负责", " unassigned "),
        (r"\b(?:answer|answers|response|responses)\b|回答", " response "),
        (r"\b(?:approve|approved|approval)\b|批准", " approval "),
        (r"\b(?:deployment|deployments|production changes)\b", " deployment_changes "),
        (r"\blogs\b", " log "),
        (r"\b(?:prefer|prefers|preferred|preference|preferences)\b|首选|偏好", " preference "),
    )
    for pattern, replacement in substitutions:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return "".join(char for char in normalized if char.isalnum())


def _support_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    substitutions = (
        (r"\benglish\b|英文|英语", " language_en "),
        (r"\bchinese\b|中文|汉语|漢語", " language_zh "),
        (r"用户|使用者|用戶", " user "),
        (r"\b(?:not (?:been )?assigned|unassigned)\b|未分配|尚未分配|无人负责", " unassigned "),
        (r"\b(?:answer|answers|response|responses)\b|回答", " response "),
        (r"\b(?:approve|approved|approval)\b|批准", " approval "),
        (r"\b(?:deployment|deployments|production changes)\b", " deployment_changes "),
        (r"\blogs\b", " log "),
        (r"\b(?:prefer|prefers|preferred|preference|preferences)\b|首选|偏好", " preference "),
    )
    for pattern, replacement in substitutions:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        tokens.update(run[index : index + 2] for index in range(max(1, len(run) - 1)))
    return tokens


def _literal_is_supported(value: str | None, evidence: str) -> bool:
    if not value:
        return True
    needle = _normalized_literal(value)
    if needle and needle in _normalized_literal(evidence):
        return True
    # Structured fields are normalized labels, so intervening grammatical
    # words ("not *been* assigned") and snake_case must not turn an otherwise
    # exact entity/value into a false rejection. Requiring every normalized
    # token still rejects identity swaps such as Alice -> Bob.
    value_tokens = _support_tokens(value)
    return bool(value_tokens and value_tokens.issubset(_support_tokens(evidence)))


def _predicate_is_supported(predicate: str | None, evidence: str) -> bool:
    if not predicate:
        return True
    folded = evidence.casefold()
    normalized = _normalized_literal(predicate)
    aliases = {
        "owner": r"\b(?:owner|owns|owned|responsible|assignee|assigned)\b|负责人|负责|所有者",
        "assignee": r"\b(?:assignee|assigned|responsible)\b|负责人|分配|负责",
        "deadline": r"\b(?:deadline|due|by)\b|截止|到期",
        "status": r"\b(?:status|state|is|was)\b|状态|当前|目前",
        "dependency": r"\b(?:depend|depends|dependency|blocked by)\b|依赖|取决于",
        "decision": r"\b(?:decide|decided|decision|approved|rejected)\b|决定|决策|批准|拒绝",
        "preference": r"\b(?:prefer|prefers|preferred|preference)\b|首选|偏好",
        "retention_period": r"\b(?:retain|retained|retention)\b|保留|留存",
        "prohibited_after": r"\b(?:prohibit|prohibited|forbid|forbidden)\b|禁止",
        "approval_status": r"\b(?:approve|approved|approval|withdrawn|rejected)\b|批准|撤回|拒绝",
    }
    pattern = {_normalized_literal(key): value for key, value in aliases.items()}.get(normalized)
    return (
        bool(re.search(pattern, folded, re.IGNORECASE))
        if pattern
        else _literal_is_supported(predicate, evidence)
    )


_OWNER_PREDICATES = {"owner", "owns", "assignee", "responsible", "负责人"}
_NEGATED_OWNER_RE = re.compile(
    r"\b(?:does\s+not|doesn't|did\s+not|didn't|is\s+not|isn't|no\s+longer)\s+"
    r"(?:own|owns|responsible|the\s+owner)\b|不再?负责|不是.+负责人|并非.+负责人",
    re.IGNORECASE,
)
_OWNER_RELATION_PATTERNS = (
    re.compile(
        r"\b(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+"
        r"(?:now\s+|currently\s+)?(?:owns|leads|is\s+responsible\s+for|"
        r"is\s+assigned\s+to)\s+(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+replaced\s+.+?\s+"
        r"as\s+(?:the\s+)?owner\s+of\s+(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\s+will\s+"
        r"(?:complete|deliver|prepare|review|handle|own)\s+(?:the\s+)?(?P<target>[^.;]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:the\s+)?(?P<target>[^.;]{2,80}?)\s+(?:owner|assignee)\s+"
        r"(?:is|is\s+now|will\s+be)\s+(?P<owner>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\s\uFF0C\u3002\uFF1B;])"
        r"(?P<owner>[\u3400-\u9fffA-Za-z0-9_-]{2,20})接替"
        r"[\u3400-\u9fffA-Za-z0-9_-]{2,20}负责(?P<target>[^\uFF0C\u3002\uFF1B;]+)"
    ),
    re.compile(
        r"(?:^|[\s\uFF0C\u3002\uFF1B;])"
        r"(?P<owner>[\u3400-\u9fffA-Za-z0-9_-]{2,20})(?:现在|当前)?"
        r"负责(?P<target>[^\uFF0C\u3002\uFF1B;]+)"
    ),
    re.compile(
        r"(?P<target>[^\uFF0C\u3002\uFF1B;]{2,80}?)(?:的)?负责人(?:是|改为|现在是|当前是)"
        r"(?P<owner>[\u3400-\u9fffA-Za-z0-9_-]{2,30})"
    ),
)


def _owner_relations(text: str) -> list[tuple[str, str]]:
    relations: list[tuple[str, str]] = []
    for pattern in _OWNER_RELATION_PATTERNS:
        for match in pattern.finditer(text):
            observed_owner = match.group("owner").strip()
            observed_owner = re.sub(r"^(?:that|then)\s+", "", observed_owner, flags=re.I)
            # The generic CJK ``X负责Y`` matcher can also see the entire
            # ``X接替Z`` prefix as X. The dedicated replacement matcher above
            # carries the correct new owner, so discard this ambiguous parse.
            if "接替" in observed_owner:
                continue
            relations.append((observed_owner, match.group("target").strip()))
    return relations


def _same_owner_entity(candidate: str, observed: str) -> bool:
    # A name prefix/subsequence is not proof of identity (Alex != Alexander).
    return bool(_normalized_literal(candidate)) and _normalized_literal(
        candidate
    ) == _normalized_literal(observed)


def _same_relation_entity(candidate: str, observed: str) -> bool:
    left = _normalized_literal(candidate)
    right = _normalized_literal(observed)
    return bool(left and right and (left == right or left in right or right in left))


def _is_owner_like_fact(fact: dict) -> bool:
    predicate = _normalized_literal(str(fact.get("predicate") or ""))
    key = str(fact.get("key") or "")
    return (
        predicate in {_normalized_literal(item) for item in _OWNER_PREDICATES}
        or bool(re.search(r"(?:^|[._])(?:owner|assignee)(?:$|[._])|负责人", key, re.IGNORECASE))
        or bool(_owner_relations(str(fact.get("value") or "")))
    )


def _source_supports_current_assertion(fact: dict, evidence: str) -> bool:
    """Return whether evidence states an unconditional, unattributed current fact."""
    allow_committed_future = str(fact.get("fact_type") or "") == "action_item"
    # An explicit lack of an assigned owner is itself a current state.
    absent_owner = _normalized_literal(str(fact.get("object_value") or "")) in {
        "unknown",
        "unassigned",
        "unknownunassigned",
        "unassignedunknown",
        "未知",
        "尚未确定",
    } and not fact.get("assignee")
    for clause in split_assertion_clauses(evidence):
        if not clause_is_current_assertion(
            clause,
            positive=_is_owner_like_fact(fact) and not absent_owner,
            allow_committed_future=allow_committed_future,
        ):
            continue
        if _is_owner_like_fact(fact) and _NEGATED_OWNER_RE.search(clause):
            continue
        if _structured_relation_is_supported(fact, clause):
            return True
    return False


def _structured_relation_is_supported(fact: dict, evidence: str) -> bool:
    """Validate directional owner/assignee relations against one quote.

    Token-set support is intentionally order-insensitive for normalized labels,
    but that alone accepts role reversals (``A replaced B`` vs ``B replaced
    A``).  When the quote exposes a recognizable directional relation, require
    the structured owner and target to match the same parsed relation.
    """
    if not _is_owner_like_fact(fact):
        return True

    observed_relations = _owner_relations(evidence)
    if not observed_relations:
        # Unknown syntax remains governed by the existing conservative literal
        # checks.  This avoids rejecting languages/constructions not yet parsed.
        return True

    # Prefer the human-readable assertion over model-normalized fields. Models
    # frequently emit the grammatical object (the owned task) in
    # ``object_value`` even though our schema expects the owner there. A
    # directional assertion in ``value`` is both richer and independently
    # checked against the source quote.
    candidate_relations = _owner_relations(str(fact.get("value") or ""))
    if not candidate_relations:
        assignee = str(fact.get("assignee") or "").strip()
        object_value = str(fact.get("object_value") or "").strip()
        subject = str(fact.get("subject") or "").strip()
        project_id = str(fact.get("project_id") or "").strip()
        owner = assignee or object_value
        target = subject or project_id
        candidate_relations = [(owner, target)] if owner else []
        if subject and object_value and (not assignee or _same_owner_entity(assignee, subject)):
            candidate_relations.append((subject, object_value))
    if not candidate_relations:
        return True

    matched = next(
        (
            (observed_owner, observed_target)
            for candidate_owner, candidate_target in candidate_relations
            for observed_owner, observed_target in observed_relations
            if _same_owner_entity(candidate_owner, observed_owner)
            and (not candidate_target or _same_relation_entity(candidate_target, observed_target))
        ),
        None,
    )
    if matched is None:
        return False

    assignee = str(fact.get("assignee") or "").strip()
    object_value = str(fact.get("object_value") or "").strip()
    subject = str(fact.get("subject") or "").strip()
    project_id = str(fact.get("project_id") or "").strip()
    if not any((assignee, object_value, subject, project_id)):
        return True
    observed_owner, observed_target = matched
    if not assignee and not object_value:
        # The assertion itself carries the complete direction. A lone subject
        # may use either grammatical orientation, while project_id is a scope
        # label rather than an owner/object claim.
        return not subject or any(
            _same_relation_entity(subject, entity) for entity in (observed_owner, observed_target)
        )
    canonical_owner = assignee or object_value
    canonical_target = subject or project_id
    canonical = _same_owner_entity(canonical_owner, observed_owner) and (
        not canonical_target or _same_relation_entity(canonical_target, observed_target)
    )
    # Accept only the known grammatical schema inversion produced by common
    # extraction models: subject=owner and object=owned item, with no separate
    # assignee. Arbitrary conflicting structured fields still fail closed.
    inverted = (
        (not assignee or _same_owner_entity(assignee, observed_owner))
        and bool(subject and object_value)
        and _same_owner_entity(subject, observed_owner)
        and _same_relation_entity(object_value, observed_target)
    )
    return canonical or inverted


def _sanitize_structured_fields(fact: dict, evidence: str) -> dict | None:
    """Reject relation fields that are not proven by the same evidence quote.

    ``value`` is validated separately. Identity-bearing fields fail closed;
    optional derived status/time fields are removed when the source does not
    explicitly support them.
    """
    if not _structured_relation_is_supported(fact, evidence):
        logger.debug("Rejected extracted fact %s: unsupported relation direction", fact.get("key"))
        return None
    sanitized = dict(fact)
    # Equivalent predicate spellings must pass the same domain-state admission
    # rule. This does not promote a conditional dependency to a current fact.
    predicate = str(fact.get("predicate") or "").casefold().replace(" ", "_")
    if predicate in {"depends_on", "depends", "dependency", "blocked_by"} and re.search(
        r"\b(?:depends?\s+on|dependency|blocked\s+by)\b|依赖|取决于", evidence, re.I
    ):
        sanitized["predicate"] = "dependency"
    owner_like = _is_owner_like_fact(fact)
    candidate_relations = _owner_relations(str(fact.get("value") or ""))
    if not candidate_relations:
        assignee = str(fact.get("assignee") or "").strip()
        object_value = str(fact.get("object_value") or "").strip()
        subject = str(fact.get("subject") or "").strip()
        project_id = str(fact.get("project_id") or "").strip()
        if assignee or object_value:
            candidate_relations.append((assignee or object_value, subject or project_id))
        if subject and object_value and (not assignee or _same_owner_entity(assignee, subject)):
            candidate_relations.append((subject, object_value))
    observed_relations = _owner_relations(evidence)
    if owner_like and candidate_relations and observed_relations:
        matched = next(
            (
                (observed_owner, observed_target)
                for candidate_owner, candidate_target in candidate_relations
                for observed_owner, observed_target in observed_relations
                if _same_owner_entity(candidate_owner, observed_owner)
                and (
                    not candidate_target or _same_relation_entity(candidate_target, observed_target)
                )
            ),
            None,
        )
        if matched:
            # Repair a common schema inversion without changing the grounded
            # display assertion: object/assignee is the owner; subject is the
            # owned work item.
            sanitized["subject"] = matched[1]
            sanitized["object_value"] = matched[0]
            sanitized["assignee"] = matched[0]
            sanitized["predicate"] = "owner"

    for field in ("project_id", "subject", "object_value", "assignee"):
        value = sanitized.get(field)
        if value and not _literal_is_supported(str(value).replace("_", " "), evidence):
            # These are retrieval/indexing metadata, not the assertion itself.
            # Preserve an exact quoted value as an unstructured fact while
            # removing unsupported model-derived routing fields.
            logger.debug("Sanitized extracted fact %s: unsupported %s", fact.get("key"), field)
            sanitized[field] = None
    if not _predicate_is_supported(sanitized.get("predicate"), evidence):
        # A predicate is a model-normalized relation label, not source
        # identity. Keep the grounded fact but discard an unsupported label;
        # subject/object mismatches above still fail closed.
        logger.debug("Sanitized extracted fact %s: unsupported predicate", fact.get("key"))
        sanitized["predicate"] = None
    status = sanitized.get("action_status")
    if status:
        status_patterns = {
            "open": r"\b(?:open|todo|to-do|will|needs? to|assigned)\b|待办|未完成|需要|将由",
            "in_progress": r"\b(?:in progress|underway|working on)\b|进行中|正在",
            "blocked": r"\b(?:blocked|waiting|depends on)\b|阻塞|等待|依赖",
            "done": r"\b(?:done|completed|finished|resolved)\b|已完成|完成了|已解决",
            "cancelled": r"\b(?:cancelled|canceled|withdrawn)\b|已取消|取消了|撤回",
        }
        if not re.search(status_patterns.get(str(status), r"$^"), evidence, re.IGNORECASE):
            sanitized["action_status"] = None
    due_at = sanitized.get("due_at")
    if due_at:
        date_part = str(due_at)[:10]
        year, month, day = (
            date_part.split("-") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part) else ("", "", "")
        )
        numeric_variants = {
            date_part,
            f"{year}/{int(month)}/{int(day)}" if year else "",
            f"{int(month)}/{int(day)}/{year}" if year else "",
        }
        if not any(variant and variant in evidence for variant in numeric_variants):
            sanitized["due_at"] = None
    return sanitized


@dataclass(frozen=True)
class ExtractedFact:
    key: str
    value: str
    importance: int
    category: str | None
    expires_at: str | None
    confidence: float = 0.75
    fact_type: str = "fact"
    project_id: str | None = None
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    evidence_quote: str | None = None
    action_status: str | None = None
    assignee: str | None = None
    due_at: str | None = None


def extract_facts(
    *,
    content: str,
    question: str,
    answer: str,
    max_facts: int,
    evidence_text: str | None = None,
) -> list[ExtractedFact]:
    """Parse LLM output once and return source-grounded memory candidates.

    ``evidence_text`` has three states for compatibility:
    - ``None``: legacy callers validate against the Q&A pair.
    - ``""``: production extraction validates against the user's own message
      only; the generated assistant answer is never treated as evidence.
    - non-empty: validate against the user's message plus retrieved source
      evidence, still excluding the assistant answer.
    """
    facts = _parse_fact_json(content)
    if not facts:
        return []

    candidates: list[ExtractedFact] = []
    for fact in facts[:max_facts]:
        key = (fact.get("key") or "").strip()
        value = (fact.get("value") or "").strip()
        if not key or not value:
            continue
        source_question = question if evidence_text is None else _assertive_user_text(question)
        source_answer = answer if evidence_text is None else evidence_text
        evidence_quote = (fact.get("evidence_quote") or "").strip() or None
        source = f"{source_question}\n{source_answer}"
        if (
            evidence_quote
            and " ".join(evidence_quote.split()).casefold()
            not in " ".join(source.split()).casefold()
        ):
            # A model-generated quotation is useful provenance only when it is
            # actually present in the authoritative input.
            logger.debug("Rejected extracted fact %s: non-verbatim evidence quote", key)
            continue
        validation_answer = evidence_quote or source_answer
        if not _structured_relation_is_supported(fact, validation_answer):
            logger.debug("Rejected extracted fact %s: unsupported relation direction", key)
            continue
        if evidence_quote and not _literal_is_supported(value, evidence_quote):
            # The broad support check below is intentionally tolerant enough
            # for candidate generation. Durable confirmation is stricter:
            # prefer an atomic grounded object, otherwise use the exact quote.
            fact = dict(fact)
            object_value = str(fact.get("object_value") or "").strip()
            if object_value and _literal_is_supported(object_value, evidence_quote):
                logger.debug("Sanitized extracted fact %s: using grounded object_value", key)
                fact["value"] = object_value
                value = object_value
            else:
                logger.debug("Sanitized extracted fact %s: replacing paraphrase with quote", key)
                fact["value"] = evidence_quote
                value = evidence_quote
        if not _is_fact_supported(key, value, source_question, validation_answer):
            object_value = str(fact.get("object_value") or "").strip()
            if object_value and _is_fact_supported(
                key, object_value, source_question, validation_answer
            ):
                # Models sometimes wrap a correct normalized object in an
                # unsupported explanatory gloss. Persist only the grounded
                # atomic value rather than accepting or discarding the gloss.
                logger.debug("Sanitized extracted fact %s: using grounded object_value", key)
                fact = dict(fact)
                fact["value"] = object_value
                value = object_value
            elif evidence_quote:
                # The model sometimes paraphrases ``value`` while still
                # returning a verbatim source quote. Store the authoritative
                # quote itself instead of losing a clear durable assertion.
                logger.debug("Sanitized extracted fact %s: using verbatim evidence quote", key)
                fact = dict(fact)
                fact["value"] = evidence_quote
                value = evidence_quote
            else:
                logger.debug("Rejected extracted fact %s: value not supported", key)
                continue
        supported_fact = _sanitize_structured_fields(fact, validation_answer)
        if supported_fact is None:
            continue
        fact = supported_fact
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            unit_match = re.search(
                rf"\b{re.escape(value)}\s+(?:calendar\s+|business\s+)?"
                r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
                validation_answer,
                re.IGNORECASE,
            )
            if unit_match:
                # A bare number is not a self-contained durable value. Preserve
                # the source unit and keep object_value aligned with it.
                value = unit_match.group(0)
                fact = dict(fact)
                fact["value"] = value
                fact["object_value"] = value

        importance = min(
            settings.MEMORY_MAX_IMPORTANCE,
            max(settings.MEMORY_MIN_IMPORTANCE, int(fact.get("importance", 3))),
        )
        category = fact.get("category") or None
        ttl_days = fact.get("ttl_days")
        candidates.append(
            ExtractedFact(
                key=key,
                value=value,
                importance=importance,
                category=category,
                expires_at=_compute_expiry(ttl_days),
                confidence=max(0.0, min(1.0, float(fact.get("confidence", 0.75)))),
                fact_type=fact.get("fact_type") or "fact",
                project_id=fact.get("project_id") or None,
                subject=fact.get("subject") or None,
                predicate=fact.get("predicate") or None,
                object_value=fact.get("object_value") or value,
                valid_from=fact.get("valid_from") or None,
                valid_to=fact.get("valid_to") or None,
                evidence_quote=evidence_quote,
                action_status=fact.get("action_status") or None,
                assignee=fact.get("assignee") or None,
                due_at=fact.get("due_at") or None,
            )
        )
    return candidates
