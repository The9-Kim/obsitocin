"""Vault content lint checks for obsitocin.

Checks:
1. check_broken_wikilinks  — wikilinks in MOC/index not matching actual files
2. check_orphan_topics     — topic notes not referenced by any other file
3. check_thin_notes        — topic notes with fewer than min_knowledge items
4. check_moc_consistency   — topics in MOC not matching filesystem or vice versa
"""

from __future__ import annotations

import re
from pathlib import Path


def _extract_wikilinks(text: str) -> list[str]:
    """Extract [[path|label]] or [[path]] paths from markdown text."""
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", text)


def _extract_fm(content: str, key: str, default: str = "") -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else default


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


def check_broken_wikilinks(vault_dir: Path) -> list[dict]:
    """Find wikilinks in MOC and index files that point to nonexistent files."""
    issues = []
    check_files = []
    moc = vault_dir / "_MOC.md"
    if moc.exists():
        check_files.append(moc)

    projects_dir = vault_dir / "projects"
    if projects_dir.exists():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            index = project_dir / "_index.md"
            if index.exists():
                check_files.append(index)

    for check_file in check_files:
        try:
            content = check_file.read_text(errors="replace")
        except OSError:
            continue

        for link_path in _extract_wikilinks(content):
            candidate = vault_dir / link_path
            candidate_md = vault_dir / (link_path + ".md")
            if not candidate.exists() and not candidate_md.exists():
                issues.append(
                    {
                        "type": "broken_wikilink",
                        "file": str(check_file.relative_to(vault_dir)),
                        "link": link_path,
                        "message": f"Wikilink not found: {link_path}",
                    }
                )

    return issues


def check_orphan_topics(vault_dir: Path) -> list[dict]:
    """Find topic notes that are not referenced by any other file."""
    issues = []
    projects_dir = vault_dir / "projects"
    if not projects_dir.exists():
        return []

    all_topic_paths: set[str] = set()
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue
        for f in topics_dir.glob("*.md"):
            rel = str(f.relative_to(vault_dir).with_suffix(""))
            all_topic_paths.add(rel)

    referenced: set[str] = set()
    for md_file in vault_dir.rglob("*.md"):
        try:
            content = md_file.read_text(errors="replace")
        except OSError:
            continue
        for link in _extract_wikilinks(content):
            referenced.add(link.rstrip("/"))

    for topic_path in sorted(all_topic_paths):
        if topic_path not in referenced:
            issues.append(
                {
                    "type": "orphan_topic",
                    "path": topic_path,
                    "message": f"Topic not referenced by any other file: {topic_path}",
                }
            )

    return issues


def check_thin_notes(vault_dir: Path, min_knowledge: int = 2) -> list[dict]:
    """Find topic notes with fewer than min_knowledge knowledge items."""
    issues = []
    projects_dir = vault_dir / "projects"
    if not projects_dir.exists():
        return []

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue
        for f in sorted(topics_dir.glob("*.md")):
            try:
                content = f.read_text(errors="replace")
            except OSError:
                continue
            knowledge = _extract_bullet_section(content, "핵심 지식")
            real_knowledge = [k for k in knowledge if k and "아직 축적된" not in k]
            if len(real_knowledge) < min_knowledge:
                title = _extract_fm(content, "title", f.stem)
                issues.append(
                    {
                        "type": "thin_note",
                        "path": str(f.relative_to(vault_dir)),
                        "title": title,
                        "knowledge_count": len(real_knowledge),
                        "min_required": min_knowledge,
                        "message": f"Topic '{title}' has only {len(real_knowledge)} knowledge items (minimum: {min_knowledge})",
                    }
                )

    return issues


def check_moc_consistency(vault_dir: Path) -> list[dict]:
    """Find topics in MOC not on filesystem, or on filesystem but not in MOC."""
    issues = []
    moc_path = vault_dir / "_MOC.md"

    moc_links: set[str] = set()
    if moc_path.exists():
        try:
            moc_content = moc_path.read_text(errors="replace")
            moc_links = set(_extract_wikilinks(moc_content))
        except OSError:
            pass

    actual_topics: set[str] = set()
    projects_dir = vault_dir / "projects"
    if projects_dir.exists():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            topics_dir = project_dir / "topics"
            if not topics_dir.exists():
                continue
            for f in topics_dir.glob("*.md"):
                rel = str(f.relative_to(vault_dir).with_suffix(""))
                actual_topics.add(rel)

    for link in sorted(moc_links):
        if link.startswith("projects/") and "/topics/" in link:
            if link not in actual_topics:
                candidate = vault_dir / (link + ".md")
                if not candidate.exists():
                    issues.append(
                        {
                            "type": "moc_stale_entry",
                            "link": link,
                            "message": f"MOC references nonexistent topic: {link}",
                        }
                    )

    for topic_path in sorted(actual_topics):
        if topic_path not in moc_links:
            issues.append(
                {
                    "type": "moc_missing_entry",
                    "path": topic_path,
                    "message": f"Topic not listed in MOC: {topic_path}",
                }
            )

    return issues


def run_all_checks(vault_dir: Path, min_knowledge: int = 2) -> dict:
    """Run all lint checks. Returns summary dict with check results."""
    broken_wikilinks = check_broken_wikilinks(vault_dir)
    orphan_topics = check_orphan_topics(vault_dir)
    thin_notes = check_thin_notes(vault_dir, min_knowledge=min_knowledge)
    moc_consistency = check_moc_consistency(vault_dir)

    total_issues = (
        len(broken_wikilinks)
        + len(orphan_topics)
        + len(thin_notes)
        + len(moc_consistency)
    )

    return {
        "vault_dir": str(vault_dir),
        "total_issues": total_issues,
        "checks": {
            "broken_wikilinks": broken_wikilinks,
            "orphan_topics": orphan_topics,
            "thin_notes": thin_notes,
            "moc_consistency": moc_consistency,
        },
        "clean": total_issues == 0,
    }
