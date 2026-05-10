"""One-off script to generate golden-set Q/A pairs via LLM.

Usage:
    uv run python -m scripts._gen_golden

The script reads benchmark fixtures, asks the LLM to generate questions,
expected chunks, and reference answers, then prints a JSON structure
that a human can review and commit to golden_set.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.services.llm import get_llm
from src.services.parser import parse

FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "benchmark"

PROMPT = """You are given a meeting document. Generate 3 diverse questions that can be answered from the text.
For each question, provide:
1. the question itself
2. the exact text snippet(s) from the document that answer it (as a list of strings)
3. a concise reference answer

RESPOND ONLY with a JSON object in this exact schema:
{{
  "items": [
    {{
      "query": "...",
      "expected_chunks": ["..."],
      "expected_answer": "..."
    }}
  ]
}}

DOCUMENT TEXT:
{text}
"""


def generate_for_fixture(fixture_name: str) -> list[dict]:
    path = FIXTURE_DIR / fixture_name
    text = parse(path)
    llm = get_llm()
    response = llm.invoke(PROMPT.format(text=text))
    content = response.content if hasattr(response, "content") else str(response)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    data = json.loads(content.strip())
    items = data.get("items", [])
    for item in items:
        item["source"] = "llm-generated"
        item["fixture_file"] = fixture_name
        item["difficulty"] = "medium"
    return items


def main() -> None:
    all_items: list[dict] = []
    for fixture in ["sample.pdf", "scanned.pdf", "sample.pptx"]:
        print(f"Generating for {fixture}...")
        items = generate_for_fixture(fixture)
        all_items.extend(items)

    output = {"version": 1, "items": all_items}
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
