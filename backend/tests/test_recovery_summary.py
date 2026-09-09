"""Regression tests for startup recovery summary state handling."""

from src.core import database as db
from src.services.processor._recovery import recover_stale_meetings


def test_recovery_does_not_treat_old_pending_summaries_as_failures():
    with db.get_write_connection() as conn:
        recent_id = db.create_meeting(conn, title="recent", user_id="default")
        first_grace_id = db.create_meeting(conn, title="first grace", user_id="default")
        second_grace_id = db.create_meeting(conn, title="second grace", user_id="default")
        conn.execute(
            "UPDATE meetings SET summary_status='pending', updated_at=datetime('now', '-25 minutes') "
            "WHERE id=?",
            (recent_id,),
        )
        conn.execute(
            "UPDATE meetings SET summary_status='pending', updated_at=datetime('now', '-45 minutes') "
            "WHERE id=?",
            (first_grace_id,),
        )
        conn.execute(
            "UPDATE meetings SET summary_status='pending', updated_at=datetime('now', '-90 minutes') "
            "WHERE id=?",
            (second_grace_id,),
        )

    recover_stale_meetings()

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, summary_status FROM meetings WHERE id IN (?, ?, ?)",
            (recent_id, first_grace_id, second_grace_id),
        ).fetchall()

    statuses = {row["id"]: row["summary_status"] for row in rows}
    assert statuses[recent_id] == "pending"
    assert statuses[first_grace_id] == "pending"
    assert statuses[second_grace_id] == "pending"
