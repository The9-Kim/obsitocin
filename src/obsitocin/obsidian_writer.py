#!/usr/bin/env python3
"""Obsidian note writer: converts processed Q&A pairs to knowledge graph notes.

Writes markdown files directly to the Obsidian vault directory.
Creates session notes, daily indexes, concept notes, and a Map of Content (MOC).
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from obsitocin.config import (
    CONCEPTS_DIR,
    DAILY_DIR,
    OBS_DIR,
    LOGS_DIR,
    MOC_PATH,
    PARA_AREAS_DIR,
    PARA_ARCHIVES_DIR,
    PARA_PROJECTS_DIR,
    PARA_RESOURCES_DIR,
    PROCESSED_DIR,
    PROFILE_PATH,
    SESSIONS_DIR,
)
from obsitocin.concepts import (
    build_concept_catalog,
    canonicalize_concepts,
    concept_note_stem,
    concept_lookup_key,
)
from obsitocin.memory_relations import (
    build_version_history,
    classify_concept_relations,
)

LOG_FILE = LOGS_DIR / "obsidian_writer.log"
USER_NOTES_START = "<!-- OBSITOCIN:BEGIN USER NOTES -->"
USER_NOTES_END = "<!-- OBSITOCIN:END USER NOTES -->"
CURATED_MAPS_START = "<!-- OBSITOCIN:BEGIN CURATED MAPS -->"
CURATED_MAPS_END = "<!-- OBSITOCIN:END CURATED MAPS -->"
_EMBEDDINGS_CACHE: dict | None = None


class _UnionFind:
    def __init__(self):
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        for x in self._parent:
            result[self.find(x)].append(x)
        return {k: v for k, v in result.items() if len(v) > 1}


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = name.strip()
    return name[:100] if name else "untitled"


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def get_processed_files() -> list[Path]:
    """Get all processed Q&A pair files."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(PROCESSED_DIR.glob("*.json"))


# ── Step 1: Session filename helper ──


def session_filename(qa: dict) -> str:
    """Generate a session note filename from Q&A data."""
    tagging = qa.get("tagging_result", {})
    title = tagging.get("title", "Untitled")
    session_id = str(qa.get("session_id", "")).strip()
    ts = qa.get("timestamp", "")
    try:
        date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")
    suffix_source = session_id or f"{ts}:{title}"
    suffix = hashlib.sha1(suffix_source.encode("utf-8")).hexdigest()[:8]
    return f"{date_str}_{sanitize_filename(title)}_{suffix}"


def note_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _concepts_for(qa: dict) -> list[str]:
    tagging = qa.get("tagging_result", {})
    return tagging.get("canonical_concepts") or tagging.get("key_concepts", [])


def session_relative_path(qa: dict) -> str:
    project_name = _extract_project_name(qa.get("cwd", ""))
    return f"00-projects/{project_name}/{session_filename(qa)}"


def concept_relative_path(concept: str) -> str:
    return f"20-resources/concepts/{concept_note_stem(concept)}"


def daily_relative_path(date_str: str) -> str:
    return f"30-archives/daily/{date_str}"


def concept_display_label_from_path(target: str) -> str:
    return Path(target).name.split("__", 1)[0]


def make_wikilink(target: str, label: str | None = None) -> str:
    if label and label != target:
        return f"[[{target}|{label}]]"
    return f"[[{target}]]"


def extract_preserved_block(
    text: str, start_marker: str, end_marker: str, default: str = ""
) -> str:
    if not text:
        return default
    pattern = re.escape(start_marker) + r"\n?(.*?)\n?" + re.escape(end_marker)
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return default
    return match.group(1).strip("\n")


def render_preserved_block(
    title: str, start_marker: str, end_marker: str, body: str
) -> str:
    inner = body.strip("\n")
    return f"\n## {title}\n\n{start_marker}\n{inner}\n{end_marker}\n"


def extract_created_date(existing_text: str) -> str:
    created_match = re.search(r"created: (\S+)", existing_text)
    if created_match:
        return created_match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def earliest_reference_date(references: list[dict]) -> str:
    dates: list[str] = []
    for ref in references:
        ts = str(ref.get("timestamp", "")).strip()
        if not ts:
            continue
        try:
            dates.append(datetime.fromisoformat(ts).strftime("%Y-%m-%d"))
        except (ValueError, TypeError):
            continue
    return min(dates) if dates else datetime.now().strftime("%Y-%m-%d")


def build_concept_draft(concept: str, references: list[dict]) -> str:
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for ref in references:
        tagging = ref.get("tagging_result", {})
        summary = str(tagging.get("summary", "")).strip()
        if not summary or summary in seen:
            continue
        seen.add(summary)
        score = int(tagging.get("importance", 3))
        if tagging.get("memory_type") == "static":
            score += 2
        if concept in summary:
            score += 2
        ranked.append((score, summary))

    top_summaries = [summary for _score, summary in sorted(ranked, reverse=True)[:3]]
    if not top_summaries:
        return f"{concept}에 대한 충분한 요약이 아직 없습니다. 아래 소스 대화를 바탕으로 내용을 보강하세요."
    return "\n\n".join(top_summaries)


