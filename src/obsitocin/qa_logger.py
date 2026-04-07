#!/usr/bin/env python3
"""Hook handler for Claude Code: captures Q&A pairs to queue.

Reads JSON from stdin, handles UserPromptSubmit and Stop events.
Designed to be fast (file I/O only, no blocking calls).

Usage as hook: python3 -m obsitocin.qa_logger
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

from obsitocin.config import DATA_DIR, QUEUE_DIR, LOGS_DIR
from obsitocin.identity import compute_content_hash


def log(msg: str) -> None:
    """Append a log line with timestamp."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOGS_DIR / "qa_logger.log"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _strip_system_noise(text: str) -> str:
    import re

    # <system-reminder>...</system-reminder> 등 XML 블록
    text = re.sub(
        r"<(?:system-reminder|auto-slash-command|command-instruction|session-context|"
        r"Work_Context|omo-env|env|directories)>.*?</(?:system-reminder|auto-slash-command|"
        r"command-instruction|session-context|Work_Context|omo-env|env|directories)>",
        "",
        text,
        flags=re.DOTALL,
    )
    # [analyze-mode] ... --- 패턴
    text = re.sub(
        r"\[(?:analyze-mode|search-mode|auto-slash-command)\].*?---",
        "",
        text,
        flags=re.DOTALL,
    )
    # <!-- HTML comments -->
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # MANDATORY delegate_task ... 줄 단위 제거
    text = re.sub(
        r"^(?:MANDATORY delegate_task|Example: delegate_task).*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # 남은 고립 구분자 --- (앞뒤에 내용 없는 것)
    text = re.sub(r"^---\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


INTERNAL_PROMPT_MARKERS = (
    "You are a knowledge extraction engine for a work knowledge base.",
    "다음 대화를 분석하고 JSON으로만 응답하세요.",
)

INTERNAL_RESPONSE_MARKERS = (
    "<task-notification>",
    "Background command",
    "Run processor again",
)


def _contains_internal_obsitocin_prompt(text: str) -> bool:
    if not text:
        return False
    return all(marker in text for marker in INTERNAL_PROMPT_MARKERS)


def _contains_internal_obsitocin_response(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in INTERNAL_RESPONSE_MARKERS)


def _transcript_contains_internal_queue_operation(transcript_path: str) -> bool:
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"queue-operation"' in line or '"type": "queue-operation"' in line:
                    return True
    except OSError:
        return False
    return False


def handle_prompt_submit(data: dict) -> None:
    """Save prompt to a temporary file keyed by session_id."""
    session_id = data.get("session_id", "unknown")
    prompt = _strip_system_noise(data.get("prompt", ""))
    cwd = data.get("cwd", "")
    timestamp = datetime.now().isoformat()

    if not prompt.strip():
        log(f"Empty prompt from session {session_id}, skipping")
        return

    if _contains_internal_obsitocin_prompt(prompt):
        log(f"Internal obsitocin prompt for session {session_id}, skipping")
        return

    prompt_file = QUEUE_DIR / f"{session_id}_prompt.json"
    entry = {
        "session_id": session_id,
        "timestamp": timestamp,
        "cwd": cwd,
        "prompt": prompt,
    }

    # Append to list of prompts for this session
    existing = []
    if prompt_file.exists():
        try:
            existing = json.loads(prompt_file.read_text())
            if isinstance(existing, dict):
                existing = [existing]
        except (json.JSONDecodeError, Exception):
            existing = []

    existing.append(entry)
    prompt_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    log(f"Saved prompt for session {session_id} (#{len(existing)})")


MAX_WRITE_CONTENT_CHARS = 2000  # Max chars of Write content to inline


def _is_user_prompt(msg: dict) -> bool:
    """Check if a message is a user prompt (not a tool_result).

    Supports both legacy format ({role: "user", content: ...}) and
    new flat format ({type: "user", content: "..."}).
    """
    # New flat format: {type: "user", content: "..."}
    if msg.get("type") == "user" and "role" not in msg:
        content = msg.get("content", "")
        return isinstance(content, str) and bool(content.strip())
    # Legacy format: {role: "user", content: ...}
    if msg.get("role") != "user":
        return False
    content = msg.get("content", "")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" for b in content
        ) and not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def _extract_assistant_parts(msg: dict) -> list[str]:
    """Extract text and Write tool content from an assistant message."""
    parts: list[str] = []
    content = msg.get("content", "")
    if isinstance(content, str):
        if content.strip():
            parts.append(content.strip())
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                if isinstance(block, str) and block.strip():
                    parts.append(block.strip())
                continue
            if block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
            elif block.get("type") == "tool_use" and block.get("name") == "Write":
                inp = block.get("input", {})
                fpath = inp.get("file_path", "")
                code = inp.get("content", "")
                if fpath and code:
                    truncated = code[:MAX_WRITE_CONTENT_CHARS]
                    if len(code) > MAX_WRITE_CONTENT_CHARS:
                        truncated += "\n... (truncated)"
                    parts.append(f"[Created file: {fpath}]\n```\n{truncated}\n```")
    return parts


def _extract_response_from_rich_transcript(entries: list[dict]) -> str:
    """Extract response from rich transcript (project-specific, has type: assistant)."""
    last_prompt_idx = -1
    for i, entry in enumerate(entries):
        if entry.get("type") == "assistant":
            msg = entry.get("message", {})
            if _is_user_prompt(msg):
                last_prompt_idx = i
        else:
            msg = entry.get("message", entry)
            if _is_user_prompt(msg):
                last_prompt_idx = i

    if last_prompt_idx < 0:
        return ""

    parts: list[str] = []
    for entry in entries[last_prompt_idx + 1 :]:
        if entry.get("type") == "assistant":
            msg = entry.get("message", {})
            if msg.get("role") == "assistant":
                parts.extend(_extract_assistant_parts(msg))
        else:
            msg = entry.get("message", entry)
            if msg.get("role") == "assistant":
                parts.extend(_extract_assistant_parts(msg))

    return "\n\n".join(parts) if parts else ""


def _extract_response_from_flat_transcript(entries: list[dict]) -> str:
    """Extract response from flat/simplified transcript (tool_use/tool_result only).

    Reconstructs the assistant response from tool actions when there are
    no explicit assistant text entries.
    """
    # Find the last user prompt
    last_prompt_idx = -1
    for i, entry in enumerate(entries):
        if entry.get("type") == "user" and "role" not in entry:
            content = entry.get("content", "")
            if isinstance(content, str) and content.strip():
                last_prompt_idx = i

    if last_prompt_idx < 0:
        return ""

    parts: list[str] = []
    for entry in entries[last_prompt_idx + 1 :]:
        etype = entry.get("type", "")

        if etype == "tool_use":
            tool_name = entry.get("tool_name", "")
            tool_input = entry.get("tool_input", {})

            if tool_name.lower() == "write":
                fpath = tool_input.get("file_path", "")
                code = tool_input.get("content", "")
                if fpath and code:
                    truncated = code[:MAX_WRITE_CONTENT_CHARS]
                    if len(code) > MAX_WRITE_CONTENT_CHARS:
                        truncated += "\n... (truncated)"
                    parts.append(f"[Created file: {fpath}]\n```\n{truncated}\n```")
            elif tool_name.lower() == "edit":
                fpath = tool_input.get("file_path", "")
                old_s = tool_input.get("old_string", "")
                new_s = tool_input.get("new_string", "")
                if fpath:
                    parts.append(f"[Edited: {fpath}] {old_s[:60]}→{new_s[:60]}")

        elif etype == "tool_result":
            tool_name = entry.get("tool_name", "")
            output = entry.get("tool_output", {})
            if isinstance(output, dict):
                out_text = output.get("output", "")
            elif isinstance(output, str):
                out_text = output
            else:
                out_text = ""
            # Include meaningful bash output and text results
            if tool_name.lower() == "bash" and out_text.strip():
                parts.append(f"[bash output] {out_text[:500]}")

    return "\n\n".join(parts) if parts else ""


def extract_full_response(transcript_path: str) -> str:
    """Extract assistant response for the last turn from transcript JSONL.

    Supports three transcript formats:
    1. Legacy: {message: {role: "assistant", content: [...]}}
    2. Rich/project: {type: "assistant", message: {role: "assistant", ...}}
    3. Flat/simplified: {type: "tool_use"}, {type: "tool_result"} (no assistant text)
    """
    if not transcript_path:
        return ""
    try:
        entries: list[dict] = []
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if not entries:
            return ""

        # Detect format by checking entry types
        has_assistant_type = any(e.get("type") == "assistant" for e in entries)
        has_role_field = any(
            e.get("message", e).get("role") in ("user", "assistant") for e in entries
        )
        has_flat_types = any(
            e.get("type") in ("tool_use", "tool_result") and "message" not in e
            for e in entries
        )

        # Try rich format first (has type: "assistant" with message wrapper)
        if has_assistant_type:
            result = _extract_response_from_rich_transcript(entries)
            if result:
                return result

        # Try legacy format (has role field in message or entry)
        if has_role_field and not has_flat_types:
            last_prompt_idx = -1
            for i, entry in enumerate(entries):
                msg = entry.get("message", entry)
                if _is_user_prompt(msg):
                    last_prompt_idx = i

            if last_prompt_idx >= 0:
                parts: list[str] = []
                for entry in entries[last_prompt_idx + 1 :]:
                    msg = entry.get("message", entry)
                    if msg.get("role") == "assistant":
                        parts.extend(_extract_assistant_parts(msg))
                if parts:
                    return "\n\n".join(parts)

        # Flat/simplified format: reconstruct from tool actions
        if has_flat_types:
            result = _extract_response_from_flat_transcript(entries)
            if result:
                return result

        return ""
    except Exception as e:
        log(f"Failed to read transcript: {e}")
        return ""


def _resolve_transcript_path(session_id: str, hint: str = "", cwd: str = "") -> str:
    """Resolve transcript path, checking project-specific dirs first (richer format)."""
    if hint and Path(hint).exists():
        return hint

    # Check project-specific transcript (has assistant text entries)
    if cwd:
        encoded_cwd = cwd.replace("/", "-")
        project_candidate = (
            Path.home() / ".claude" / "projects" / encoded_cwd / f"{session_id}.jsonl"
        )
        if project_candidate.exists():
            return str(project_candidate)

    # Fallback: global transcripts dir (simplified format, no assistant text)
    candidate = Path.home() / ".claude" / "transcripts" / f"{session_id}.jsonl"
    if candidate.exists():
        return str(candidate)
    return hint or ""


def handle_stop(data: dict) -> None:
    """Merge last assistant message with most recent prompt, create Q&A pair."""
    session_id = data.get("session_id", "unknown")
    stop_hook_active = data.get("stop_hook_active", False)

    # Prevent infinite loops
    if stop_hook_active:
        log(f"stop_hook_active=True for session {session_id}, skipping")
        return

    raw_transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")
    transcript_path = _resolve_transcript_path(session_id, raw_transcript_path, cwd)
    if transcript_path and not raw_transcript_path:
        log(f"Resolved transcript via fallback: {transcript_path}")

    response = extract_full_response(transcript_path)
    if not response:
        response = data.get("last_assistant_message", "")
    timestamp = datetime.now().isoformat()

    prompt_file = QUEUE_DIR / f"{session_id}_prompt.json"

    if (
        _contains_internal_obsitocin_response(response)
        or _transcript_contains_internal_queue_operation(transcript_path)
    ):
        log(f"Internal obsitocin stop event for session {session_id}, skipping")
        prompt_file.unlink(missing_ok=True)
        return

    if not prompt_file.exists():
        log(f"No prompt file for session {session_id}, saving response-only")
        qa_entry = {
            "session_id": session_id,
            "timestamp": timestamp,
            "cwd": cwd,
            "prompt": "",
            "response": response,
            "content_hash": compute_content_hash("", response, cwd),
            "status": "pending",
            "transcript_path": transcript_path,
            "source_type": "claude_code",
            "source_metadata": {
                "session_id": session_id,
                "transcript_path": transcript_path,
            },
        }
        ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = QUEUE_DIR / f"{ts_slug}_{session_id}.json"
        out_file.write_text(json.dumps(qa_entry, ensure_ascii=False, indent=2))
        return

    # Read the most recent prompt
    try:
        prompts = json.loads(prompt_file.read_text())
        if isinstance(prompts, dict):
            prompts = [prompts]
    except (json.JSONDecodeError, Exception):
        log(f"Failed to read prompt file for session {session_id}")
        prompts = []

    if not prompts:
        log(f"Empty prompts list for session {session_id}")
        prompt_file.unlink(missing_ok=True)
        return

    # Take the last prompt entry
    last_prompt = prompts[-1]
    if _contains_internal_obsitocin_prompt(last_prompt.get("prompt", "")):
        log(f"Internal obsitocin prompt file for session {session_id}, skipping")
        prompt_file.unlink(missing_ok=True)
        return

    # Create the Q&A pair
    qa_entry = {
        "session_id": session_id,
        "timestamp": last_prompt.get("timestamp", timestamp),
        "cwd": last_prompt.get("cwd", cwd),
        "prompt": last_prompt.get("prompt", ""),
        "response": response,
        "content_hash": compute_content_hash(
            last_prompt.get("prompt", ""),
            response,
            last_prompt.get("cwd", cwd),
        ),
        "status": "pending",
        "transcript_path": transcript_path,
        "source_type": "claude_code",
        "source_metadata": {
            "session_id": session_id,
            "transcript_path": transcript_path,
        },
    }

    ts_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = QUEUE_DIR / f"{ts_slug}_{session_id}.json"
    out_file.write_text(json.dumps(qa_entry, ensure_ascii=False, indent=2))
    log(f"Created Q&A pair: {out_file.name}")

    # Clean up prompt file
    prompt_file.unlink(missing_ok=True)

    # Trigger background processing
    trigger_processor()


def trigger_processor() -> None:
    """Launch tagging_processor in background if not already running.

    Uses a lock file to prevent duplicate runs.
    """
    import subprocess
    import fcntl

    lock_file = DATA_DIR / "processor.lock"
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            log("Processor already running, skipping trigger")
            return

        # Release the lock — the subprocess will acquire its own
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

        # Launch processor in background
        subprocess.Popen(
            [sys.executable, "-m", "obsitocin.processor"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log("Triggered background processor")
    except Exception as e:
        log(f"Failed to trigger processor: {e}")


def main() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        log(f"Failed to parse stdin: {e}")
        sys.exit(0)  # Exit 0 to not block Claude Code

    event = data.get("hook_event_name", "")
    log(f"Received event: {event} (session: {data.get('session_id', 'unknown')})")

    if event == "UserPromptSubmit":
        handle_prompt_submit(data)
    elif event == "Stop":
        handle_stop(data)
    else:
        log(f"Unknown event: {event}")

    # Always exit 0 to not interfere with Claude Code
    sys.exit(0)


if __name__ == "__main__":
    main()
