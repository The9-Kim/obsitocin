import json
from pathlib import Path

from obsitocin.config import EMBEDDINGS_INDEX_PATH, PROCESSED_DIR, QUEUE_DIR
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

    if EMBEDDINGS_INDEX_PATH.exists():
        try:
            index = json.loads(EMBEDDINGS_INDEX_PATH.read_text())
            entries = index.get("entries", {})
            for file_id in sorted(entries):
                if file_id not in valid_processed_ids:
                    report["orphan_embeddings"].append(file_id)
        except Exception:
            report["processed_invalid"].append(EMBEDDINGS_INDEX_PATH.name)

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

    if EMBEDDINGS_INDEX_PATH.exists():
        try:
            index = json.loads(EMBEDDINGS_INDEX_PATH.read_text())
        except Exception:
            index = None
        if index is not None:
            entries = index.get("entries", {})
            valid_ids = {filepath.stem for filepath in PROCESSED_DIR.glob("*.json")}
            orphan_ids = sorted(
                file_id for file_id in entries if file_id not in valid_ids
            )
            result["orphan_embeddings"] = orphan_ids
            if orphan_ids and not dry_run:
                for file_id in orphan_ids:
                    entries.pop(file_id, None)
                index["entries"] = entries
                EMBEDDINGS_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False))

    return result
