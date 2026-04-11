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


def _resolve_db_path(vault_dir: Path) -> Path | None:
    """Return search.db path only if it belongs to the given vault."""
    try:
        from obsitocin.config import OBS_DIR, SEARCH_DB_PATH

        if not SEARCH_DB_PATH.exists():
            return None
        # Only run DB checks when vault_dir matches the configured vault
        if OBS_DIR is not None and vault_dir.resolve() == OBS_DIR.resolve():
            return SEARCH_DB_PATH
    except Exception:
        pass
    return None


def check_db_vault_consistency(vault_dir: Path) -> list[dict]:
    """Check that search.db entries and vault topic files are in sync."""
    issues: list[dict] = []
    try:
        db_path = _resolve_db_path(vault_dir)
        if db_path is None:
            return []
        from obsitocin.search_db import ensure_schema, get_connection

        conn = get_connection(db_path)
        ensure_schema(conn)

        # DB topic entries without vault files
        rows = conn.execute(
            "SELECT file_id, title, project FROM qa_entries WHERE source_type = 'topic_note'"
        ).fetchall()
        for row in rows:
            title = row["title"]
            project = row["project"]
            if project and title:
                topic_path = vault_dir / "projects" / project / "topics"
                candidates = [
                    topic_path / f"{title}.md",
                    topic_path / f"{_topic_file_stem(title)}.md",
                ]
                if not any(c.exists() for c in candidates):
                    issues.append(
                        {
                            "type": "db_orphan_entry",
                            "file_id": row["file_id"],
                            "message": f"DB entry has no vault file: {row['file_id']}",
                        }
                    )

        # Vault topics without DB entries
        projects_dir = vault_dir / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                topics_dir = project_dir / "topics"
                if not topics_dir.exists():
                    continue
                for f in topics_dir.glob("*.md"):
                    try:
                        content = f.read_text(errors="replace")
                    except OSError:
                        continue
                    title = _extract_fm(content, "title", f.stem)
                    db_key = f"topic:{project_dir.name}:{title}"
                    entry = conn.execute(
                        "SELECT file_id FROM qa_entries WHERE file_id = ?",
                        (db_key,),
                    ).fetchone()
                    if not entry:
                        issues.append(
                            {
                                "type": "db_missing_entry",
                                "path": str(f.relative_to(vault_dir)),
                                "message": f"Vault topic not indexed in search.db: {title}",
                            }
                        )

        conn.close()
    except Exception:
        pass
    return issues


def _topic_file_stem(text: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]', "", text).strip() or "untitled"
    return clean[:80]


def check_fts_integrity(vault_dir: Path) -> list[dict]:
    """Verify FTS index row count matches qa_entries."""
    issues: list[dict] = []
    try:
        db_path = _resolve_db_path(vault_dir)
        if db_path is None:
            return []
        from obsitocin.search_db import ensure_schema, get_connection

        conn = get_connection(db_path)
        ensure_schema(conn)

        entry_count = conn.execute("SELECT COUNT(*) FROM qa_entries").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM qa_fts").fetchone()[0]

        if entry_count != fts_count:
            issues.append(
                {
                    "type": "fts_count_mismatch",
                    "entries": entry_count,
                    "fts_rows": fts_count,
                    "message": f"FTS index has {fts_count} rows but qa_entries has {entry_count}",
                }
            )

        try:
            conn.execute("INSERT INTO qa_fts(qa_fts) VALUES('integrity-check')")
        except Exception as e:
            issues.append(
                {
                    "type": "fts_integrity_error",
                    "message": f"FTS integrity check failed: {e}",
                }
            )

        conn.close()
    except Exception:
        pass
    return issues


def check_orphan_embeddings(vault_dir: Path) -> list[dict]:
    """Find embeddings/chunks without matching parent records."""
    issues: list[dict] = []
    try:
        db_path = _resolve_db_path(vault_dir)
        if db_path is None:
            return []
        from obsitocin.search_db import ensure_schema, get_connection

        conn = get_connection(db_path)
        ensure_schema(conn)

        # Embeddings without chunks
        orphan_emb = conn.execute(
            """SELECT e.chunk_id FROM embeddings e
               LEFT JOIN chunks c ON e.chunk_id = c.chunk_id
               WHERE c.chunk_id IS NULL"""
        ).fetchall()
        for row in orphan_emb:
            issues.append(
                {
                    "type": "orphan_embedding",
                    "chunk_id": row[0],
                    "message": f"Embedding for chunk_id={row[0]} has no matching chunk",
                }
            )

        # Chunks without entries
        orphan_chunks = conn.execute(
            """SELECT c.chunk_id, c.file_id FROM chunks c
               LEFT JOIN qa_entries e ON c.file_id = e.file_id
               WHERE e.file_id IS NULL"""
        ).fetchall()
        for row in orphan_chunks:
            issues.append(
                {
                    "type": "orphan_chunk",
                    "chunk_id": row[0],
                    "file_id": row[1],
                    "message": f"Chunk {row[0]} references missing entry '{row[1]}'",
                }
            )

        conn.close()
    except Exception:
        pass
    return issues


