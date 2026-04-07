#!/usr/bin/env python3
"""Project > Topic writer: accumulates knowledge per-project, per-topic.

Vault structure:
  obsitocin/
  ├── _MOC.md                          (cross-project index)
  ├── projects/
  │   └── <project>/
  │       ├── _index.md                (project MOC)
  │       └── topics/
  │           └── <topic>.md           (accumulated knowledge)
  └── daily/
      └── <date>.md                    (work log)
"""

import re
from datetime import datetime
from pathlib import Path

from obsitocin.concepts import (
    concept_lookup_key,
    concept_note_stem,
    find_fuzzy_topic_match,
)  # concept_note_stem: legacy only
from obsitocin.config import LOGS_DIR, OBS_DIR


def _topic_file_stem(text: str) -> str:
    import re as _re

    clean = _re.sub(r'[\\/:*?"<>|]', "", text).strip() or "untitled"
    return clean[:80]


LOG_FILE = LOGS_DIR / "topic_writer.log" if LOGS_DIR else Path("topic_writer.log")

USER_NOTES_START = "<!-- OBSITOCIN:BEGIN USER NOTES -->"
USER_NOTES_END = "<!-- OBSITOCIN:END USER NOTES -->"


def log(msg: str) -> None:
    if LOGS_DIR:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _extract_project_name(cwd: str) -> str:
    if not cwd:
        return "uncategorized"
    return Path(cwd).name or "uncategorized"


def _projects_dir() -> Path | None:
    return OBS_DIR / "projects" if OBS_DIR else None


def _daily_dir() -> Path | None:
    return OBS_DIR / "daily" if OBS_DIR else None


def _project_topics_dir(project: str) -> Path | None:
    base = _projects_dir()
    return base / project / "topics" if base else None


def _project_entities_dir(project: str) -> Path | None:
    base = _projects_dir()
    return base / project / "entities" if base else None


def _project_sources_dir(project: str) -> Path | None:
    base = _projects_dir()
    return base / project / "sources" if base else None


def _raw_dir() -> Path | None:
    return OBS_DIR / "raw" if OBS_DIR else None


PAGE_TYPES = {"topic", "entity", "source"}
TOPIC_PROMOTION_IMPORTANCE_MIN = 3


# ── Markdown parsing helpers ──


def _extract_bullet_section(content: str, heading: str) -> list[str]:
    pattern = rf"## {re.escape(heading)}\s*\n((?:.*\n)*?)(?=\n## |\Z)"
    match = re.search(pattern, content)
    if not match:
        return []
    return [
        line.lstrip("- ").strip()
        for line in match.group(1).strip().split("\n")
        if line.strip().startswith("- ")
    ]


def _extract_preserved_block(content: str) -> str:
    if not content:
        return ""
    pattern = re.escape(USER_NOTES_START) + r"\n?(.*?)\n?" + re.escape(USER_NOTES_END)
    match = re.search(pattern, content, flags=re.DOTALL)
    return match.group(1).strip("\n") if match else ""


def _extract_fm(content: str, key: str, default: str = "") -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else default


def _extract_fm_tags(content: str) -> set[str]:
    tags: set[str] = set()
    block = re.search(r"tags:\s*\n((?:\s+-\s+.+\n)*)", content)
    if block:
        for t in re.findall(r"-\s+(\S+)", block.group(1)):
            tags.add(t.strip())
    return tags


# ── Cross-project topic scan ──


def _scan_all_topics() -> dict[str, list[tuple[str, str, Path]]]:
    """Returns {lookup_key: [(project, title, path), ...]} across all projects."""
    result: dict[str, list[tuple[str, str, Path]]] = {}
    projects_dir = _projects_dir()
    if not projects_dir or not projects_dir.exists():
        return result
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue
        project = project_dir.name
        for f in topics_dir.glob("*.md"):
            try:
                content = f.read_text(errors="replace")
            except OSError:
                continue
            title_match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            if title_match:
                title = title_match.group(1).strip()
                key = concept_lookup_key(title)
                result.setdefault(key, []).append((project, title, f))
    return result


def _find_cross_project_refs(
    topic: str,
    current_project: str,
    all_topics: dict[str, list[tuple[str, str, Path]]] | None = None,
) -> list[tuple[str, str]]:
    if all_topics is None:
        all_topics = _scan_all_topics()
    key = concept_lookup_key(topic)
    matches = all_topics.get(key, [])
    return [
        (project, title)
        for project, title, _path in matches
        if project != current_project
    ]


