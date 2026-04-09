"""Scan AI agent session logs and queue them for processing.

Discovers session files from known CLI tool directories and converts
them to obsitocin queue format.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from obsitocin.config import QUEUE_DIR
from obsitocin.identity import compute_content_hash

# Known session log directories per agent
AGENT_SESSION_DIRS: dict[str, list[Path]] = {
    "claude_code": [
        Path.home() / ".claude" / "projects",
    ],
    "codex": [
        Path.home() / ".codex",
    ],
    "gemini": [
        Path.home() / ".gemini",
    ],
}


def _find_jsonl_files(base_dir: Path, max_depth: int = 4) -> list[Path]:
    """Recursively find .jsonl files up to max_depth."""
    results: list[Path] = []
    if not base_dir.exists():
        return results
    for p in base_dir.rglob("*.jsonl"):
        if len(p.relative_to(base_dir).parts) <= max_depth:
            results.append(p)
    return sorted(results)


def _extract_session_id(path: Path) -> str:
    return path.stem


def _parse_claude_code_session(path: Path) -> dict | None:
    """Parse a Claude Code project-level session JSONL."""
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return None

    if not entries:
        return None

    # Extract user prompts and assistant responses
    prompts: list[str] = []
    responses: list[str] = []
    for entry in entries:
        msg = entry.get("message", entry)
        role = msg.get("role", entry.get("type", ""))
        content = msg.get("content", "")

        if role in ("user", "human"):
            if isinstance(content, str) and content.strip():
                prompts.append(content.strip())
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    prompts.append("\n".join(texts))
        elif role == "assistant":
            if isinstance(content, str) and content.strip():
                responses.append(content.strip())
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                if texts:
                    responses.append("\n".join(texts))

    if not prompts and not responses:
        return None

    prompt = prompts[-1] if prompts else ""
    response = responses[-1] if responses else ""

    # Try to extract cwd from path (encoded in project dir name)
    cwd = ""
    parts = path.parts
    try:
        projects_idx = parts.index("projects")
        if projects_idx + 1 < len(parts):
            encoded = parts[projects_idx + 1]
            cwd = encoded.replace("-", "/")
            if not cwd.startswith("/"):
                cwd = "/" + cwd
    except (ValueError, IndexError):
        pass

    return {
        "prompt": prompt,
        "response": response,
        "cwd": cwd,
        "session_id": _extract_session_id(path),
        "transcript_path": str(path),
        "turn_count": len(prompts) + len(responses),
    }


def _parse_codex_session(path: Path) -> dict | None:
    """Parse a Codex CLI session log. Returns None if format unknown."""
    # Codex uses a similar JSONL format — try Claude-style parsing
    return _parse_claude_code_session(path)


def _parse_gemini_session(path: Path) -> dict | None:
    """Parse a Gemini CLI session log. Returns None if format unknown."""
    # Gemini may use a different format — try Claude-style as best effort
    return _parse_claude_code_session(path)


_PARSERS = {
    "claude_code": _parse_claude_code_session,
    "codex": _parse_codex_session,
    "gemini": _parse_gemini_session,
}


def _get_existing_session_ids() -> set[str]:
    """Get session IDs already in the queue or processed."""
    ids: set[str] = set()
    if not QUEUE_DIR.exists():
        return ids
    for f in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sid = data.get("session_id", "")
            if sid:
                ids.add(sid)
        except (json.JSONDecodeError, OSError):
            continue
    from obsitocin.config import PROCESSED_DIR

    if PROCESSED_DIR.exists():
        for f in PROCESSED_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                sid = data.get("session_id", "")
                if sid:
                    ids.add(sid)
            except (json.JSONDecodeError, OSError):
                continue
    return ids


def scan_sessions(
    source_type: str,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Scan and queue sessions from a given agent source.

    Returns {"scanned": int, "queued": int, "skipped": int, "errors": list[str]}
    """
    if source_type not in AGENT_SESSION_DIRS:
        return {
            "scanned": 0,
            "queued": 0,
            "skipped": 0,
            "errors": [f"Unknown source type: {source_type}. Supported: {', '.join(AGENT_SESSION_DIRS)}"],
        }

    parser = _PARSERS.get(source_type)
    if not parser:
        return {
            "scanned": 0,
            "queued": 0,
            "skipped": 0,
            "errors": [f"No parser for source type: {source_type}"],
        }

    existing_ids = _get_existing_session_ids()
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    scanned = 0
    queued = 0
    skipped = 0
    errors: list[str] = []

    for base_dir in AGENT_SESSION_DIRS[source_type]:
        for session_file in _find_jsonl_files(base_dir):
            if limit is not None and queued >= limit:
                break

            scanned += 1
            session_id = _extract_session_id(session_file)

            if session_id in existing_ids:
                skipped += 1
                continue

            try:
                parsed = parser(session_file)
            except Exception as e:
                errors.append(f"{session_file.name}: {e}")
                continue

            if not parsed:
                skipped += 1
                continue

            prompt = parsed.get("prompt", "")
            response = parsed.get("response", "")
            if not prompt and not response:
                skipped += 1
                continue

            if dry_run:
                queued += 1
                continue

            qa_entry = {
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "cwd": parsed.get("cwd", ""),
                "prompt": prompt,
                "response": response,
                "content_hash": compute_content_hash(prompt, response, parsed.get("cwd", "")),
                "status": "pending",
                "transcript_path": parsed.get("transcript_path", ""),
                "source_type": source_type,
                "source_metadata": {
                    "session_id": session_id,
                    "transcript_path": parsed.get("transcript_path", ""),
                    "turn_count": parsed.get("turn_count", 0),
                },
            }

            ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_file = QUEUE_DIR / f"{ts_slug}_{session_id}.json"
            out_file.write_text(json.dumps(qa_entry, ensure_ascii=False, indent=2))
            queued += 1

    return {
        "scanned": scanned,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
    }
