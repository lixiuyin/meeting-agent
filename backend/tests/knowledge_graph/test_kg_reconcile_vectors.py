"""Tests for knowledge graph entity vector reconciliation."""

from unittest.mock import MagicMock, patch


class TestKgReconcileVectors:
    def test_reconcile_no_missing_vectors(self):
        """When all entities have embeddings, reconcile returns 0."""
        from src.services.knowledge_graph._storage import (
            reconcile_missing_entity_vectors,
        )

        with patch("src.services.knowledge_graph._storage.db") as mock_db:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_db.get_connection.return_value = mock_conn

            result = reconcile_missing_entity_vectors(user_id="u1")
            assert result == 0

    def test_reconcile_all_users(self):
        """Without user_id, reconcile checks all users."""
        from src.services.knowledge_graph._storage import (
            reconcile_missing_entity_vectors,
        )

        with patch("src.services.knowledge_graph._storage.db") as mock_db:
            mock_conn = MagicMock()
            mock_conn.execute.return_value.fetchall.return_value = []
            mock_conn.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_db.get_connection.return_value = mock_conn

            result = reconcile_missing_entity_vectors()
            assert result == 0

    def test_entity_types_constants(self):
        """ENTITY_TYPES should contain expected types."""
        from src.services.knowledge_graph._storage import ENTITY_TYPES

        assert "person" in ENTITY_TYPES
        assert "project" in ENTITY_TYPES
        assert "concept" in ENTITY_TYPES
        assert len(ENTITY_TYPES) == 7

    def test_relation_predicates_constants(self):
        """RELATION_PREDICATES should contain expected predicates."""
        from src.services.knowledge_graph._storage import RELATION_PREDICATES

        assert "works_on" in RELATION_PREDICATES
        assert "uses" in RELATION_PREDICATES
        assert "related_to" in RELATION_PREDICATES