def build_takeaways(references: list[dict]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    ranked_refs = sorted(
        references,
        key=lambda ref: (
            ref.get("tagging_result", {}).get("memory_type") == "static",
            ref.get("tagging_result", {}).get("importance", 3),
            ref.get("timestamp", ""),
        ),
        reverse=True,
    )
    for ref in ranked_refs:
        tagging = ref.get("tagging_result", {})
        for candidate in (tagging.get("summary", ""), tagging.get("title", "")):
            text = str(candidate).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lines.append(f"- {truncate(text, 120)}")
            if len(lines) >= 5:
                return "\n".join(lines)
    if lines:
        return "\n".join(lines)
    return "- 아직 추출된 핵심 포인트가 없습니다."


# ── Step 2: Weighted similarity ──


def _load_embeddings_index() -> dict:
    """Load embeddings index for similarity enhancement (cached per session)."""
    global _EMBEDDINGS_CACHE
    if _EMBEDDINGS_CACHE is None:
        try:
            from obsitocin.embeddings import load_index

            _EMBEDDINGS_CACHE = load_index()
        except Exception:
            _EMBEDDINGS_CACHE = {}
    return _EMBEDDINGS_CACHE


def compute_similarity(qa1: dict, qa2: dict) -> tuple[float, list[str]]:
    """Compute weighted similarity between two Q&A pairs.

    Returns (score, list of reason strings).
    Uses tag/concept overlap + optional embedding cosine similarity.
    """
    tagging1 = qa1.get("tagging_result", {})
    tagging2 = qa2.get("tagging_result", {})

    score = 0.0
    reasons: list[str] = []

    # Shared tags: 0.2 per tag, capped at 0.8
    tags1 = set(tagging1.get("tags", []))
    tags2 = set(tagging2.get("tags", []))
    shared_tags = tags1 & tags2
    if shared_tags:
        tag_score = min(len(shared_tags) * 0.2, 0.8)
        score += tag_score
        reasons.append(f"shared tags: {', '.join(sorted(shared_tags))}")

    # Shared concepts: 0.4 per concept, capped at 1.2
    concepts1 = set(_concepts_for(qa1))
    concepts2 = set(_concepts_for(qa2))
    shared_concepts = concepts1 & concepts2
    if shared_concepts:
        concept_score = min(len(shared_concepts) * 0.4, 1.2)
        score += concept_score
        reasons.append(f"shared concepts: {', '.join(sorted(shared_concepts))}")

    # Same category: 0.15
    cat1 = tagging1.get("category", "")
    cat2 = tagging2.get("category", "")
    if cat1 and cat2 and cat1 == cat2:
        score += 0.15
        reasons.append(f"same category: {cat1}")

    # Same cwd (project): 0.2
    cwd1 = qa1.get("cwd", "").replace("\\", "/")
    cwd2 = qa2.get("cwd", "").replace("\\", "/")
    if cwd1 and cwd2 and cwd1 == cwd2:
        score += 0.2
        reasons.append("same project")

    # Embedding cosine similarity: weighted 0.5, capped at 0.5
    embed_index = _load_embeddings_index()
    entries = embed_index.get("entries", {})
    if entries:
        # Find embeddings by session_id matching
        id1 = qa1.get("session_id", "")
        id2 = qa2.get("session_id", "")
        emb1 = None
        emb2 = None
        for file_id, entry in entries.items():
            if id1 and id1 in file_id:
                emb1 = entry.get("embedding")
            if id2 and id2 in file_id:
                emb2 = entry.get("embedding")
        if emb1 and emb2:
            from obsitocin.embeddings import cosine_similarity as cos_sim

            cos = cos_sim(emb1, emb2)
            embed_score = min(cos * 0.5, 0.5)
            if embed_score > 0.1:
                score += embed_score
                reasons.append(f"semantic: {cos:.2f}")

    return score, reasons


# ── Step 3: Build session relations ──


def build_session_relations(
    qa_list: list[dict],
) -> dict[str, list[tuple[str, str, float, list[str]]]]:
    if len(qa_list) < 2:
        return {}

    session_keys = [str(qa.get("session_id", session_filename(qa))) for qa in qa_list]

    # Compute all pairwise similarities
    pairs: dict[str, list[tuple[str, str, float, list[str]]]] = defaultdict(list)
    for i in range(len(qa_list)):
        for j in range(i + 1, len(qa_list)):
            score, reasons = compute_similarity(qa_list[i], qa_list[j])
            if score >= 0.6:
                pairs[session_keys[i]].append(
                    (
                        session_relative_path(qa_list[j]),
                        qa_list[j]
                        .get("tagging_result", {})
                        .get("title", session_filename(qa_list[j])),
                        score,
                        reasons,
                    )
                )
                pairs[session_keys[j]].append(
                    (
                        session_relative_path(qa_list[i]),
                        qa_list[i]
                        .get("tagging_result", {})
                        .get("title", session_filename(qa_list[i])),
                        score,
                        reasons,
                    )
                )

    # Apply tiered cap: strong (>=0.8) unlimited, moderate (0.6-0.8) max 5
    result: dict[str, list[tuple[str, str, float, list[str]]]] = {}
    for session_key, matches in pairs.items():
        strong = [(path, title, s, r) for path, title, s, r in matches if s >= 0.8]
        moderate = sorted(
            [(path, title, s, r) for path, title, s, r in matches if s < 0.8],
            key=lambda x: x[2],
            reverse=True,
        )[:5]
        result[session_key] = sorted(
            strong + moderate, key=lambda x: x[2], reverse=True
        )

    return result


def group_issue_threads(qa_list: list[dict]) -> list[list[dict]]:
    if len(qa_list) < 2:
        return []

    uf = _UnionFind()
    fnames = [session_filename(qa) for qa in qa_list]

    for i in range(len(qa_list)):
        for j in range(i + 1, len(qa_list)):
            if _extract_project_name(
                qa_list[i].get("cwd", "")
            ) != _extract_project_name(qa_list[j].get("cwd", "")):
                continue
            score, _ = compute_similarity(qa_list[i], qa_list[j])
            if score >= 0.8:
                uf.union(fnames[i], fnames[j])

    groups = uf.groups()
    if not groups:
        return []

    fname_to_qa = {session_filename(qa): qa for qa in qa_list}
    threads = []
    for members in groups.values():
        thread = [fname_to_qa[m] for m in sorted(members)]
        threads.append(thread)

    return threads


def write_issue_thread(
    thread: list[dict], thread_index: int, project_name: str
) -> Path:
    if PARA_PROJECTS_DIR is None:
        raise RuntimeError("Vault directory not configured.")
    project_dir = PARA_PROJECTS_DIR / project_name / "threads"
    project_dir.mkdir(parents=True, exist_ok=True)

    first_qa = thread[0]
    tagging = first_qa.get("tagging_result", {})
    title = tagging.get("title", "Untitled")
    ts = first_qa.get("timestamp", "")
    try:
        date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")

    concepts = set()
    tags = set()
    for qa in thread:
        q = qa.get("tagging_result", {})
        concepts.update(_concepts_for(qa))
        tags.update(q.get("tags", []))

    thread_file = (
        project_dir
        / f"{date_str}_thread-{thread_index:02d}_{sanitize_filename(title)}.md"
    )

    entries = []
    for qa in thread:
        q = qa.get("tagging_result", {})
        t = qa.get("timestamp", "")
        try:
            time_str = datetime.fromisoformat(t).strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = "??:??"
        entries.append(
            f"### {time_str} — {make_wikilink(session_relative_path(qa), q.get('title', ''))}\n\n"
            f"**{q.get('title', '')}**\n\n"
            f"{q.get('summary', '')}"
        )

    concepts_str = ", ".join(
        make_wikilink(concept_relative_path(c), c) for c in sorted(concepts)
    )
    tags_str = " ".join(f"#{t}" for t in sorted(tags))

    content = f"""---
title: "이슈 스레드: {title}"
date: {date_str}
type: issue-thread
sessions: {len(thread)}
tags:
{chr(10).join(f"  - {t}" for t in sorted(tags))}
---

# 이슈 스레드: {title}

**관련 세션**: {len(thread)}개
**태그**: {tags_str}
**관련 개념**: {concepts_str}

---

## 타임라인

{chr(10) + chr(10).join(entries)}
"""
    thread_file.write_text(content)
    return thread_file


# ── Step 4: Write session note ──


def _extract_project_name(cwd: str) -> str:
    if not cwd:
        return "uncategorized"
    return Path(cwd).name or "uncategorized"


def write_session_note(
    qa: dict, see_also: list[tuple[str, str, float, list[str]]]
) -> Path:
    if PARA_PROJECTS_DIR is None:
        raise RuntimeError("Vault directory not configured.")
    cwd = qa.get("cwd", "").replace("\\", "/")
    project_name = _extract_project_name(cwd)
    project_dir = PARA_PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    fname = session_filename(qa)
    session_file = project_dir / f"{fname}.md"

    tagging = qa.get("tagging_result", {})
    title = tagging.get("title", "Untitled")
    summary = tagging.get("summary", "")
    tags = tagging.get("tags", [])
    category = tagging.get("category", "other")
    key_concepts = _concepts_for(qa)
    prompt = qa.get("prompt", "")
    response = qa.get("response", "")
    session_id = qa.get("session_id", "")
    cwd = qa.get("cwd", "").replace("\\", "/")

    ts = qa.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")
        time_str = "00:00"

    tags_yaml = "\n".join(f"  - {t}" for t in tags) if tags else "  - untagged"
    tags_inline = " ".join(f"#{t}" for t in tags) if tags else ""
    concepts_str = (
        ", ".join(make_wikilink(concept_relative_path(c), c) for c in key_concepts)
        if key_concepts
        else ""
    )

    # Tool summary data
    tool_summary = qa.get("tool_summary", {})
    files_modified = tool_summary.get("files_modified", [])
    commands_executed = tool_summary.get("commands_executed", [])
    tool_counts = tool_summary.get("tool_counts", {})
    total_tools = sum(tool_counts.values()) if tool_counts else 0

    # Frontmatter extras for tool summary
    tool_frontmatter = ""
    if tool_summary:
        tool_frontmatter = (
            f"\nfiles_modified: {len(files_modified)}\ntools_used: {total_tools}"
        )

    # See Also section
    see_also_lines = ""
    if see_also:
        lines = []
        for other_path, other_title, _score, reasons in see_also:
            reason_str = " — " + "; ".join(reasons) if reasons else ""
            lines.append(f"- {make_wikilink(other_path, other_title)}{reason_str}")
        see_also_lines = "\n## See Also\n\n" + "\n".join(lines) + "\n"

    # Session Activity section
    activity_section = ""
    if tool_summary:
        activity_parts = []
        if files_modified:
            file_lines = "\n".join(f"  - `{f}`" for f in files_modified)
            activity_parts.append(
                f"### Files Modified ({len(files_modified)})\n{file_lines}"
            )
        if commands_executed:
            cmd_lines = "\n".join(f"  - `{c}`" for c in commands_executed)
            activity_parts.append(
                f"### Commands Executed ({len(commands_executed)})\n{cmd_lines}"
            )
        if tool_counts:
            counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(tool_counts.items()))
            activity_parts.append(f"**Tool Usage** ({total_tools} total): {counts_str}")
        if activity_parts:
            activity_section = (
                "\n## Session Activity\n\n" + "\n\n".join(activity_parts) + "\n"
            )

    # Memory type and importance
    memory_type = tagging.get("memory_type", "dynamic")
    importance = tagging.get("importance", 3)
    memory_frontmatter = f"\nmemory_type: {memory_type}\nimportance: {importance}"

    # Memory type callout
    if memory_type == "static":
        memory_callout = f"\n> [!info] Static Memory (Importance: {importance}/5)\n"
    else:
        memory_callout = (
            f"\n> [!abstract] Dynamic Memory (Importance: {importance}/5)\n"
        )

    content = f"""---
title: "{title}"
date: {date_str}
time: "{time_str}"
session_id: {session_id}
note_id: {note_id("session", session_id or fname)}
category: {category}
tags:
{tags_yaml}
type: source-note
zettel_stage: source
memory_type: {memory_type}
importance: {importance}
cwd: "{cwd}"{tool_frontmatter}
---

# {title}
{memory_callout}
**Summary**: {summary}

**Category**: {category}

**Tags**: {tags_inline}

**Key Concepts**: {concepts_str}

**Project**: `{cwd}`
{activity_section}{see_also_lines}
## Conversation

> [!question] Prompt
> {prompt.replace(chr(10), chr(10) + "> ")}

> [!quote] Response
> {response.replace(chr(10), chr(10) + "> ")}
"""
    session_file.write_text(content)
    return session_file


