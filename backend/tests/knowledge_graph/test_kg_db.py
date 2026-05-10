"""Tests for knowledge graph database CRUD operations."""

from src.core import database as db
from src.core.database import get_write_connection


def _make_entity(
    user_id: str,
    name: str,
    entity_type: str = "project",
    description: str | None = None,
) -> int:
    """Helper: insert an entity and return its id."""
    with get_write_connection() as conn:
        return db.upsert_entity(
            conn,
            user_id=user_id,
            name=name,
            entity_type=entity_type,
            description=description,
        )


class TestEntityCRUD:
    def test_upsert_creates_entity(self):
        eid = _make_entity("crud_user", "Project Alpha", "project", "Main project")
        assert isinstance(eid, int)
        assert eid > 0

    def test_upsert_increments_mention_count(self):
        user_id = "mention_count_user"
        _make_entity(user_id, "Tool X", "tool")
        _make_entity(user_id, "Tool X", "tool")  # second upsert

        with db.get_connection() as conn:
            entity = db.get_entity_by_name(conn, user_id=user_id, name="Tool X")
        assert entity is not None
        assert entity["mention_count"] == 2

    def test_get_entity_by_name_returns_none_when_missing(self):
        with db.get_connection() as conn:
            result = db.get_entity_by_name(conn, user_id="ghost_user", name="NonExistent")
        assert result is None

    def test_get_entity_by_id(self):
        eid = _make_entity("id_lookup_user", "Concept X", "concept")
        with db.get_connection() as conn:
            entity = db.get_entity_by_id(conn, eid)
        assert entity is not None
        assert entity["name"] == "concept x"

    def test_list_entities_ordered_by_mention_count(self):
        user_id = "list_order_user"
        _make_entity(user_id, "Rarely Used", "tool")
        for _ in range(3):
            _make_entity(user_id, "Frequently Mentioned", "project")

        with db.get_connection() as conn:
            entities = db.list_entities(conn, user_id=user_id)
        names = [e["name"] for e in entities]
        assert names.index("frequently mentioned") < names.index("rarely used")

    def test_list_entities_filters_by_type(self):
        user_id = "type_filter_user"
        _make_entity(user_id, "Alice", "person")
        _make_entity(user_id, "ProjectX", "project")

        with db.get_connection() as conn:
            people = db.list_entities(conn, user_id=user_id, entity_type="person")
        assert all(e["entity_type"] == "person" for e in people)
        assert any(e["name"] == "alice" for e in people)

    def test_delete_entity_removes_it(self):
        user_id = "delete_entity_user"
        eid = _make_entity(user_id, "ToDelete", "concept")
        with get_write_connection() as conn:
            db.delete_entity(conn, entity_id=eid)

        with db.get_connection() as conn:
            result = db.get_entity_by_id(conn, eid)
        assert result is None


