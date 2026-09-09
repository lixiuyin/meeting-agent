"""Bind explicit JSON-object requests to supported provider output constraints."""

import re
from typing import Any

_JSON_REQUEST = re.compile(
    r"(?:^|[.!?\u3002\uff01\uff1f]\s*)(?:return|output|respond\s+with)\s+(?:only\s+)?"
    r"(?:valid\s+)?(?:a\s+)?JSON\b|(?:仅|只)(?:返回|输出)\s*JSON",
    re.IGNORECASE,
)


def bind_requested_output(llm: Any, question: str) -> Any:
    """Only the direct question can select JSON mode; sources never select it.

    JSON arrays and schemas requested indirectly/ambiguously are left alone.
    Other providers retain their prompt contract rather than receiving an
    unsupported OpenAI transport option.
    """
    if not _JSON_REQUEST.search(question) or re.search(
        r"\bJSON\s+(?:array|list)\b|JSON\s*数组", question, re.IGNORECASE
    ):
        return llm
    from langchain_openai import ChatOpenAI

    base = getattr(llm, "bound", llm)
    if not isinstance(base, ChatOpenAI):
        return llm
    # A single explicitly named field is an unambiguous object contract. Leave
    # its value unconstrained: output mode must never invent a factual value or
    # coerce numbers, lists, objects, or null into strings.
    field = re.search(r'\b(?:a\s+)?single\s+"([^"\n]{1,64})"\s+field\b', question, re.I)
    if field:
        name = field[1]
        return llm.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "requested_answer",
                    "strict": False,
                    "schema": {
                        "type": "object",
                        "properties": {name: {}},
                        "required": [name],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return llm.bind(response_format={"type": "json_object"})