# ── Step 5: Daily entry (index line) ──


def build_daily_entry(qa: dict) -> str:
    """Build a one-line index entry linking to the session note."""
    tagging = qa.get("tagging_result", {})
    tags = tagging.get("tags", [])
    category = tagging.get("category", "other")
    title = tagging.get("title", session_filename(qa))

    ts = qa.get("timestamp", "")
    try:
        time_str = datetime.fromisoformat(ts).strftime("%H:%M")
    except (ValueError, TypeError):
        time_str = "00:00"

    tags_preview = " ".join(f"#{t}" for t in tags[:3]) if tags else ""

    return (
        f"- [{time_str}] {make_wikilink(session_relative_path(qa), title)} "
        f"— {category}, {tags_preview}"
    )


# ── Step 6: Daily note with "Today's Concepts" ──


def write_daily_note(
    date_str: str, entries: list[str], day_concepts: list[str] | None = None
) -> Path:
    if DAILY_DIR is None:
        raise RuntimeError("Vault directory not configured.")
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    daily_file = DAILY_DIR / f"{date_str}.md"

    entries_block = "\n".join(entries)

    concepts_section = ""
    if day_concepts:
        unique = sorted(set(day_concepts))
        concepts_links = ", ".join(
            make_wikilink(concept_relative_path(c), c) for c in unique
        )
        concepts_section = f"\n## Today's Concepts\n\n{concepts_links}\n"

    frontmatter = f"""---
title: {date_str} AI Conversation Log
date: {date_str}
tags:
  - ai-log
  - obsitocin
type: daily-ai-log
zettel_stage: index
---

# {date_str} AI Conversation Log

"""
    daily_file.write_text(frontmatter + entries_block + "\n" + concepts_section)

    return daily_file