def _ensure_cross_link(
    target_project: str, target_topic: str, source_project: str, source_topic: str
) -> None:
    topics_dir = _project_topics_dir(target_project)
    if not topics_dir or not topics_dir.exists():
        return
    index = _scan_topic_index(target_project)
    target_file = index.get(concept_lookup_key(target_topic))
    if not target_file or not target_file.exists():
        return
    content = target_file.read_text(errors="replace")
    link_path = _topic_rel_path(source_project, source_topic)
    if link_path in content:
        return
    link_line = f"- [[{link_path}|{source_topic} ({source_project})]]"
    if "## 관련 주제" in content:
        content = content.replace("## 관련 주제\n", f"## 관련 주제\n\n{link_line}", 1)
    else:
        content = content.replace(
            "## User Notes",
            f"## 관련 주제\n\n{link_line}\n\n## User Notes",
        )
    target_file.write_text(content)
    log(f"[{target_project}] Added cross-link to {source_topic} ({source_project})")


# ── Topic note index (per-project) ──


def _scan_topic_index(project: str) -> dict[str, Path]:
    index: dict[str, Path] = {}
    topics_dir = _project_topics_dir(project)
    if not topics_dir or not topics_dir.exists():
        return index
    for f in topics_dir.glob("*.md"):
        try:
            content = f.read_text(errors="replace")
        except OSError:
            continue
        title_match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
        if title_match:
            index[concept_lookup_key(title_match.group(1).strip())] = f
        aliases_block = re.search(r"aliases:\s*\n((?:\s+-\s+.+\n)*)", content)
        if aliases_block:
            for alias in re.findall(r"-\s+(.+)", aliases_block.group(1)):
                alias = alias.strip()
                if alias:
                    index[concept_lookup_key(alias)] = f
    return index


def _topic_rel_path(project: str, topic: str) -> str:
    return f"projects/{project}/topics/{_topic_file_stem(topic)}"


# ── LLM knowledge synthesis ──


def _synthesize_knowledge(existing: list[str], new: list[str], topic: str) -> list[str]:
    if not existing or not new:
        return existing + [k for k in new if k and k not in set(existing)]

    try:
        from obsitocin.provider import run_provider_prompt

        existing_str = "\n".join(f"- {k}" for k in existing)
        new_str = "\n".join(f"- {k}" for k in new)
        prompt = (
            f'주제 "{topic}"에 대한 지식을 합성해라.\n\n'
            f"기존 지식:\n{existing_str}\n\n"
            f"새로 배운 내용:\n{new_str}\n\n"
            "규칙:\n"
            "- 기존 지식을 삭제하지 마라. 보강하거나 그대로 유지해라.\n"
            "- 중복되는 내용은 하나로 합쳐라.\n"
            "- 새 내용 중 기존에 없는 것은 추가해라.\n"
            "- 각 항목은 한 줄로, '- ' 접두사로 시작해라.\n"
            "- 항목 리스트만 출력해라. 다른 설명은 불필요.\n"
        )
        result = run_provider_prompt(prompt, timeout=60)
        lines = [
            line.lstrip("- ").strip()
            for line in result.strip().split("\n")
            if line.strip().startswith("- ")
        ]
        if lines:
            return lines
    except Exception as e:
        log(f"Knowledge synthesis failed, falling back to append: {e}")

    merged = list(existing)
    known = set(existing)
    for item in new:
        if item and item not in known:
            merged.append(item)
            known.add(item)
    return merged


# ── Write / update topic note ──


