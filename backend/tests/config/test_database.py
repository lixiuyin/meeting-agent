"""Tests for database CRUD operations"""

from src.core import database as db


class TestMeetingCRUD:
    def test_create_meeting(self, db_conn):
        meeting_id = db.create_meeting(
            db_conn,
            title="Test Meeting",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            description="A test meeting",
            user_id="test",
        )
        assert meeting_id == 1

    def test_get_meeting(self, db_conn):
        meeting_id = db.create_meeting(
            db_conn,
            title="Test Meeting",
            file_type="video",
            file_name="test.mp4",
            file_path="/tmp/test.mp4",
            user_id="test",
        )
        m = db.get_meeting(db_conn, meeting_id)
        assert m is not None
        assert m["title"] == "Test Meeting"
        assert m["file_type"] == "video"
        assert m["status"] == "uploading"

    def test_get_meeting_not_found(self, db_conn):
        assert db.get_meeting(db_conn, 999) is None

    def test_update_meeting_status(self, db_conn):
        meeting_id = db.create_meeting(
            db_conn,
            title="Test",
            file_type="pdf",
            file_name="test.pdf",
            file_path="/tmp/test.pdf",
            user_id="test",
        )
        db.update_meeting_status(db_conn, meeting_id, "processing")
        m = db.get_meeting(db_conn, meeting_id)
        assert m["status"] == "processing"

        db.update_meeting_status(db_conn, meeting_id, "ready", transcript="Hello world")
        m = db.get_meeting(db_conn, meeting_id)
        assert m["status"] == "ready"
        assert m["transcript"] == "Hello world"

    def test_list_meetings(self, db_conn):
        for i in range(5):
            db.create_meeting(
                db_conn,
                title=f"Meeting {i}",
                file_type="pdf",
                file_name=f"test_{i}.pdf",
                file_path=f"/tmp/test_{i}.pdf",
                user_id="test",
            )
        meetings = db.list_meetings(db_conn, limit=3)
        assert len(meetings) == 3
        total = db.count_meetings(db_conn)
        assert total == 5

    def test_list_meetings_by_status(self, db_conn):
        db.create_meeting(
            db_conn,
            title="A",
            file_type="pdf",
            file_name="a.pdf",
            file_path="/a.pdf",
            user_id="test",
        )
        mid2 = db.create_meeting(
            db_conn,
            title="B",
            file_type="pdf",
            file_name="b.pdf",
            file_path="/b.pdf",
            user_id="test",
        )
        db.update_meeting_status(db_conn, mid2, "processing")
        db.update_meeting_status(db_conn, mid2, "ready")

        ready = db.list_meetings(db_conn, status="ready")
        assert len(ready) == 1
        assert ready[0]["title"] == "B"

    def test_delete_meeting(self, db_conn):
        meeting_id = db.create_meeting(
            db_conn,
            title="Delete Me",
            file_type="pdf",
            file_name="del.pdf",
            file_path="/tmp/del.pdf",
            user_id="test",
        )
        db.delete_meeting(db_conn, meeting_id)
        assert db.get_meeting(db_conn, meeting_id) is None


class TestSessionCRUD:
    def test_create_session(self, db_conn):
        sid = db.create_session(db_conn, user_id="user1", title="First session")
        assert len(sid) == 32  # uuid4 hex

    def test_get_session(self, db_conn):
        sid = db.create_session(db_conn, user_id="user1")
        session = db.get_session(db_conn, sid)
        assert session is not None
        assert session["user_id"] == "user1"

    def test_list_sessions(self, db_conn):
        for i in range(3):
            db.create_session(db_conn, user_id="user1", title=f"Session {i}")
        sessions = db.list_sessions(db_conn, user_id="user1")
        assert len(sessions) == 3

    def test_list_sessions_has_stable_tie_breaker(self, db_conn):
        db.create_session(db_conn, session_id="session-a", user_id="user1")
        db.create_session(db_conn, session_id="session-b", user_id="user1")
        db_conn.execute(
            "UPDATE chat_sessions SET updated_at='2026-09-05 00:00:00' WHERE user_id='user1'"
        )

        sessions = db.list_sessions(db_conn, user_id="user1")

        assert [session["id"] for session in sessions] == ["session-b", "session-a"]

    def test_delete_session(self, db_conn):
        sid = db.create_session(db_conn, user_id="user1")
        db.delete_session(db_conn, sid)
        assert db.get_session(db_conn, sid) is None

    def test_touch_session(self, db_conn):
        sid = db.create_session(db_conn, user_id="user1")
        original = db.get_session(db_conn, sid)
        db.touch_session(db_conn, sid)
        updated = db.get_session(db_conn, sid)
        assert updated["updated_at"] >= original["updated_at"]


class TestMessageCRUD:
    def test_add_and_get_messages(self, db_conn):
        sid = db.create_session(db_conn)
        db.add_message(db_conn, session_id=sid, role="human", content="Hello")
        db.add_message(db_conn, session_id=sid, role="ai", content="Hi there")

        messages = db.get_messages(db_conn, sid)
        # Messages come in DESC order by default (with limit)
        assert len(messages) == 2

    def test_get_messages_with_limit(self, db_conn):
        sid = db.create_session(db_conn)
        for i in range(10):
            db.add_message(db_conn, session_id=sid, role="human", content=f"Msg {i}")

        messages = db.get_messages(db_conn, sid, limit=3)
        assert len(messages) == 3

    def test_count_messages(self, db_conn):
        sid = db.create_session(db_conn)
        for i in range(5):
            db.add_message(db_conn, session_id=sid, role="human", content=f"Msg {i}")
        assert db.count_messages(db_conn, sid) == 5

    def test_clear_messages(self, db_conn):
        sid = db.create_session(db_conn)
        db.add_message(db_conn, session_id=sid, role="human", content="Hello")
        db.clear_messages(db_conn, sid)
        assert db.count_messages(db_conn, sid) == 0


class TestMemoryCRUD:
    def test_set_and_get_memory(self, db_conn):
        db.set_memory(db_conn, user_id="user1", key="name", value="Alice")
        val = db.get_memory(db_conn, user_id="user1", key="name")
        assert val == "Alice"

    def test_get_memory_not_found(self, db_conn):
        assert db.get_memory(db_conn, user_id="user1", key="nonexistent") is None

    def test_set_memory_upsert(self, db_conn):
        db.set_memory(db_conn, user_id="user1", key="color", value="blue")
        db.set_memory(db_conn, user_id="user1", key="color", value="red")
        val = db.get_memory(db_conn, user_id="user1", key="color")
        assert val == "red"

    def test_list_memories(self, db_conn):
        db.set_memory(db_conn, user_id="user1", key="k1", value="v1")
        db.set_memory(db_conn, user_id="user1", key="k2", value="v2")
        memories = db.list_memories(db_conn, user_id="user1")
        assert len(memories) == 2

    def test_list_memories_has_stable_tie_breaker(self, db_conn):
        db.set_memory(db_conn, user_id="user1", key="k2", value="v2")
        db.set_memory(db_conn, user_id="user1", key="k1", value="v1")
        db_conn.execute(
            "UPDATE user_memories SET salience=0.5, updated_at='2026-09-05 00:00:00' "
            "WHERE user_id='user1'"
        )

        memories = db.list_memories(db_conn, user_id="user1")

        assert [memory["key"] for memory in memories] == ["k1", "k2"]

    def test_delete_memory(self, db_conn):
        db.set_memory(db_conn, user_id="user1", key="k1", value="v1")
        db.delete_memory(db_conn, user_id="user1", key="k1")
        assert db.get_memory(db_conn, user_id="user1", key="k1") is None
