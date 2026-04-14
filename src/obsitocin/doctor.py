"""System health diagnostics for obsitocin."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def _check_hook_registration() -> dict:
    """Check if Claude Code hooks are properly registered."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {"status": "warn", "message": "~/.claude/settings.json not found"}
    try:
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})

        def _has_obsitocin_hook(entries: list) -> bool:
            for entry in entries:
                # Flat format: {"command": "..."}
                if isinstance(entry, dict) and "obsitocin" in entry.get("command", ""):
                    return True
                # Nested format: {"hooks": [{"type": "command", "command": "..."}]}
                for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
                    if isinstance(hook, dict) and "obsitocin" in hook.get("command", ""):
                        return True
            return False

        has_submit = _has_obsitocin_hook(hooks.get("UserPromptSubmit", []))
        has_stop = _has_obsitocin_hook(hooks.get("Stop", []))
        if has_submit and has_stop:
            return {"status": "ok", "message": "UserPromptSubmit + Stop hooks registered"}
        missing = []
        if not has_submit:
            missing.append("UserPromptSubmit")
        if not has_stop:
            missing.append("Stop")
        return {"status": "warn", "message": f"Missing hooks: {', '.join(missing)}"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to read settings: {e}"}


def _check_llm_provider() -> dict:
    """Check configured LLM provider availability."""
    from obsitocin.config import LLM_PROVIDER
    try:
        from obsitocin.provider import get_provider
        provider = get_provider()
        if provider.is_configured():
            return {"status": "ok", "message": f"{LLM_PROVIDER}: configured ({provider.model})"}
        return {"status": "warn", "message": f"{LLM_PROVIDER}: not configured"}
    except Exception as e:
        return {"status": "error", "message": f"{LLM_PROVIDER}: {e}"}


def _check_llama_server() -> dict:
    """Check llama-server binary availability."""
    from obsitocin.config import LLAMA_SERVER_BIN
    found = Path(LLAMA_SERVER_BIN).exists() or shutil.which(str(LLAMA_SERVER_BIN))
    if found:
        return {"status": "ok", "message": f"llama-server: {found or LLAMA_SERVER_BIN}"}
    return {"status": "warn", "message": f"llama-server not found: {LLAMA_SERVER_BIN}"}


def _check_gguf_models() -> dict:
    """Check GGUF model file availability."""
    from obsitocin.config import EMBED_MODEL_PATH, QWEN_MODEL_PATH
    issues = []
    if QWEN_MODEL_PATH and QWEN_MODEL_PATH != Path("") and QWEN_MODEL_PATH.exists():
        gen_msg = f"generation: {QWEN_MODEL_PATH.name}"
    else:
        gen_msg = "generation: not found"
        issues.append("generation")
    if EMBED_MODEL_PATH and EMBED_MODEL_PATH != Path("") and EMBED_MODEL_PATH.exists():
        emb_msg = f"embedding: {EMBED_MODEL_PATH.name}"
    else:
        emb_msg = "embedding: not found"
        issues.append("embedding")
    status = "ok" if not issues else "warn"
    return {"status": status, "message": f"{gen_msg}, {emb_msg}"}


def _check_vault() -> dict:
    """Check vault directory status."""
    from obsitocin.config import OBS_DIR, VAULT_DIR
    if not VAULT_DIR:
        return {"status": "error", "message": "OBS_VAULT_DIR not set (run obsitocin init)"}
    if not VAULT_DIR.exists():
        return {"status": "error", "message": f"Vault directory missing: {VAULT_DIR}"}
    obs_dir = OBS_DIR
    if not obs_dir or not obs_dir.exists():
        return {"status": "warn", "message": f"obsitocin subdir missing in vault: {VAULT_DIR}"}
    git_dir = VAULT_DIR / ".git"
    git_status = "git repo" if git_dir.exists() else "no git"
    projects_dir = obs_dir / "projects"
    project_count = len(list(projects_dir.iterdir())) if projects_dir.exists() else 0
    return {
        "status": "ok",
        "message": f"{VAULT_DIR} ({git_status}, {project_count} projects)",
    }


def _check_search_db() -> dict:
    """Check search.db status."""
    from obsitocin.config import SEARCH_DB_PATH
    if not SEARCH_DB_PATH.exists():
        return {"status": "warn", "message": "search.db not found (run obsitocin migrate)"}
    try:
        from obsitocin.search_db import get_connection, get_db_stats, get_schema_version
        conn = get_connection(SEARCH_DB_PATH, readonly=True)
        version = get_schema_version(conn)
        stats = get_db_stats(conn)
        conn.close()
        return {
            "status": "ok",
            "message": (
                f"v{version}, {stats.get('entries', 0)} entries, "
                f"{stats.get('chunks', 0)} chunks, {stats.get('embeddings', 0)} embeddings"
            ),
        }
    except Exception as e:
        return {"status": "error", "message": f"search.db error: {e}"}


def _check_embedding_coverage() -> dict:
    """Check what percentage of entries have embeddings."""
    from obsitocin.config import SEARCH_DB_PATH
    if not SEARCH_DB_PATH.exists():
        return {"status": "warn", "message": "search.db not found"}
    try:
        from obsitocin.search_db import get_connection, get_db_stats
        conn = get_connection(SEARCH_DB_PATH, readonly=True)
        stats = get_db_stats(conn)
        conn.close()
        entries = stats.get("entries", 0)
        embeddings = stats.get("embeddings", 0)
        if entries == 0:
            return {"status": "ok", "message": "No entries yet"}
        pct = round(embeddings / entries * 100, 1) if entries else 0
        status = "ok" if pct >= 80 else "warn" if pct >= 50 else "error"
        return {"status": status, "message": f"{embeddings}/{entries} ({pct}%)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _check_queue_backlog() -> dict:
    """Check pending items in queue."""
    from obsitocin.config import QUEUE_DIR
    if not QUEUE_DIR.exists():
        return {"status": "ok", "message": "No queue directory"}
    pending = list(QUEUE_DIR.glob("*.json"))
    # Exclude prompt files
    pending = [f for f in pending if "_prompt" not in f.name]
    count = len(pending)
    status = "ok" if count < 10 else "warn" if count < 50 else "error"
    return {"status": status, "message": f"{count} pending"}


def _check_disk_usage() -> dict:
    """Check disk usage for data and vault directories."""
    from obsitocin.config import DATA_DIR, VAULT_DIR

    def _dir_size(d: Path) -> int:
        if not d or not d.exists():
            return 0
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    def _human(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    data_size = _dir_size(DATA_DIR)
    vault_size = _dir_size(VAULT_DIR) if VAULT_DIR else 0
    return {
        "status": "ok",
        "message": f"data: {_human(data_size)}, vault: {_human(vault_size)}",
    }


def _check_config() -> dict:
    """Check configuration validity."""
    from obsitocin.config import get_config_validation_errors
    errors = get_config_validation_errors()
    if not errors:
        return {"status": "ok", "message": "All settings valid"}
    return {"status": "warn", "message": f"{len(errors)} issue(s): {'; '.join(errors[:3])}"}


def run_doctor() -> dict:
    """Run all diagnostic checks. Returns structured report."""
    checks = {
        "hooks": _check_hook_registration(),
        "llm_provider": _check_llm_provider(),
        "llama_server": _check_llama_server(),
        "gguf_models": _check_gguf_models(),
        "vault": _check_vault(),
        "search_db": _check_search_db(),
        "embedding_coverage": _check_embedding_coverage(),
        "queue_backlog": _check_queue_backlog(),
        "disk_usage": _check_disk_usage(),
        "config": _check_config(),
    }

    statuses = [c["status"] for c in checks.values()]
    if "error" in statuses:
        overall = "error"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    return {"overall": overall, "checks": checks}


STATUS_ICONS = {"ok": "+", "warn": "!", "error": "x"}


def format_doctor_report(report: dict) -> str:
    """Format doctor report for terminal output."""
    lines = []
    overall = report["overall"]
    icon = STATUS_ICONS.get(overall, "?")
    lines.append(f"[{icon}] obsitocin doctor — {overall.upper()}\n")

    for name, check in report["checks"].items():
        icon = STATUS_ICONS.get(check["status"], "?")
        label = name.replace("_", " ").title()
        lines.append(f"  [{icon}] {label}: {check['message']}")

    return "\n".join(lines)
