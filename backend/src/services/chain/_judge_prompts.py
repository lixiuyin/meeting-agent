"""Prompt templates for LLM-as-judge RAG evaluation."""

from __future__ import annotations

FAITHFULNESS_PROMPT = """You are an expert evaluator assessing the faithfulness of an AI-generated answer to a provided context.

INSTRUCTIONS:
- Read the CONTEXT and the ANSWER carefully.
- Determine whether every factual claim in the ANSWER is supported by the CONTEXT.
- Score from 0.0 (completely unsupported / hallucinated) to 1.0 (fully supported).
- Provide a concise justification (1-2 sentences).

RESPOND ONLY with a JSON object in this exact schema:
{{
  "score": 0.0,
  "justification": "..."
}}

CONTEXT:
{context}

ANSWER:
{answer}
"""


ANSWER_RELEVANCE_PROMPT = """You are an expert evaluator assessing how well an answer addresses a question.

INSTRUCTIONS:
- Read the QUESTION and the ANSWER.
- Determine whether the ANSWER actually addresses the QUESTION without drift or tangents.
- Score from 0.0 (does not address the question at all) to 1.0 (directly and completely addresses it).
- Provide a concise justification (1-2 sentences).

RESPOND ONLY with a JSON object in this exact schema:
{{
  "score": 0.0,
  "justification": "..."
}}

QUESTION:
{question}

ANSWER:
{answer}
"""


CONTEXT_PRECISION_PROMPT = """You are an expert evaluator assessing the relevance of retrieved context chunks to a question.

INSTRUCTIONS:
- Read the QUESTION and the ORDERED CHUNKS.
- For each chunk, determine whether it is relevant to the QUESTION.
- Compute the fraction of top-k chunks that are relevant. If all chunks are relevant, score 1.0. If none are relevant, score 0.0.
- Provide a concise justification summarizing the relevance of the chunks.

RESPOND ONLY with a JSON object in this exact schema:
{{
  "score": 0.0,
  "justification": "..."
}}

QUESTION:
{question}

ORDERED CHUNKS:
{chunks}
"""


CONTEXT_RECALL_PROMPT = """You are an expert evaluator assessing whether retrieved context chunks contain enough information to answer a question.

INSTRUCTIONS:
- Read the QUESTION, the REFERENCE ANSWER (ground truth), and the CHUNKS.
- Break the REFERENCE ANSWER into atomic factual claims.
- For each claim, determine whether the CHUNKS contain information that supports it.
- Score from 0.0 (no claims supported) to 1.0 (all claims supported).
- Provide a concise justification.

RESPOND ONLY with a JSON object in this exact schema:
{{
  "score": 0.0,
  "justification": "..."
}}

QUESTION:
{question}

REFERENCE ANSWER:
{reference_answer}

CHUNKS:
{chunks}
"""
