"""Query expansion using local Qwen LLM.

Generates 2-3 alternative phrasings for a search query to improve recall,
especially for Korean↔English mixed queries.
"""

from __future__ import annotations

import json
import re


def expand_query(query_text: str, timeout: int = 30) -> list[str]:
    """Expand a single query into multiple variant phrasings via Qwen.

    Returns a list including the original query plus 2-3 expansions.
    Falls back to [query_text] on any error.
    """
    from obsitocin.qwen_client import run_qwen_prompt, is_qwen_configured

    if not is_qwen_configured():
        return [query_text]

    prompt = (
        "Given the search query below, generate 2-3 alternative phrasings "
        "that capture the same intent. Include both Korean and English variants "
        "if the query contains either language. "
        "Output a JSON array of strings only, no explanation.\n\n"
        f'Query: "{query_text}"'
    )

    try:
        raw = run_qwen_prompt(prompt, timeout=timeout)
        variants = _parse_variants(raw)
        if variants:
            return [query_text] + [v for v in variants if v != query_text]
    except Exception:
        pass

    return [query_text]


def _parse_variants(raw: str) -> list[str]:
    """Parse JSON array from LLM output, tolerating markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find JSON array
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group())
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if isinstance(v, str) and v.strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []
