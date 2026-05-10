"""Knowledge Graph service - entity/relation extraction and retrieval."""

from ...core.config import settings  # noqa: F401
from ._parsing import _parse_entities_json
from ._service import KnowledgeGraphService
from ._storage import ENTITY_TYPES, RELATION_PREDICATES
from ._vectorstore import EntityVectorStore, get_entity_vectorstore

kg_service = KnowledgeGraphService()

__all__ = [
    "ENTITY_TYPES",
    "RELATION_PREDICATES",
    "EntityVectorStore",
    "KnowledgeGraphService",
    "_parse_entities_json",
    "get_entity_vectorstore",
    "kg_service",
]
