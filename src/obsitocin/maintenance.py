import json
from pathlib import Path

from obsitocin.config import PROCESSED_DIR, QUEUE_DIR, SEARCH_DB_PATH
from obsitocin.processor import ORPHAN_MAX_AGE_SECONDS


def verify_state() -> dict:
    report = {
        "queue_invalid": [],
        "processed_invalid": [],
        "missing_tagging_result": [],
        "missing_content_hash": [],
        "orphan_embeddings": [],
        "duplicate_content_hashes": {},
    }

    seen_hashes: dict[str, list[str]] = {}
    for filepath in sorted(QUEUE_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except Exception:
            report["queue_invalid"].append(filepath.name)
            continue
        if filepath.stem.endswith("_prompt"):
            continue
        content_hash = str(qa.get("content_hash", "")).strip()
        if not content_hash:
            report["missing_content_hash"].append(filepath.name)

    valid_processed_ids: set[str] = set()
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except Exception:
            report["processed_invalid"].append(filepath.name)
            continue
        valid_processed_ids.add(filepath.stem)
        status = qa.get("status")
        content_hash = str(qa.get("content_hash", "")).strip()
        if not content_hash:
            report["missing_content_hash"].append(filepath.name)
        else:
            seen_hashes.setdefault(content_hash, []).append(filepath.name)
        if status in {"processed", "written"} and not isinstance(
            qa.get("tagging_result"), dict
        ):
            report["missing_tagging_result"].append(filepath.name)

    report["duplicate_content_hashes"] = {
        content_hash: files
        for content_hash, files in seen_hashes.items()
        if len(files) > 1
    }

    try:
        from obsitocin.search_db import ensure_schema, get_connection

        conn = get_connection(SEARCH_DB_PATH)
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT DISTINCT e.file_id
            FROM qa_entries e
            JOIN chunks c ON e.file_id = c.file_id
            JOIN embeddings emb ON c.chunk_id = emb.chunk_id
            WHERE e.source_type != 'topic_note'
            ORDER BY e.file_id
            """
        ).fetchall()
        for row in rows:
            file_id = row[0]
            if file_id not in valid_processed_ids:
                report["orphan_embeddings"].append(file_id)
        conn.close()
    except Exception:
        pass

    return report


def cleanup_state(*, dry_run: bool = False) -> dict:
    result = {
        "orphan_prompts": [],
        "orphan_embeddings": [],
    }

    import time

    current_time = time.time()
    for filepath in sorted(QUEUE_DIR.glob("*_prompt.json")):
        age = current_time - filepath.stat().st_mtime
        if age <= ORPHAN_MAX_AGE_SECONDS:
            continue
        result["orphan_prompts"].append(filepath.name)
        if not dry_run:
            filepath.unlink(missing_ok=True)

    try:
        from obsitocin.search_db import delete_qa_entry, ensure_schema, get_connection

        conn = get_connection(SEARCH_DB_PATH)
        ensure_schema(conn)
        valid_ids = {filepath.stem for filepath in PROCESSED_DIR.glob("*.json")}
        rows = conn.execute(
            """
            SELECT DISTINCT e.file_id
            FROM qa_entries e
            JOIN chunks c ON e.file_id = c.file_id
            JOIN embeddings emb ON c.chunk_id = emb.chunk_id
            WHERE e.source_type != 'topic_note'
            ORDER BY e.file_id
            """
        ).fetchall()
        orphan_ids = sorted(row[0] for row in rows if row[0] not in valid_ids)
        result["orphan_embeddings"] = orphan_ids
        if orphan_ids and not dry_run:
            for file_id in orphan_ids:
                delete_qa_entry(conn, file_id)
            conn.commit()
        conn.close()
    except Exception:
        pass

    return result