# ── Step 7: Concept relations with metadata ──


def build_concept_relations(
    concept_refs: dict[str, list[dict]],
) -> dict[str, dict[str, dict]]:
    """Build concept-to-concept relations with co-occurrence counts and shared tags.

    Returns {concept: {related_concept: {"co_occurred": int, "shared_tags": set}}}.
    """
    cooccurrence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # Co-occurrence: concepts appearing in the same Q&A pair
    seen_pairs: dict[str, list[str]] = defaultdict(list)
    for concept, refs in concept_refs.items():
        for ref in refs:
            pair_id = f"{ref.get('session_id', '')}_{ref.get('timestamp', '')}"
            seen_pairs[pair_id].append(concept)

    for pair_id, concepts in seen_pairs.items():
        for i, c1 in enumerate(concepts):
            for c2 in concepts[i + 1 :]:
                cooccurrence[c1][c2] += 1
                cooccurrence[c2][c1] += 1

    # Collect tags per concept
    concept_tags: dict[str, set[str]] = {}
    for concept, refs in concept_refs.items():
        tags: set[str] = set()
        for ref in refs:
            tagging = ref.get("tagging_result", {})
            tags.update(tagging.get("tags", []))
        concept_tags[concept] = tags

    # Build final relations dict with metadata
    all_concepts = list(concept_refs.keys())
    relations: dict[str, dict[str, dict]] = defaultdict(dict)

    for i, c1 in enumerate(all_concepts):
        for c2 in all_concepts[i + 1 :]:
            co_count = cooccurrence.get(c1, {}).get(c2, 0)
            shared = concept_tags.get(c1, set()) & concept_tags.get(c2, set())

            # Include if co-occurred or share 2+ tags
            if co_count > 0 or len(shared) >= 2:
                meta = {"co_occurred": co_count, "shared_tags": shared}
                relations[c1][c2] = meta
                relations[c2][c1] = meta

    return dict(relations)


