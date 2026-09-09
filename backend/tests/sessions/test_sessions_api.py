"""Tests for sessions API endpoints"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def client():
    """Async test client"""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSessionsCRUD:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client, auth_headers):
        """List sessions for user with no sessions"""
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["sessions"] == []

    @pytest.mark.asyncio
    async def test_list_sessions_with_data(self, client, auth_headers):
        """List sessions returns created sessions - uses direct DB insertion"""
        from src.core import database as db
        from src.core.database import get_write_connection

        # Create a session directly in the database
        user_id = "default"
        with get_write_connection() as conn:
            _ = db.create_session(conn, user_id=user_id, title="Test Session")

        # List sessions
        async with client as c:
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["sessions"]) >= 1

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self, client, auth_headers):
        """Delete non-existent session returns 404"""
        async with client as c:
            resp = await c.delete("/api/v1/sessions/nonexistent_session_id", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_session_success(self, client, auth_headers):
        """Delete an existing session"""
        from src.core import database as db
        from src.core.database import get_write_connection

        # Create a session directly in the database
        user_id = "default"
        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id=user_id, title="Session to Delete")

        # Use single client context for both operations
        async with client as c:
            # Delete the session via API
            resp = await c.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)
            assert resp.status_code == 200

            # Verify it's deleted (same client context)
            resp = await c.get("/api/v1/sessions", headers=auth_headers)
            data = resp.json()
            session_ids = [s["id"] for s in data["sessions"]]
            assert sid not in session_ids

    @pytest.mark.asyncio
    async def test_delete_session_can_retract_derived_memories(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default", title="Derived memory")
            db.set_memory(
                conn,
                user_id="default",
                key="project.alpha.owner",
                value="Alice",
                source="auto_extracted",
            )
            conn.execute(
                "UPDATE user_memories SET session_id=? WHERE user_id='default' AND key=?",
                (sid, "project.alpha.owner"),
            )

        async with client as c:
            response = await c.delete(
                f"/api/v1/sessions/{sid}?retract_derived_memories=true",
                headers=auth_headers,
            )

        assert response.status_code == 200
        with db.get_connection() as conn:
            memory = db.get_memory_full(conn, user_id="default", key="project.alpha.owner")
            versions = db.list_memory_versions(conn, user_id="default", key="project.alpha.owner")
        assert memory is not None
        assert memory["assertion_status"] == "retracted"
        assert memory["vector_state"] == "inactive"
        assert [row["assertion_status"] for row in versions] == ["retracted", "confirmed"]

    @pytest.mark.asyncio
    async def test_delete_session_cancels_active_fact_extraction(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection
        from src.services.jobs import enqueue_durable_job

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default", title="Session to Delete")

        await enqueue_durable_job(
            kind="fact_extraction",
            dedupe_key=f"session:{sid}:turn-1",
            payload={"session_id": sid, "user_id": "default"},
        )

        async with client as c:
            response = await c.delete(f"/api/v1/sessions/{sid}", headers=auth_headers)

        assert response.status_code == 200
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM durable_jobs WHERE dedupe_key=?",
                (f"session:{sid}:turn-1",),
            ).fetchone()
        assert row["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_batch_delete_sessions_reports_missing(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            first = db.create_session(conn, user_id="default", title="Batch delete one")
            second = db.create_session(conn, user_id="default", title="Batch delete two")

        async with client as c:
            response = await c.post(
                "/api/v1/sessions/batch-delete",
                headers=auth_headers,
                json={"session_ids": [first, second, "missing-session"]},
            )

        assert response.status_code == 200
        assert response.json() == {"deleted": 2, "missing": ["missing-session"]}
        with db.get_connection() as conn:
            assert db.get_session(conn, first) is None
            assert db.get_session(conn, second) is None


class TestSessionMessages:
    @pytest.mark.asyncio
    async def test_edit_branch_copies_only_history_before_target(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            source = db.create_session(
                conn,
                user_id="default",
                title="Original",
                config_json=(
                    '{"schema_version":1,"retrieval_profile":"balanced",'
                    '"continuation_mode":"saved_snapshot"}'
                ),
            )
            db.add_turn(
                conn,
                session_id=source,
                human_content="first question",
                ai_content="first answer",
            )
            target, _ = db.add_turn(
                conn,
                session_id=source,
                human_content="message to edit",
                ai_content="answer to replace",
            )

        async with client as c:
            response = await c.post(
                f"/api/v1/sessions/{source}/branches",
                headers=auth_headers,
                json={"from_message_id": target, "reason": "edit"},
            )

        assert response.status_code == 200
        payload = response.json()
        branch = payload["session"]
        assert branch["id"] != source
        assert branch["parent_session_id"] == source
        assert branch["branched_from_message_id"] == target
        assert branch["branch_reason"] == "edit"
        assert payload["total"] == 2
        assert payload["next_before_id"] is None
        assert [item["content"] for item in payload["messages"]] == [
            "first question",
            "first answer",
        ]
        with db.get_connection() as conn:
            branched = db.get_session(conn, branch["id"], user_id="default")
            assert json.loads(branched["config_json"])["continuation_mode"] == "latest"
            assert [item["content"] for item in db.get_messages(conn, source)] == [
                "first question",
                "first answer",
                "message to edit",
                "answer to replace",
            ]

    @pytest.mark.asyncio
    async def test_branch_rejects_an_agent_message_boundary(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            source = db.create_session(conn, user_id="default")
            _, agent_id = db.add_turn(
                conn,
                session_id=source,
                human_content="question",
                ai_content="answer",
            )
        async with client as c:
            response = await c.post(
                f"/api/v1/sessions/{source}/branches",
                headers=auth_headers,
                json={"from_message_id": agent_id, "reason": "edit"},
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_messages_support_reverse_cursor_pagination(self, client, auth_headers):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")
            for index in range(5):
                db.add_message(
                    conn,
                    session_id=sid,
                    role="human" if index % 2 == 0 else "ai",
                    content=f"message-{index}",
                )

        async with client as c:
            recent = await c.get(
                f"/api/v1/sessions/{sid}/messages?limit=2",
                headers=auth_headers,
            )
            assert recent.status_code == 200
            recent_data = recent.json()
            older = await c.get(
                f"/api/v1/sessions/{sid}/messages?limit=2&before_id="
                f"{recent_data['next_before_id']}",
                headers=auth_headers,
            )

        assert [item["content"] for item in recent_data["messages"]] == [
            "message-3",
            "message-4",
        ]
        assert [item["content"] for item in older.json()["messages"]] == [
            "message-1",
            "message-2",
        ]
        assert older.json()["next_before_id"] is not None

    @pytest.mark.asyncio
    async def test_get_session_messages_not_found(self, client, auth_headers):
        """GET /sessions/{id}/messages returns 404 for unknown session."""
        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/nonexistent_session_id_xyz/messages",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_session_messages_success(self, client, auth_headers):
        """GET /sessions/{id}/messages returns messages with correct shape."""
        from langchain_core.messages import AIMessage, HumanMessage

        from src.core import database as db
        from src.core.database import get_write_connection
        from src.services.memory import get_session_history

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")

        hist = get_session_history(sid)
        hist.add_message(HumanMessage(content="hello world"))
        hist.add_message(AIMessage(content="hi there"))

        async with client as c:
            resp = await c.get(
                f"/api/v1/sessions/{sid}/messages",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "human"
        assert data["messages"][1]["role"] == "ai"

    @pytest.mark.asyncio
    async def test_session_detail_restores_versioned_retrieval_config(self, client, auth_headers):
        import json

        from src.core import database as db
        from src.core.database import get_write_connection

        config = {
            "schema_version": 1,
            "meeting_ids": [12],
            "file_ids": [34],
            "retrieval_profile": "thorough",
            "memory_mode": "focused",
        }
        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default", config_json=json.dumps(config))
            task_state = {
                "schema_version": 1,
                "objective": "List every open action item",
                "intent": "exhaustive",
                "meeting_ids": [12],
            }
            conn.execute(
                "UPDATE chat_sessions SET task_state_json=? WHERE id=?",
                (json.dumps(task_state), sid),
            )

        async with client as c:
            response = await c.get(f"/api/v1/sessions/{sid}/messages", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["task_state"] == task_state
        assert response.json()["session_config"] == config

    @pytest.mark.asyncio
    async def test_completed_latest_run_hides_older_interrupted_recovery(
        self, client, auth_headers
    ):
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")
            for run_id, status, created in (
                ("old-interrupted", "interrupted", "2026-01-01 00:00:00"),
                ("new-completed", "completed", "2026-01-02 00:00:00"),
            ):
                conn.execute(
                    "INSERT INTO chat_runs(id,user_id,request_hash,question,session_id,status,"
                    "owner,lease_expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (run_id, "default", run_id, "q", sid, status, "owner", created, created),
                )

        async with client as c:
            response = await c.get(f"/api/v1/sessions/{sid}/messages", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["pending_run"] is None


class TestSessionSummarize:
    @pytest.mark.asyncio
    async def test_summarize_not_found(self, client, auth_headers):
        """POST /sessions/{id}/summarize returns 404 for unknown session."""
        async with client as c:
            resp = await c.post(
                "/api/v1/sessions/nonexistent_sum_session/summarize",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_summarize_too_few_messages_returns_422(self, client, auth_headers):
        """POST /sessions/{id}/summarize returns 422 when session has too few messages."""
        from src.core import database as db
        from src.core.database import get_write_connection

        with get_write_connection() as conn:
            sid = db.create_session(conn, user_id="default")

        async with client as c:
            resp = await c.post(
                f"/api/v1/sessions/{sid}/summarize",
                headers=auth_headers,
            )
        assert resp.status_code == 422


class TestSessionSummaries:
    @pytest.mark.asyncio
    async def test_list_summaries_empty(self, client, auth_headers):
        """GET /sessions/summaries returns empty list for user with no summaries."""
        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/summaries",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["summaries"] == []

    @pytest.mark.asyncio
    async def test_list_summaries_limit(self, client, auth_headers):
        """GET /sessions/summaries respects limit parameter."""
        from src.core import database as db
        from src.core.database import get_write_connection

        user_id = "default"
        for i in range(3):
            with get_write_connection() as conn:
                sid = db.create_session(conn, user_id=user_id)
                db.upsert_session_summary(
                    conn,
                    session_id=sid,
                    user_id=user_id,
                    summary=f"summary {i}",
                    topics=None,
                    key_entities=None,
                    decisions=None,
                    turn_count=5,
                )

        async with client as c:
            resp = await c.get(
                "/api/v1/sessions/summaries?limit=2",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["summaries"]) == 2

    @pytest.mark.asyncio
    async def test_list_summaries_offset(self, client, auth_headers):
        """GET /sessions/summaries offset pagination works."""
        from src.core import database as db
        from src.core.database import get_write_connection

        user_id = "default"
        for i in range(3):
            with get_write_connection() as conn:
                sid = db.create_session(conn, user_id=user_id)
                db.upsert_session_summary(
                    conn,
                    session_id=sid,
                    user_id=user_id,
                    summary=f"summary {i}",
                    topics=None,
                    key_entities=None,
                    decisions=None,
                    turn_count=5,
                )

        async with client as c:
            page1 = await c.get(
                "/api/v1/sessions/summaries?limit=2&offset=0",
                headers=auth_headers,
            )
            page2 = await c.get(
                "/api/v1/sessions/summaries?limit=2&offset=2",
                headers=auth_headers,
            )
        assert page1.status_code == 200
        assert page2.status_code == 200
        p1_ids = {s["session_id"] for s in page1.json()["summaries"]}
        p2_ids = {s["session_id"] for s in page2.json()["summaries"]}
        # No overlap between pages
        assert not p1_ids.intersection(p2_ids)


class TestSessionSearch:
    @pytest.mark.asyncio
    async def test_search_sessions_empty_returns_ok(self, client, auth_headers):
        """POST /sessions/search returns 200 with empty results when nothing matches."""
        from unittest.mock import AsyncMock, patch

        with patch(
            "src.api.routers.sessions.session_summary_service.search_past_conversations",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/sessions/search",
                    headers=auth_headers,
                    json={"query": "very random xyz query", "user_id": "default"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "results" in data

    @pytest.mark.asyncio
    async def test_search_sessions_missing_query(self, client, auth_headers):
        """POST /sessions/search without query returns 422."""
        async with client as c:
            resp = await c.post(
                "/api/v1/sessions/search",
                headers=auth_headers,
                json={"user_id": "default"},
            )
        assert resp.status_code == 422