class TestRelationCRUD:
    def test_upsert_relation(self):
        user_id = "relation_user"
        aid = _make_entity(user_id, "Alice", "person")
        bid = _make_entity(user_id, "ProjectX", "project")

        with get_write_connection() as conn:
            db.upsert_relation(
                conn,
                user_id=user_id,
                subject_id=aid,
                predicate="works_on",
                object_id=bid,
            )

        with db.get_connection() as conn:
            rels = db.list_entity_relations(conn, entity_id=aid)
        assert len(rels) == 1
        assert rels[0]["predicate"] == "works_on"
        assert rels[0]["other_name"] == "projectx"

    def test_list_entity_relations_includes_incoming_and_outgoing(self):
        user_id = "bidir_user"
        aid = _make_entity(user_id, "Alice", "person")
        bid = _make_entity(user_id, "Bob", "person")
        cid = _make_entity(user_id, "ProjectY", "project")

        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=aid, predicate="works_on", object_id=cid
            )
            db.upsert_relation(
                conn, user_id=user_id, subject_id=bid, predicate="works_on", object_id=cid
            )

        with db.get_connection() as conn:
            rels = db.list_entity_relations(conn, entity_id=cid)
        directions = {r["direction"] for r in rels}
        assert "incoming" in directions

    def test_upsert_relation_is_idempotent(self):
        user_id = "idempotent_rel_user"
        aid = _make_entity(user_id, "A", "concept")
        bid = _make_entity(user_id, "B", "concept")

        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=aid, predicate="related_to", object_id=bid
            )
            db.upsert_relation(
                conn, user_id=user_id, subject_id=aid, predicate="related_to", object_id=bid
            )

        with db.get_connection() as conn:
            rels = db.list_entity_relations(conn, entity_id=aid)
        outgoing = [r for r in rels if r["direction"] == "outgoing"]
        assert len(outgoing) == 1

    def test_delete_entity_cascades_relations(self):
        user_id = "cascade_rel_user"
        aid = _make_entity(user_id, "EntityA", "concept")
        bid = _make_entity(user_id, "EntityB", "concept")
        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=aid, predicate="related_to", object_id=bid
            )
            db.delete_entity(conn, entity_id=aid)

        with db.get_connection() as conn:
            rels = db.list_entity_relations(conn, entity_id=bid)
        incoming = [r for r in rels if r["direction"] == "incoming"]
        assert len(incoming) == 0

    def test_reassign_entity_relations(self):
        user_id = "reassign_user"
        src = _make_entity(user_id, "OldEntity", "concept")
        tgt = _make_entity(user_id, "NewEntity", "concept")
        other = _make_entity(user_id, "Other", "concept")
        with get_write_connection() as conn:
            db.upsert_relation(
                conn, user_id=user_id, subject_id=src, predicate="related_to", object_id=other
            )
            db.reassign_entity_relations(conn, source_id=src, target_id=tgt, user_id=user_id)

        with db.get_connection() as conn:
            rels = db.list_entity_relations(conn, entity_id=tgt)
        assert any(r["other_name"] == "other" for r in rels)


class TestEntityRelationsLimit:
    def test_limit_is_respected(self):
        """list_entity_relations honours the limit parameter."""
        from src.core.database import get_connection

        user_id = "rel_limit_test_user"
        hub = _make_entity(user_id, "hub_entity", "concept")
        spoke_ids = [_make_entity(user_id, f"spoke_{i}", "concept") for i in range(5)]

        with get_write_connection() as conn:
            for sid in spoke_ids:
                db.upsert_relation(
                    conn,
                    user_id=user_id,
                    subject_id=hub,
                    predicate="related_to",
                    object_id=sid,
                )

        with get_connection() as conn:
            limited = db.list_entity_relations(conn, entity_id=hub, limit=3)
            unlimited = db.list_entity_relations(conn, entity_id=hub, limit=100)

        assert len(limited) == 3
        assert len(unlimited) == 5

    def test_get_entity_with_relations_uses_settings_limit(self):
        """get_entity_with_relations() passes ENTITY_RELATIONS_LIMIT to the DB query."""
        from unittest.mock import patch

        from src.services.knowledge_graph import kg_service

        user_id = "rel_limit_svc_user"
        hub = _make_entity(user_id, "hub_svc", "concept")
        spokes = [_make_entity(user_id, f"spoke_svc_{i}", "concept") for i in range(4)]

        with get_write_connection() as conn:
            for sid in spokes:
                db.upsert_relation(
                    conn,
                    user_id=user_id,
                    subject_id=hub,
                    predicate="related_to",
                    object_id=sid,
                )

        # Patch settings to a small limit
        with patch("src.services.knowledge_graph._service.settings") as mock_s:
            mock_s.ENTITY_RELATIONS_LIMIT = 2
            mock_s.KNOWLEDGE_GRAPH_ENABLED = True
            result = kg_service.get_entity_with_relations(user_id, "hub_svc")

        assert result is not None
        assert len(result["relations"]) == 2