# ── Step 8: Concept note with grouped refs and annotated relations ──


def write_concept_note(
    concept: str,
    references: list[dict],
    aliases: list[str] | None = None,
    related_concepts: dict[str, dict] | None = None,
) -> Path:
    if CONCEPTS_DIR is None:
        raise RuntimeError("Vault directory not configured.")
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    concept_file = CONCEPTS_DIR / f"{concept_note_stem(concept)}.md"
    legacy_file = CONCEPTS_DIR / f"{sanitize_filename(concept)}.md"
    if not concept_file.exists() and legacy_file.exists():
        existing = legacy_file.read_text()
    else:
        existing = concept_file.read_text() if concept_file.exists() else ""
    created = (
        extract_created_date(existing)
        if existing
        else earliest_reference_date(references)
    )
    preserved_notes = extract_preserved_block(
        existing,
        USER_NOTES_START,
        USER_NOTES_END,
        "여기에 직접 정리한 영구 노트를 남기세요.",
    )

    # Group references by category, link to session notes
    by_category: dict[str, list[str]] = defaultdict(list)
    for ref in references:
        tagging = ref.get("tagging_result", {})
        cat = tagging.get("category", "other")
        title = tagging.get("title", session_filename(ref))
        link = f"- {make_wikilink(session_relative_path(ref), title)}"
        by_category[cat].append(link)

    ref_section_parts = []
    for cat in sorted(by_category.keys()):
        ref_section_parts.append(f"### {cat}\n\n" + "\n".join(by_category[cat]))
    ref_section = "\n\n".join(ref_section_parts)

    # Build annotated related concepts section with relationship types
    related_lines = ""
    if related_concepts:
        sorted_related = sorted(
            ((c, meta) for c, meta in related_concepts.items() if c != concept),
            key=lambda x: x[0],
        )
        if sorted_related:
            lines = []
            for c, meta in sorted_related:
                parts = []
                rel_type = meta.get("relationship_type", "co-occurrence")
                if rel_type != "co-occurrence":
                    parts.append(rel_type)
                co = meta.get("co_occurred", 0)
                if co > 0:
                    parts.append(f"co-occurred {co} time{'s' if co != 1 else ''}")
                shared = meta.get("shared_tags", set())
                if shared:
                    parts.append(f"shared tags: {', '.join(sorted(shared))}")
                annotation = " — " + ", ".join(parts) if parts else ""
                lines.append(
                    f"- {make_wikilink(concept_relative_path(c), c)}{annotation}"
                )
            related_lines = "\n## Related Concepts\n\n" + "\n".join(lines) + "\n"

    # Build version history section
    version_history_lines = ""
    versions = build_version_history(concept, references)
    if versions:
        vh_lines = []
        for v in versions:
            label = "latest" if v["is_latest"] else ""
            summary_text = truncate(v["summary"], 80)
            session_link = make_wikilink(
                session_relative_path(v["qa"]),
                v["qa"]
                .get("tagging_result", {})
                .get("title", session_filename(v["qa"])),
            )
            if label:
                vh_lines.append(
                    f'- v{v["version"]} (latest): {session_link} — "{summary_text}"'
                )
            else:
                vh_lines.append(f'- v{v["version"]}: {session_link} — "{summary_text}"')
        version_history_lines = "\n## Version History\n\n" + "\n".join(vh_lines) + "\n"

    ref_count = sum(len(v) for v in by_category.values())

    all_tags: set[str] = set()
    for ref in references:
        tagging = ref.get("tagging_result", {})
        all_tags.update(tagging.get("tags", []))

    tags_yaml = (
        "\n".join(f"  - {t}" for t in sorted(all_tags)) if all_tags else "  - concept"
    )

    # Determine aggregate memory type (majority vote) and average importance
    static_count = sum(
        1
        for r in references
        if r.get("tagging_result", {}).get("memory_type") == "static"
    )
    dynamic_count = len(references) - static_count
    concept_memory_type = "static" if static_count >= dynamic_count else "dynamic"
    importances = [r.get("tagging_result", {}).get("importance", 3) for r in references]
    avg_importance = round(sum(importances) / len(importances)) if importances else 3

    # Version info
    is_latest = True
    version = versions[0]["version"] if versions else 1
    version_frontmatter = f"\nis_latest: {str(is_latest).lower()}\nversion: {version}"
    aliases = sorted(set(aliases or [concept]))
    aliases_yaml = "\n".join(f"  - {alias}" for alias in aliases)
    alias_lines = "\n".join(f"- `{alias}`" for alias in aliases)
    draft_body = build_concept_draft(concept, references)
    takeaways = build_takeaways(references)
    user_notes_block = render_preserved_block(
        "User Notes", USER_NOTES_START, USER_NOTES_END, preserved_notes
    )

    content = f"""---
title: {concept}
aliases:
{aliases_yaml}
tags:
{tags_yaml}
  - concept
type: permanent-note
zettel_stage: permanent
note_id: {note_id("concept", concept)}
canonical_key: "{concept_lookup_key(concept)}"
memory_type: {concept_memory_type}
importance: {avg_importance}{version_frontmatter}
created: {created}
updated: {datetime.now().strftime("%Y-%m-%d")}
references: {ref_count}
---

# {concept}
> [!summary] Permanent Note Draft
> This note is generated from Claude sessions. Update the **User Notes** section to make it truly yours.

## Distilled Note

{draft_body}

## Key Takeaways

{takeaways}

## Aliases

{alias_lines}
{version_history_lines}
## Referenced Conversations

{ref_section}
{related_lines}{user_notes_block}"""
    concept_file.write_text(content)
    if legacy_file.exists() and legacy_file != concept_file:
        legacy_file.unlink()
    return concept_file