def write_topic_note(
    project: str,
    topic: str,
    new_knowledge: list[str],
    work_summary: str,
    timestamp: str,
    tags: list[str],
    importance: int,
    related_topics: list[str] | None = None,
    page_type: str = "topic",
) -> Path | None:
    dir_map = {
        "topic": _project_topics_dir,
        "entity": _project_entities_dir,
        "source": _project_sources_dir,
    }
    get_dir = dir_map.get(page_type, _project_topics_dir)
    target_parent = get_dir(project)
    if target_parent is None:
        return None
    target_parent.mkdir(parents=True, exist_ok=True)
    topics_dir = target_parent

    try:
        dt = datetime.fromisoformat(timestamp)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        date_str = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M")

    history_entry = f"{date_str} {time_str}: {work_summary}"

    index = _scan_topic_index(project)
    existing_file = index.get(concept_lookup_key(topic))
    if existing_file is None:
        existing_file = find_fuzzy_topic_match(topic, index)
        if existing_file is not None:
            log(f"[{project}] Fuzzy-matched topic '{topic}' → existing '{existing_file.stem}'")

    if existing_file and existing_file.exists():
        existing_content = existing_file.read_text(errors="replace")
        knowledge = _extract_bullet_section(existing_content, "핵심 지식")
        history = _extract_bullet_section(existing_content, "히스토리")
        preserved_notes = _extract_preserved_block(existing_content)
        created = _extract_fm(existing_content, "created", date_str)
        all_tags = _extract_fm_tags(existing_content) | set(tags)

        knowledge = _synthesize_knowledge(knowledge, new_knowledge, topic)

        history.insert(0, history_entry)
        target_file = existing_file
    else:
        knowledge = [k for k in new_knowledge if k]
        history = [history_entry]
        preserved_notes = "여기에 직접 정리한 내용을 작성하세요."
        created = date_str
        all_tags = set(tags)
        target_file = topics_dir / f"{_topic_file_stem(topic)}.md"

    related_section = ""
    related_links: list[str] = []
    if related_topics:
        related_links.extend(
            f"- [[{_topic_rel_path(project, rt)}|{rt}]]"
            for rt in related_topics
            if rt != topic
        )
    cross_refs = _find_cross_project_refs(topic, project)
    for ref_project, ref_title in cross_refs:
        related_links.append(
            f"- [[{_topic_rel_path(ref_project, ref_title)}|{ref_title} ({ref_project})]]"
        )
    if related_links:
        related_section = "\n## 관련 주제\n\n" + "\n".join(related_links) + "\n"

    knowledge_lines = (
        "\n".join(f"- {k}" for k in knowledge)
        if knowledge
        else "- (아직 축적된 지식 없음)"
    )
    history_lines = "\n".join(f"- {h}" for h in history)
    tags_yaml = (
        "\n".join(f"  - {t}" for t in sorted(all_tags)) if all_tags else "  - untagged"
    )

    content = f"""---
title: {topic}
project: {project}
tags:
{tags_yaml}
type: {page_type}-note
created: {created}
updated: {date_str}
sessions: {len(history)}
importance: {importance}
---

# {topic}

## 핵심 지식

{knowledge_lines}

## 히스토리

{history_lines}
{related_section}
## User Notes

{USER_NOTES_START}
{preserved_notes}
{USER_NOTES_END}
"""
    target_file.write_text(content)
    return target_file


# ── Project index ──


def update_project_index(project: str) -> Path | None:
    projects_dir = _projects_dir()
    if projects_dir is None:
        return None
    project_dir = projects_dir / project
    project_dir.mkdir(parents=True, exist_ok=True)

    index_path = project_dir / "_index.md"
    today = datetime.now().strftime("%Y-%m-%d")

    topics: list[tuple[str, str, int]] = []
    topics_dir = _project_topics_dir(project)
    if topics_dir and topics_dir.exists():
        for f in sorted(topics_dir.glob("*.md")):
            try:
                content = f.read_text(errors="replace")
            except OSError:
                continue
            title = _extract_fm(content, "title", f.stem)
            try:
                sessions = int(_extract_fm(content, "sessions", "0"))
            except ValueError:
                sessions = 0
            topics.append((title, _topic_rel_path(project, title), sessions))

    topics.sort(key=lambda x: -x[2])
    topic_links = "\n".join(
        f"- [[{path}|{title}]] ({sessions})" for title, path, sessions in topics
    )

    index_path.write_text(
        f"---\ntitle: {project}\ntype: project-index\nupdated: {today}\n"
        f"topics: {len(topics)}\n---\n\n# {project}\n\n"
        f"## 주제\n\n{topic_links or '(아직 주제 없음)'}\n"
    )
    return index_path


# ── Work log ──


def append_work_log(
    project: str,
    date_str: str,
    time_str: str,
    work_summary: str,
    topics: list[str],
) -> Path | None:
    daily_dir = _daily_dir()
    if daily_dir is None:
        return None
    daily_dir.mkdir(parents=True, exist_ok=True)

    log_file = daily_dir / f"{date_str}.md"

    topic_links = ", ".join(f"[[{_topic_rel_path(project, t)}|{t}]]" for t in topics)

    entry = f"- {time_str} [{project}] {work_summary}"
    if topic_links:
        entry += f" → {topic_links}"

    if log_file.exists():
        existing = log_file.read_text()
        if entry in existing:
            return log_file
        log_file.write_text(existing.rstrip() + "\n" + entry + "\n")
    else:
        log_file.write_text(
            f'---\ntitle: "{date_str} 작업 로그"\ndate: {date_str}\n'
            f"type: work-log\n---\n\n# {date_str} 작업 로그\n\n{entry}\n"
        )
    return log_file


# ── MOC (cross-project) ──


