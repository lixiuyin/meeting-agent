"""Resolve bounded, explicit calendar/event phrases against owned meetings."""

import re
from datetime import UTC, date, datetime, timedelta


def resolve_meeting_time(
    conn, user_id: str, question: str, *, file_ids=None, meeting_ids=None, now=None
):
    """Return date bounds and an explanation; unknown anchors are explicit."""
    today = (now or datetime.now(UTC)).date()
    if re.search(r"上周|\blast week\b", question, re.I):
        this_monday = today - timedelta(days=today.weekday())
        return (
            this_monday - timedelta(days=7),
            this_monday - timedelta(days=1),
            "previous_calendar_week",
        )
    if re.search(r"昨天|\byesterday\b", question, re.I):
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday, "previous_calendar_day"
    match = re.search(
        r"(?:上次|最近一次)(评审|例会|会议)(之后|以来|之前)|"
        r"(after|since|before)\s+(?:the\s+)?last\s+(review|meeting)",
        question,
        re.I,
    )
    future = re.search(
        r"下次(评审|例会|会议)(?:之前|前)|before\s+(?:the\s+)?next\s+(review|meeting)",
        question,
        re.I,
    )
    if not match and not future:
        return None, None, None
    if match:
        name = match.group(1) or match.group(4)
    else:
        assert future is not None
        name = future.group(1) or future.group(2)
    clauses, params = ["m.user_id=?", "m.meeting_date IS NOT NULL"], [user_id]
    if meeting_ids is not None:
        if not meeting_ids or meeting_ids == [-1]:
            return None, None, "unresolved_meeting_anchor"
        clauses.append("m.id IN (" + ",".join("?" for _ in meeting_ids) + ")")
        params.extend(meeting_ids)
    if file_ids is not None:
        if not file_ids or file_ids == [-1]:
            return None, None, "unresolved_meeting_anchor"
        clauses.append(
            "EXISTS (SELECT 1 FROM meeting_files f WHERE f.meeting_id=m.id AND f.id IN ("
            + ",".join("?" for _ in file_ids)
            + "))"
        )
        params.extend(file_ids)
    if name in {"评审", "review"}:
        clauses.append("(instr(lower(m.title),'review')>0 OR instr(m.title,'评审')>0)")
    if name == "例会":
        clauses.append("(instr(lower(m.title),'weekly')>0 OR instr(m.title,'例会')>0)")
    clauses.append("date(m.meeting_date)" + (">?" if future else "<=?"))
    params.append(today.isoformat())
    order = "ASC" if future else "DESC"
    rows = conn.execute(
        "SELECT m.id,m.title,date(m.meeting_date) AS day FROM meetings m WHERE "
        + " AND ".join(clauses)
        + f" ORDER BY m.meeting_date {order},m.id {order} LIMIT 2",
        params,
    ).fetchall()
    if not rows or (len(rows) > 1 and rows[0]["day"] == rows[1]["day"]):
        return None, None, "unresolved_meeting_anchor"
    anchor = date.fromisoformat(rows[0]["day"])
    before = future or (match and (match.group(2) == "之前" or match.group(3) == "before"))
    # API document scopes are date-granular. Same-day order cannot be asserted.
    return (
        (None, anchor - timedelta(days=1), f"before_meeting:{rows[0]['id']}")
        if before
        else (
            anchor + timedelta(days=1),
            None,
            f"after_meeting:{rows[0]['id']}",
        )
    )