# ── Step 10: MOC with Recent Sessions ──


def update_moc(
    daily_files: list[str],
    concept_files: list[tuple[str, str] | str],
    session_files: list[str] | None = None,
) -> None:
    if OBS_DIR is None or MOC_PATH is None:
        raise RuntimeError("Vault directory not configured.")
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    existing_dailies: set[str] = set()
    existing_concepts: dict[str, str] = {}
    existing_sessions: set[str] = set()
    existing = MOC_PATH.read_text() if MOC_PATH.exists() else ""
    curated_maps = extract_preserved_block(
        existing,
        CURATED_MAPS_START,
        CURATED_MAPS_END,
        "주제별 MOC나 수동 구조 노트를 여기에 연결하세요.",
    )

    if existing:
        for match in re.finditer(r"\[\[30-archives/daily/([^\]|]+)", existing):
            existing_dailies.add(match.group(1))
        for match in re.finditer(
            r"\[\[(20-resources/concepts/[^\]|]+)(?:\|([^\]]+))?\]\]", existing
        ):
            target = match.group(1)
            existing_concepts[target] = match.group(
                2
            ) or concept_display_label_from_path(target)
        for match in re.finditer(r"\[\[(00-projects/[^\]|]+)", existing):
            existing_sessions.add(match.group(1))

    for d in daily_files:
        existing_dailies.add(d)
    for concept_item in concept_files:
        if isinstance(concept_item, tuple):
            target, label = concept_item
        else:
            target, label = concept_item, concept_display_label_from_path(concept_item)
        existing_concepts[target] = label
    if session_files:
        for s in session_files:
            existing_sessions.add(s)

    sorted_dailies = sorted(existing_dailies, reverse=True)
    sorted_concepts = sorted(existing_concepts.items())
    sorted_sessions = sorted(existing_sessions, reverse=True)[:20]

    daily_links = "\n".join(
        make_wikilink(daily_relative_path(d), d) for d in sorted_dailies
    )
    concept_links = "\n".join(
        f"- {make_wikilink(target, label)}" for target, label in sorted_concepts
    )
    session_links = "\n".join(f"- {make_wikilink(s)}" for s in sorted_sessions)
    curated_maps_block = render_preserved_block(
        "Curated Maps", CURATED_MAPS_START, CURATED_MAPS_END, curated_maps
    )

    content = f"""---
title: Knowledge Graph - Map of Content
updated: {today}
tags:
  - MOC
  - obsitocin
type: structure-note
zettel_stage: index
---

# Knowledge Graph

Map of Content for the AI conversation knowledge graph.

## Recent Sessions

{session_links}

## Daily Logs

{daily_links}

## Concepts

{concept_links}
{curated_maps_block}
"""
    MOC_PATH.write_text(content)


