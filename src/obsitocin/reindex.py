"""Rebuild search.db from vault markdown files.

Parses topic notes and (optionally) processed QA files to reconstruct
the search database. This makes vault MD the true source of truth —
the DB can always be rebuilt from files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


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
        if line.strip().startswith("- ") and "아직 축적된" not in line
    ]


def reindex_from_vault(vault_dir: Path, db_path: Path) -> dict:
    """Rebuild search.db topic_note entries from vault markdown.

    Returns {"indexed": int, "errors": list[str]}
    """
    from obsitocin.embeddings import text_hash
    from obsitocin.search_db import ensure_schema, get_connection, upsert_qa_entry

    conn = get_connection(db_path)
    ensure_schema(conn)

    # Clear existing topic_note entries (will be rebuilt)
    conn.execute("DELETE FROM qa_entries WHERE source_type = 'topic_note'")

    indexed = 0
    errors: list[str] = []

    projects_dir = vault_dir / "projects"
    if not projects_dir.exists():
        conn.commit()
        conn.close()
        return {"indexed": 0, "errors": []}

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue

        for topic_file in sorted(topics_dir.glob("*.md")):
            try:
                content = topic_file.read_text(errors="replace")
                title = _extract_fm(content, "title", topic_file.stem)
                importance = int(_extract_fm(content, "importance", "3"))

                knowledge = _extract_bullet_section(content, "핵심 지식")
                full_text = f"{title}\n" + "\n".join(knowledge)

                file_id = f"topic:{project_dir.name}:{title}"
                metadata = {
                    "title": title,
                    "work_summary": f"주제 노트: {title}",
                    "category": "other",
                    "importance": importance,
                    "memory_type": "static",
                    "tags": [],
                    "key_concepts": [title],
                    "project": project_dir.name,
                    "timestamp": "",
                    "content_hash": "",
                    "embed_text_hash": text_hash(full_text),
                    "source_type": "topic_note",
                    "full_text": full_text,
                }
                upsert_qa_entry(conn, file_id, metadata)
                indexed += 1
            except Exception as e:
                errors.append(f"{topic_file.name}: {e}")

    conn.commit()
    conn.close()
    return {"indexed": indexed, "errors": errors}


def reindex_from_processed(processed_dir: Path, db_path: Path) -> dict:
    """Rebuild search.db QA entries from processed/*.json files.

    Returns {"indexed": int, "errors": list[str]}
    """
    from obsitocin.embeddings import qa_to_embed_text, text_hash
    from obsitocin.search_db import ensure_schema, get_connection, upsert_qa_entry

    conn = get_connection(db_path)
    ensure_schema(conn)

    # Clear existing non-topic entries
    conn.execute("DELETE FROM qa_entries WHERE source_type != 'topic_note'")

    indexed = 0
    errors: list[str] = []

    if not processed_dir.exists():
        conn.commit()
        conn.close()
        return {"indexed": 0, "errors": []}

    for f in sorted(processed_dir.glob("*.json")):
        if f.stem.endswith("_prompt"):
            continue
        try:
            qa = json.loads(f.read_text())
            if qa.get("status") not in ("processed", "written"):
                continue

            tagging = qa.get("tagging_result", {})
            file_id = f.stem
            metadata = {
                "title": tagging.get("title", ""),
                "work_summary": tagging.get("work_summary")
                or tagging.get("summary", ""),
                "category": tagging.get("category", "other"),
                "importance": tagging.get("importance", 3),
                "memory_type": tagging.get("memory_type", "dynamic"),
                "tags": tagging.get("tags", []),
                "key_concepts": tagging.get("key_concepts", []),
                "project": Path(qa.get("cwd", "")).name if qa.get("cwd") else "",
                "timestamp": qa.get("timestamp", ""),
                "content_hash": qa.get("content_hash", ""),
                "embed_text_hash": text_hash(qa_to_embed_text(qa)),
                "source_type": qa.get("source_type", "qa"),
                "full_text": qa.get("prompt", "") + "\n" + qa.get("response", ""),
            }
            upsert_qa_entry(conn, file_id, metadata)
            indexed += 1
        except Exception as e:
            errors.append(f"{f.name}: {e}")

    conn.commit()
    conn.close()
    return {"indexed": indexed, "errors": errors}


def reindex_all(
    vault_dir: Path, processed_dir: Path, db_path: Path, *, from_vault_only: bool = False
) -> dict:
    """Full reindex: vault topics + processed QAs.

    Args:
        from_vault_only: If True, only rebuild topic notes from vault (skip QA entries).

    Returns {"topics_indexed": int, "qas_indexed": int, "errors": list[str]}
    """
    vault_result = reindex_from_vault(vault_dir, db_path)

    qas_indexed = 0
    qa_errors: list[str] = []
    if not from_vault_only:
        qa_result = reindex_from_processed(processed_dir, db_path)
        qas_indexed = qa_result["indexed"]
        qa_errors = qa_result["errors"]

    return {
        "topics_indexed": vault_result["indexed"],
        "qas_indexed": qas_indexed,
        "errors": vault_result["errors"] + qa_errors,
    }
