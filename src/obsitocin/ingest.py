from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from obsitocin.config import LOGS_DIR, OBS_DIR

LOG_FILE = LOGS_DIR / "ingest.log" if LOGS_DIR else Path("ingest.log")


def log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _fetch_url(url: str) -> str:
    req = Request(url, headers={"User-Agent": "obsitocin/1.0"})
    with urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "")
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        return resp.read().decode(encoding, errors="replace")


def _extract_text_from_html(html: str) -> str:
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:15000]


def _save_raw(content: str, source_name: str) -> Path | None:
    if OBS_DIR is None:
        return None

    from obsitocin.topic_writer import _raw_dir

    raw_dir = _raw_dir()
    if raw_dir is None:
        return None

    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "", source_name).strip()[:80] or "source"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_file = raw_dir / f"{timestamp}_{safe_name}.md"
    raw_file.write_text(content, encoding="utf-8")
    log(f"Saved raw: {raw_file.name}")
    return raw_file


def ingest_source(
    source: str,
    project: str | None = None,
    title: str | None = None,
) -> dict:
    if OBS_DIR is None:
        return {"success": False, "error": "Vault not configured"}

    is_url = source.startswith("http://") or source.startswith("https://")

    if is_url:
        try:
            raw_content = _fetch_url(source)
            if "<html" in raw_content.lower()[:500]:
                text_content = _extract_text_from_html(raw_content)
            else:
                text_content = raw_content[:15000]
        except (URLError, OSError) as e:
            return {"success": False, "error": f"Failed to fetch URL: {e}"}
        source_name = title or source.split("/")[-1].split("?")[0] or "web-source"
    else:
        source_path = Path(source).expanduser()
        if not source_path.exists():
            return {"success": False, "error": f"File not found: {source}"}
        try:
            raw_content = source_path.read_text(encoding="utf-8", errors="replace")
            text_content = raw_content[:15000]
        except OSError as e:
            return {"success": False, "error": f"Failed to read file: {e}"}
        source_name = title or source_path.stem

    raw_file = _save_raw(raw_content, source_name)

    if not project:
        project = Path(os.getcwd()).name

    from obsitocin.processor import (
        build_tagging_prompt,
        call_tagging,
        fallback_tagging_result,
        normalize_result,
    )
    from obsitocin.topic_writer import (
        append_work_log,
        update_moc,
        update_project_index,
        write_topic_note,
    )

    item = {
        "source_type": "manual",
        "content": text_content,
        "metadata": {
            "source_url": source if is_url else str(source),
            "source_name": source_name,
        },
        "project": project,
        "cwd": str(Path.cwd()),
    }
    prompt = build_tagging_prompt(item)

    try:
        result = call_tagging(prompt)
        if result is None:
            result = fallback_tagging_result(item)
        else:
            result = normalize_result(result)
    except Exception as e:
        log(f"LLM tagging failed for ingest: {e}")
        result = fallback_tagging_result(item)

    if result is None:
        result = fallback_tagging_result(item)

    source_page_path = write_topic_note(
        project=project,
        topic=source_name,
        new_knowledge=result.get("distilled_knowledge", []),
        work_summary=f"소스 수집: {source_name}",
        timestamp=datetime.now().isoformat(),
        tags=result.get("tags", []),
        importance=result.get("importance", 3),
        page_type="source",
    )

    topics_updated = 0
    for topic_entry in result.get("topics", []):
        name = topic_entry.get("name", "")
        knowledge = [k for k in topic_entry.get("knowledge", []) if k and k.strip()]
        if not name or not knowledge:
            continue
        write_topic_note(
            project=project,
            topic=name,
            new_knowledge=knowledge,
            work_summary=f"소스에서 추출: {source_name}",
            timestamp=datetime.now().isoformat(),
            tags=result.get("tags", []),
            importance=result.get("importance", 3),
            page_type="topic",
        )
        topics_updated += 1

    update_project_index(project)
    update_moc()

    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")
    work_log_topics = [
        topic_entry.get("name", "")
        for topic_entry in result.get("topics", [])
        if topic_entry.get("name")
    ]
    append_work_log(
        project,
        date_str,
        time_str,
        f"소스 수집: {source_name}",
        work_log_topics,
    )

    log(f"Ingested: {source_name} → {topics_updated} topics updated")

    return {
        "success": True,
        "raw_path": str(raw_file) if raw_file else None,
        "source_page": str(source_page_path) if source_page_path else None,
        "topics_updated": topics_updated,
    }
