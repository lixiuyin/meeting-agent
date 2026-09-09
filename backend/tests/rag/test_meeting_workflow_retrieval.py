from datetime import UTC, datetime

from src.core import database as db
from src.core.meeting_time import resolve_meeting_time
from src.services.rag._meeting_structure import contextual_window, meeting_units


def test_followup_context_keeps_proposal_response_and_retraction_together():
    text = "# Delivery\nAlice: Ship Friday.\nBob: Friday is impossible.\nAlice: Then Monday.\n# Budget\nBob: Ten dollars."
    start = text.index("Bob: Friday")
    end = text.index("Alice: Then")
    lo, hi = contextual_window(text, start, end)
    assert "Ship Friday" in text[lo:hi]
    assert "Then Monday" in text[lo:hi]
    assert "Ten dollars" not in text[lo:hi]
    assert meeting_units(text) == meeting_units(text)
    assert contextual_window(text, start, end, max_chars=20) == (start, end)


def test_event_time_uses_owned_meetings_and_detects_ambiguity(db_conn):
    db.create_meeting(db_conn, user_id="a", title="Design review", meeting_date="2026-01-10")
    db.create_meeting(db_conn, user_id="b", title="Private review", meeting_date="2026-01-12")
    now = datetime(2026, 1, 15, tzinfo=UTC)
    start, end, reason = resolve_meeting_time(db_conn, "a", "after last review", now=now)
    assert start.isoformat() == "2026-01-11" and end is None and reason.startswith("after_meeting:")
    start, end, _ = resolve_meeting_time(db_conn, "a", "上次评审之前", now=now)
    assert start is None and end.isoformat() == "2026-01-09"
    db.create_meeting(db_conn, user_id="a", title="Other review", meeting_date="2026-01-10")
    assert (
        resolve_meeting_time(db_conn, "a", "after last review", now=now)[2]
        == "unresolved_meeting_anchor"
    )


def test_calendar_and_empty_project_bounds(db_conn):
    now = datetime(2026, 1, 15, tzinfo=UTC)
    start, end, _ = resolve_meeting_time(db_conn, "a", "上周的会议", now=now)
    assert (str(start), str(end)) == ("2026-01-05", "2026-01-11")
    assert (
        resolve_meeting_time(db_conn, "a", "before next meeting", file_ids=[-1], now=now)[2]
        == "unresolved_meeting_anchor"
    )


def test_event_anchor_respects_selected_meeting(db_conn):
    older = db.create_meeting(db_conn, user_id="a", title="Review", meeting_date="2026-01-05")
    db.create_meeting(db_conn, user_id="a", title="Review", meeting_date="2026-01-10")
    start, _, _ = resolve_meeting_time(
        db_conn,
        "a",
        "after last review",
        meeting_ids=[older],
        now=datetime(2026, 1, 15, tzinfo=UTC),
    )
    assert str(start) == "2026-01-06"
