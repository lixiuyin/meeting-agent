"""Knowledge graph JSON parsing utilities."""

import json

from pydantic import BaseModel, Field, ValidationError


class _Entity(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)


class _Relation(BaseModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)


def _parse_entities_json(content: str) -> dict | None:
    """Parse and validate LLM entity extraction output."""
    from ..llm import parse_llm_json

    try:
        data = parse_llm_json(content)
        if not isinstance(data, dict):
            return None
        raw_entities = data.get("entities") or []
        raw_relations = data.get("relations") or []
        entities = []
        for item in raw_entities:
            try:
                entities.append(_Entity(**item).model_dump())
            except (ValidationError, TypeError):
                continue
        relations = []
        for item in raw_relations:
            try:
                relations.append(_Relation(**item).model_dump())
            except (ValidationError, TypeError):
                continue
        return {"entities": entities, "relations": relations}
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None