# ── User Profile ──


def write_user_profile(
    all_qa: list[dict], concept_refs: dict[str, list[dict]]
) -> Path | None:
    """Generate _Profile.md from accumulated knowledge.

    Aggregates static memories (importance >= 4) for core skills,
    recent dynamic memories for current activity, and top concepts by frequency.
    """
    if PROFILE_PATH is None or OBS_DIR is None:
        return None

    OBS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect static high-importance items
    static_items: list[dict] = []
    dynamic_recent: list[dict] = []
    now = datetime.now()

    for qa in all_qa:
        tagging = qa.get("tagging_result", {})
        memory_type = tagging.get("memory_type", "dynamic")
        importance = tagging.get("importance", 3)

        if memory_type == "static" and importance >= 4:
            static_items.append(qa)

        if memory_type == "dynamic":
            ts = qa.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                if (now - dt).days <= 7:
                    dynamic_recent.append(qa)
            except (ValueError, TypeError):
                pass

    # Also load previously written QAs for a fuller profile
    for filepath in PROCESSED_DIR.glob("*.json"):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        if qa.get("status") != "written":
            continue
        tagging = qa.get("tagging_result", {})
        memory_type = tagging.get("memory_type", "dynamic")
        importance = tagging.get("importance", 3)

        if memory_type == "static" and importance >= 4:
            # Avoid duplicates by session_id
            if not any(
                s.get("session_id") == qa.get("session_id") for s in static_items
            ):
                static_items.append(qa)

        if memory_type == "dynamic":
            ts = qa.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                if (now - dt).days <= 7:
                    if not any(
                        d.get("session_id") == qa.get("session_id")
                        for d in dynamic_recent
                    ):
                        dynamic_recent.append(qa)
            except (ValueError, TypeError):
                pass

    # Extract tags and concepts from static items for skill inference
    static_tags: dict[str, int] = defaultdict(int)
    static_concepts: dict[str, int] = defaultdict(int)
    cwds: dict[str, int] = defaultdict(int)

    for qa in static_items:
        tagging = qa.get("tagging_result", {})
        for tag in tagging.get("tags", []):
            static_tags[tag] += 1
        for concept in _concepts_for(qa):
            static_concepts[concept] += 1

    # Static profile section
    top_tags = sorted(static_tags.items(), key=lambda x: -x[1])[:15]
    static_section = ""
    if top_tags:
        tag_lines = "\n".join(f"- `{tag}` ({count})" for tag, count in top_tags)
        static_section = f"## Core Skills & Preferences (Static)\n\n{tag_lines}\n"
    else:
        static_section = "## Core Skills & Preferences (Static)\n\n_Not enough static memories yet._\n"

    # Dynamic recent activity section
    dynamic_section = ""
    if dynamic_recent:
        # Group by project
        for qa in dynamic_recent:
            cwd = qa.get("cwd", "").replace("\\", "/")
            if cwd:
                cwds[cwd] += 1

        recent_projects = sorted(cwds.items(), key=lambda x: -x[1])[:5]
        project_lines = (
            "\n".join(f"- `{p}` ({c} sessions)" for p, c in recent_projects)
            if recent_projects
            else ""
        )

        recent_topics = []
        for qa in sorted(
            dynamic_recent, key=lambda x: x.get("timestamp", ""), reverse=True
        )[:10]:
            tagging = qa.get("tagging_result", {})
            title = tagging.get("title", "Untitled")
            recent_topics.append(f"- {make_wikilink(session_relative_path(qa), title)}")
        topics_str = "\n".join(recent_topics)

        dynamic_section = f"## Recent Activity (Dynamic, last 7 days)\n\n"
        if project_lines:
            dynamic_section += f"### Active Projects\n\n{project_lines}\n\n"
        dynamic_section += f"### Recent Sessions\n\n{topics_str}\n"
    else:
        dynamic_section = "## Recent Activity (Dynamic)\n\n_No recent activity._\n"

    # Top concepts by frequency (across all data)
    all_concept_counts: dict[str, int] = {}
    for concept, refs in concept_refs.items():
        all_concept_counts[concept] = len(refs)
    # Also count from previously written
    for filepath in PROCESSED_DIR.glob("*.json"):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        for concept in qa.get("tagging_result", {}).get("key_concepts", []):
            if concept not in all_concept_counts:
                all_concept_counts[concept] = 0
            # Don't double-count; concept_refs already has current batch

    top_concepts = sorted(all_concept_counts.items(), key=lambda x: -x[1])[:15]
    concepts_lines = "\n".join(
        f"{i + 1}. {make_wikilink(concept_relative_path(c), c)} ({count} session{'s' if count != 1 else ''})"
        for i, (c, count) in enumerate(top_concepts)
    )
    concepts_section = (
        f"## Top Concepts (by frequency)\n\n{concepts_lines}\n" if top_concepts else ""
    )

    content = f"""---
title: Developer Profile
type: profile
updated: {today}
static_memories: {len(static_items)}
recent_sessions: {len(dynamic_recent)}
---

# Developer Profile

{static_section}
{dynamic_section}
{concepts_section}"""

    PROFILE_PATH.write_text(content)
    return PROFILE_PATH


