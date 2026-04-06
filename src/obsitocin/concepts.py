#!/usr/bin/env python3

from collections import Counter, defaultdict
import hashlib
import re
from typing import TypedDict


class _CatalogGroup(TypedDict):
    references: list[dict]
    canonical_variants: Counter[str]
    aliases: set[str]


def _variant_preference(text: str) -> tuple[int, int, int, str]:
    cleaned = _clean_text(text)
    is_all_caps = int(cleaned.isupper())
    has_parenthetical = int("(" in cleaned or ")" in cleaned)
    return (is_all_caps, has_parenthetical, -len(cleaned), cleaned.casefold())


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip())


def strip_parenthetical(text: str) -> str:
    cleaned = _clean_text(text)
    without = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    return without or cleaned


def concept_lookup_key(text: str) -> str:
    base = strip_parenthetical(text).casefold()
    base = re.sub(r"[\[\]{}()'\"`]+", " ", base)
    base = re.sub(r"[^0-9a-zA-Z가-힣\s\-/#\+]", " ", base)
    base = re.sub(r"[\-/]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


def extract_concept_aliases(text: str) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []

    aliases: list[str] = []

    def add(value: str) -> None:
        candidate = _clean_text(value)
        if candidate and candidate not in aliases:
            aliases.append(candidate)

    add(cleaned)
    add(strip_parenthetical(cleaned))

    for inner in re.findall(r"\(([^)]*)\)", cleaned):
        for part in re.split(r"[,/]|\bor\b", inner):
            add(part)

    for part in re.split(r"[/,]", strip_parenthetical(cleaned)):
        add(part)

    return aliases


def _tokenize_key(text: str) -> set[str]:
    """Split a lookup key into tokens for fuzzy matching."""
    return set(concept_lookup_key(text).split())


def find_fuzzy_topic_match(
    candidate: str,
    existing_index: dict[str, object],
    threshold: float = 0.7,
) -> object | None:
    """Find best fuzzy match for candidate topic in existing index.

    Keys of existing_index must be concept_lookup_key values.
    Returns the value from existing_index if a match is found, None otherwise.
    Exact match is tried first (fast path).
    """
    exact_key = concept_lookup_key(candidate)
    if exact_key in existing_index:
        return existing_index[exact_key]

    candidate_tokens = _tokenize_key(candidate)
    if not candidate_tokens or len(candidate_tokens) < 2:
        return None

    best_score = 0.0
    best_key: str | None = None

    for existing_key in existing_index:
        existing_tokens = set(existing_key.split())
        if not existing_tokens or len(existing_tokens) < 2:
            continue

        intersection = candidate_tokens & existing_tokens
        if not intersection:
            continue

        containment = max(
            len(intersection) / len(candidate_tokens),
            len(intersection) / len(existing_tokens),
        )
        jaccard = len(intersection) / len(candidate_tokens | existing_tokens)
        score = max(containment, jaccard)

        if score > best_score:
            best_score = score
            best_key = existing_key

    if best_score >= threshold and best_key is not None:
        return existing_index[best_key]
    return None


def canonicalize_concepts(
    concepts: list[str], alias_to_canonical: dict[str, str]
) -> list[str]:
    canonical: list[str] = []
    for concept in concepts:
        target = alias_to_canonical.get(
            concept_lookup_key(concept), _clean_text(concept)
        )
        if target and target not in canonical:
            canonical.append(target)
    return canonical


def concept_note_stem(text: str) -> str:
    digest = hashlib.sha1(concept_lookup_key(text).encode("utf-8")).hexdigest()[:10]
    base = re.sub(r'[\\/:*?"<>|]', "", _clean_text(text)).strip() or "concept"
    return f"{base[:80]}__{digest}"


def build_concept_catalog(qa_list: list[dict]) -> dict[str, dict]:
    grouped: defaultdict[str, _CatalogGroup] = defaultdict(
        lambda: {
            "references": [],
            "canonical_variants": Counter(),
            "aliases": set(),
        }
    )

    for qa in qa_list:
        tagging = qa.get("tagging_result", {})
        for raw_concept in tagging.get("key_concepts", []):
            raw_text = _clean_text(raw_concept)
            if not raw_text:
                continue

            key = concept_lookup_key(raw_text) or raw_text.casefold()
            group = grouped[key]
            group["references"].append(qa)
            group["canonical_variants"][strip_parenthetical(raw_text)] += 1
            for alias in extract_concept_aliases(raw_text):
                group["aliases"].add(alias)

    catalog: dict[str, dict] = {}
    alias_to_canonical: dict[str, str] = {}

    for key, info in grouped.items():
        variants = info["canonical_variants"]
        canonical = sorted(
            variants.items(),
            key=lambda item: (-item[1],) + _variant_preference(item[0]),
        )[0][0]
        aliases = sorted(info["aliases"])
        catalog[canonical] = {
            "canonical": canonical,
            "lookup_key": key,
            "aliases": aliases,
            "references": info["references"],
        }
        for alias in aliases + [canonical]:
            alias_key = concept_lookup_key(alias)
            if alias_key:
                alias_to_canonical[alias_key] = canonical

    return {
        "concepts": catalog,
        "alias_to_canonical": alias_to_canonical,
    }