def _find_db_for_vault(vault_dir: Path) -> Path | None:
    """Find search.db that corresponds to this vault."""
    from obsitocin.config import SEARCH_DB_PATH, OBS_DIR
    # Only use global DB if vault_dir matches the configured vault
    if OBS_DIR and vault_dir == OBS_DIR and SEARCH_DB_PATH.exists():
        return SEARCH_DB_PATH
    return None


def check_orphan_links(vault_dir: Path) -> list[dict]:
    """Find typed links pointing to topics that no longer exist in the vault."""
    try:
        db_path = _find_db_for_vault(vault_dir)
        if not db_path:
            return []
        from obsitocin.search_db import ensure_schema, get_connection

        conn = get_connection(db_path)
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT source_project, source_topic, target_project, target_topic, link_type "
            "FROM topic_links"
        ).fetchall()
        conn.close()

        issues = []
        projects_dir = vault_dir / "projects"
        for row in rows:
            r = dict(row)
            target_dir = projects_dir / r["target_project"] / "topics"
            if not target_dir.exists():
                issues.append({
                    "source": f"{r['source_project']}/{r['source_topic']}",
                    "target": f"{r['target_project']}/{r['target_topic']}",
                    "link_type": r["link_type"],
                    "severity": "warn",
                    "message": f"Link {r['link_type']}: {r['source_project']}/{r['source_topic']} → {r['target_project']}/{r['target_topic']} (target project missing)",
                })
                continue
            # Check if target topic file exists
            found = False
            for f in target_dir.glob("*.md"):
                content = f.read_text(errors="replace")
                title_m = _extract_fm(content, "title")
                if title_m and title_m.lower() == r["target_topic"].lower():
                    found = True
                    break
                if f.stem.lower() == r["target_topic"].lower():
                    found = True
                    break
            if not found:
                issues.append({
                    "source": f"{r['source_project']}/{r['source_topic']}",
                    "target": f"{r['target_project']}/{r['target_topic']}",
                    "link_type": r["link_type"],
                    "severity": "warn",
                    "message": f"Link {r['link_type']}: {r['source_project']}/{r['source_topic']} → {r['target_project']}/{r['target_topic']} (target topic missing)",
                })
        return issues
    except Exception:
        return []


def check_stale_topics(vault_dir: Path) -> list[dict]:
    """Find topic notes that have newer Q&A entries not yet reflected."""
    try:
        db_path = _find_db_for_vault(vault_dir)
        if not db_path:
            return []
        from obsitocin.search_db import ensure_schema, get_connection, get_stale_topics

        conn = get_connection(db_path)
        ensure_schema(conn)
        stale = get_stale_topics(conn)
        conn.close()
        return [
            {
                "project": s["project"],
                "topic": s["topic"],
                "last_updated": s["updated_at"],
                "latest_qa": s.get("latest_qa_timestamp", ""),
                "pending_qa_count": s.get("pending_qa_count", 0),
                "severity": "info",
                "message": (
                    f"{s['project']}/{s['topic']}: last updated {s['updated_at']}, "
                    f"{s.get('pending_qa_count', 0)} newer Q&A entries"
                ),
            }
            for s in stale
        ]
    except Exception:
        return []


def run_all_checks(vault_dir: Path, min_knowledge: int = 2) -> dict:
    """Run all lint checks. Returns summary dict with check results."""
    broken_wikilinks = check_broken_wikilinks(vault_dir)
    orphan_topics = check_orphan_topics(vault_dir)
    thin_notes = check_thin_notes(vault_dir, min_knowledge=min_knowledge)
    moc_consistency = check_moc_consistency(vault_dir)
    db_vault = check_db_vault_consistency(vault_dir)
    fts = check_fts_integrity(vault_dir)
    orphan_emb = check_orphan_embeddings(vault_dir)
    stale = check_stale_topics(vault_dir)
    orphan_links = check_orphan_links(vault_dir)

    total_issues = (
        len(broken_wikilinks)
        + len(orphan_topics)
        + len(thin_notes)
        + len(moc_consistency)
        + len(db_vault)
        + len(fts)
        + len(orphan_emb)
        + len(stale)
        + len(orphan_links)
    )

    return {
        "vault_dir": str(vault_dir),
        "total_issues": total_issues,
        "checks": {
            "broken_wikilinks": broken_wikilinks,
            "orphan_topics": orphan_topics,
            "thin_notes": thin_notes,
            "moc_consistency": moc_consistency,
            "db_vault_consistency": db_vault,
            "fts_integrity": fts,
            "orphan_embeddings": orphan_emb,
            "stale_topics": stale,
            "orphan_links": orphan_links,
        },
        "clean": total_issues == 0,
    }