# ── Step 9: Main orchestration ──


def main() -> None:
    if OBS_DIR is None:
        print(
            "Error: Vault directory not configured. Run 'obsitocin init --vault-dir <path>' first."
        )
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 50)
    log("Obsidian Writer started")

    for d in (
        PARA_PROJECTS_DIR,
        PARA_AREAS_DIR,
        PARA_RESOURCES_DIR,
        PARA_ARCHIVES_DIR,
        CONCEPTS_DIR,
        DAILY_DIR,
    ):
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)

    files = get_processed_files()
    if not files:
        log("No processed files to write, exiting")
        return

    log(f"Found {len(files)} processed file(s)")

    daily_entries: dict[str, list[str]] = {}
    daily_concepts: dict[str, list[str]] = {}
    concept_refs: dict[str, list[dict]] = {}
    written_dailies: list[str] = []
    written_concepts: list[tuple[str, str] | str] = []
    written_sessions: list[str] = []
    all_qa: list[dict] = []
    file_records: list[tuple[Path, dict]] = []

    for filepath in files:
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception) as e:
            log(f"Failed to read {filepath.name}: {e}")
            continue

        if qa.get("status") not in ("processed", "written"):
            continue

        all_qa.append(qa)
        file_records.append((filepath, qa))

    if not all_qa:
        log("No written or processed files available for rebuild, exiting")
        return

    concept_catalog = build_concept_catalog(all_qa)
    alias_to_canonical = concept_catalog["alias_to_canonical"]

    for qa in all_qa:
        tagging = qa.setdefault("tagging_result", {})
        canonical_concepts = canonicalize_concepts(
            tagging.get("key_concepts", []), alias_to_canonical
        )
        tagging["canonical_concepts"] = canonical_concepts

        ts = qa.get("timestamp", "")
        try:
            date_str = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = datetime.now().strftime("%Y-%m-%d")

        entry = build_daily_entry(qa)
        daily_entries.setdefault(date_str, []).append(entry)

        for concept in canonical_concepts:
            concept_refs.setdefault(concept, []).append(qa)
            daily_concepts.setdefault(date_str, []).append(concept)

    for filepath, qa in file_records:
        if qa.get("status") == "processed":
            qa["status"] = "written"
        qa["written_at"] = datetime.now().isoformat()
        filepath.write_text(json.dumps(qa, ensure_ascii=False, indent=2))

    # Build session-to-session similarity relations
    session_rels = build_session_relations(all_qa)
    total_see_also = sum(len(v) for v in session_rels.values())
    log(f"Built session relations: {total_see_also} 'See Also' links")

    threads = group_issue_threads(all_qa)
    written_threads = 0
    for thread in threads:
        project_name = _extract_project_name(thread[0].get("cwd", ""))
        thread_path = write_issue_thread(thread, written_threads, project_name)
        written_threads += 1
        log(f"Wrote issue thread: {thread_path.name} ({len(thread)} sessions)")

    # Write individual session notes
    for qa in all_qa:
        session_key = str(qa.get("session_id", session_filename(qa)))
        see_also = session_rels.get(session_key, [])
        session_path = write_session_note(qa, see_also)
        written_sessions.append(session_relative_path(qa))
        log(f"Wrote session note: {session_path.name}")

    # Write daily notes (index format)
    for date_str, entries in daily_entries.items():
        day_concepts = daily_concepts.get(date_str)
        daily_path = write_daily_note(date_str, entries, day_concepts)
        written_dailies.append(date_str)
        log(f"Wrote daily note: {daily_path.name}")

    # Build concept relations with typed relationships
    relations = classify_concept_relations(concept_refs)
    total_relations = sum(len(v) for v in relations.values()) // 2
    log(f"Built concept relations: {total_relations} unique pairs")

    # Write concept notes
    for concept, refs in concept_refs.items():
        related = relations.get(concept)
        aliases = concept_catalog["concepts"].get(concept, {}).get("aliases", [concept])
        concept_path = write_concept_note(concept, refs, aliases, related)
        written_concepts.append((concept_relative_path(concept), concept))
        log(f"Wrote concept note: {concept_path.name}")

    # Update MOC
    if written_dailies or written_concepts or written_sessions:
        update_moc(written_dailies, written_concepts, written_sessions)
        log("Updated _MOC.md")

    # Generate user profile
    try:
        profile_path = write_user_profile(all_qa, concept_refs)
        if profile_path:
            log(f"Updated profile: {profile_path.name}")
    except Exception as e:
        log(f"Profile generation failed: {e}")

    log(
        f"Done: {len(written_sessions)} session notes, "
        f"{len(written_dailies)} daily notes, "
        f"{len(written_concepts)} concept notes"
    )
    log("Obsidian Writer finished")


if __name__ == "__main__":
    main()