def update_moc() -> Path | None:
    if OBS_DIR is None:
        return None

    moc_path = OBS_DIR / "_MOC.md"
    today = datetime.now().strftime("%Y-%m-%d")
    projects_dir = _projects_dir()

    project_sections: list[str] = []
    if projects_dir and projects_dir.exists():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            project = project_dir.name
            topics_dir = project_dir / "topics"
            if not topics_dir.exists():
                continue

            topic_entries: list[tuple[str, str, int, str]] = []
            for f in sorted(topics_dir.glob("*.md")):
                try:
                    content = f.read_text(errors="replace")
                except OSError:
                    continue
                title = _extract_fm(content, "title", f.stem)
                try:
                    sessions = int(_extract_fm(content, "sessions", "0"))
                except ValueError:
                    sessions = 0
                # Extract first knowledge bullet as one-line summary
                knowledge = _extract_bullet_section(content, "핵심 지식")
                # Filter out placeholder text
                real_knowledge = [k for k in knowledge if k and "아직 축적된" not in k]
                first_knowledge = real_knowledge[0] if real_knowledge else ""
                topic_entries.append(
                    (title, _topic_rel_path(project, title), sessions, first_knowledge)
                )

            if not topic_entries:
                continue

            topic_entries.sort(key=lambda x: -x[2])
            lines = "\n".join(
                f"  - [[{path}|{title}]] ({s})"
                + (f" — {first_k[:80]}" if first_k else "")
                for title, path, s, first_k in topic_entries
            )
            project_sections.append(
                f"### [[projects/{project}/_index|{project}]]\n\n{lines}"
            )

    projects_body = (
        "\n\n".join(project_sections) if project_sections else "(아직 프로젝트 없음)"
    )

    daily_dir = _daily_dir()
    daily_links = ""
    if daily_dir and daily_dir.exists():
        daily_links = "\n".join(
            f"- [[daily/{f.stem}|{f.stem}]]"
            for f in sorted(daily_dir.glob("*.md"), reverse=True)
        )

    moc_path.write_text(
        f"---\ntitle: Knowledge Base\nupdated: {today}\ntype: moc\n---\n\n"
        f"# Knowledge Base\n\n## 프로젝트\n\n{projects_body}\n\n"
        f"## 작업 로그\n\n{daily_links or '(아직 작업 로그 없음)'}\n"
    )
    return moc_path


# ── Main entry ──


def write_notes_for_qa(qa: dict) -> dict:
    if OBS_DIR is None:
        return {"error": "Vault not configured"}

    tagging = qa.get("tagging_result", {})
    raw_topics = tagging.get("topics") or []
    _EMPTY_TITLES = {"untitled", "제목 없음", ""}
    work_summary = (
        tagging.get("work_summary")
        or tagging.get("summary", "")
        or ""
    )
    if not work_summary:
        title = (tagging.get("title") or "").strip()
        if title.lower() not in _EMPTY_TITLES:
            work_summary = title
    tags = tagging.get("tags", [])
    importance = tagging.get("importance", 3)
    timestamp = qa.get("timestamp", "")
    project = _extract_project_name(qa.get("cwd", ""))

    topic_entries: list[dict] = []
    for item in raw_topics:
        if isinstance(item, dict) and "name" in item:
            knowledge = [k for k in item.get("knowledge", []) if k and k.strip()]
            if not knowledge:
                continue
            topic_entries.append(item)
        elif isinstance(item, str) and item.strip():
            continue

    try:
        dt = datetime.fromisoformat(timestamp)
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")
    except (ValueError, TypeError):
        dt = datetime.now()
        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")

    all_topic_names = [e["name"] for e in topic_entries]
    written_topics: list[str] = []
    existing_index = _scan_topic_index(project)
    for entry in topic_entries:
        topic_name = entry["name"]
        knowledge = entry.get("knowledge", [])
        topic_key = concept_lookup_key(topic_name)
        fuzzy_hit = topic_key in existing_index or find_fuzzy_topic_match(
            topic_name, existing_index
        ) is not None
        if importance < TOPIC_PROMOTION_IMPORTANCE_MIN and not fuzzy_hit:
            log(
                f"[{project}] Skipped low-signal new topic: {topic_name} "
                f"(importance={importance})"
            )
            continue
        others = [n for n in all_topic_names if n != topic_name]
        path = write_topic_note(
            project=project,
            topic=topic_name,
            new_knowledge=knowledge,
            work_summary=work_summary,
            timestamp=timestamp,
            tags=tags,
            importance=importance,
            related_topics=others or None,
        )
        if path:
            written_topics.append(topic_name)
            existing_index[topic_key] = path
            log(f"[{project}] Updated topic: {topic_name}")

            cross_refs = _find_cross_project_refs(topic_name, project)
            for ref_project, ref_title in cross_refs:
                _ensure_cross_link(ref_project, ref_title, project, topic_name)

    if work_summary:
        append_work_log(project, date_str, time_str, work_summary, written_topics)

    update_project_index(project)
    update_moc()

    return {
        "project": project,
        "topics_written": len(written_topics),
        "work_log_updated": bool(work_summary),
    }
