"""Shared, deterministic constraints for current and historical fact queries."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionConstraints:
    included: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    overdue: bool = False

    def matches(self, status: str | None) -> bool:
        value = status or "open"
        return (not self.included or value in self.included) and value not in self.excluded


def parse_action_constraints(query: str) -> ActionConstraints:
    # Consume negative phrases before searching positive words. In particular,
    # 未完成的 contains 完成的, and "not completed" contains "completed".
    text = query.casefold()
    unfinished = (
        r"\b(?:not\s+(?:yet\s+)?(?:completed|done)|incomplete|unfinished)\b"
        r"|尚未完成|未完成|没(?:有)?完成"
    )
    excluded_cancel = (
        r"\b(?:except|excluding|not)\s+cancel[le]*d\b"
        r"|(?:除|排除|不含|不包括)(?:已)?取消(?:的)?(?:外)?"
    )
    included: set[str] = set()
    excluded: set[str] = set()
    if re.search(excluded_cancel, text):
        excluded.add("cancelled")
        text = re.sub(excluded_cancel, " ", text)
    if re.search(unfinished, text):
        included.update(("open", "in_progress", "blocked"))
        text = re.sub(unfinished, " ", text)
    for pattern, status in (
        (r"\b(?:open|pending)\b|待处理|未开始", "open"),
        (r"\bin[ -]progress\b|进行中", "in_progress"),
        (r"\bblocked\b|受阻|阻塞", "blocked"),
    ):
        if re.search(pattern, text):
            included.add(status)
    if re.search(r"\b(?:completed|done|finished)\b|已完成|完成的", text):
        included.add("done")
    if re.search(r"\bcancel[le]*d\b|已取消|取消的", text):
        included.add("cancelled")
    overdue = bool(re.search(r"\b(?:overdue|past due)\b|逾期|已超期", text))
    if overdue:
        excluded.update(("done", "cancelled"))
    return ActionConstraints(tuple(sorted(included)), tuple(sorted(excluded)), overdue)


def memory_scope_matches(
    row_meetings: set[int],
    row_files: set[int],
    meetings: set[int],
    files: set[int],
    *,
    include_unscoped: bool = False,
) -> bool:
    if not row_meetings and not row_files:
        return include_unscoped or not (meetings or files)
    return (not meetings or bool(meetings & row_meetings)) and (
        not files or bool(files & row_files)
    )


def memory_scope_sql(
    meeting_ids: list[int] | None, file_ids: list[int] | None, *, include_unscoped: bool = False
) -> tuple[str, list[int]]:
    clauses: list[str] = []
    params: list[int] = []
    for kind, ids in (("meeting", meeting_ids), ("file", file_ids)):
        if ids:
            clauses.append(
                "EXISTS (SELECT 1 FROM memory_scopes s WHERE s.memory_id=m.id "
                f"AND s.scope_type='{kind}' AND s.scope_id IN ({','.join('?' for _ in ids)}))"
            )
            params.extend(ids)
    if not clauses:
        return "1=1", []
    predicate = "(" + " AND ".join(clauses) + ")"
    if include_unscoped:
        predicate = (
            f"({predicate} OR NOT EXISTS (SELECT 1 FROM memory_scopes s WHERE s.memory_id=m.id))"
        )
    return predicate, params
