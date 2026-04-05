"""Manage Claude Code hooks registration for obsitocin.

Reads/writes ~/.claude/settings.json to add or remove obsitocin hooks
while preserving existing user hooks.
"""

import json
import os
import shlex
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"

# Marker to identify hooks managed by obsitocin
OBS_MARKER = "obsitocin"

_existing_pythonpath = os.environ.get("PYTHONPATH", "").strip()
_pythonpath_parts = [str(PROJECT_SRC)]
if _existing_pythonpath:
    _pythonpath_parts.append(_existing_pythonpath)


def build_hook_command(python_executable: str | None = None) -> str:
    python_bin = python_executable or sys.executable
    return (
        f"PYTHONPATH={shlex.quote(':'.join(_pythonpath_parts))} "
        f"{shlex.quote(python_bin)} -m obsitocin.qa_logger"
    )


HOOK_COMMAND = build_hook_command()


def build_hooks_config(python_executable: str | None = None) -> dict:
    hook_command = build_hook_command(python_executable)
    return {
        "UserPromptSubmit": {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command,
                    "description": f"[{OBS_MARKER}] Capture user prompts",
                }
            ],
        },
        "Stop": {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command,
                    "description": f"[{OBS_MARKER}] Capture Q&A pairs",
                }
            ],
        },
    }


HOOKS_CONFIG = build_hooks_config()


def _load_settings() -> dict:
    """Load Claude Code settings, creating file if needed."""
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_settings(settings: dict) -> None:
    """Save Claude Code settings."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def _is_obs_matcher_group(group: dict) -> bool:
    """Check if a matcher group belongs to obsitocin.

    Also detects legacy flat-format hooks ({type, command, description})
    so they can be cleaned up during unregister.
    """
    # New format: matcher group with hooks array
    for hook in group.get("hooks", []):
        desc = hook.get("description", "")
        cmd = hook.get("command", "")
        if OBS_MARKER in desc or "obsitocin" in cmd:
            return True
    # Legacy flat format: {type, command, description} without hooks array
    if "hooks" not in group:
        desc = group.get("description", "")
        cmd = group.get("command", "")
        if OBS_MARKER in desc or "obsitocin" in cmd:
            return True
    return False


def register_hooks(python_executable: str | None = None) -> bool:
    """Register obsitocin hooks in Claude Code settings.

    Returns True if hooks were added, False if already present.
    """
    settings = _load_settings()
    hooks = settings.setdefault("hooks", {})
    changed = False
    hooks_config = build_hooks_config(python_executable)

    for event_name, hook_config in hooks_config.items():
        event_hooks = hooks.setdefault(event_name, [])
        non_obs = [h for h in event_hooks if not _is_obs_matcher_group(h)]
        desired = non_obs + [hook_config]
        if event_hooks != desired:
            hooks[event_name] = desired
            changed = True

    if changed:
        _save_settings(settings)

    return changed


def unregister_hooks() -> bool:
    """Remove obsitocin hooks from Claude Code settings.

    Returns True if hooks were removed, False if none found.
    """
    settings = _load_settings()
    hooks = settings.get("hooks", {})
    changed = False

    for event_name in list(hooks.keys()):
        original = hooks[event_name]
        filtered = [h for h in original if not _is_obs_matcher_group(h)]
        if len(filtered) != len(original):
            hooks[event_name] = filtered
            changed = True
        # Clean up empty arrays
        if not hooks[event_name]:
            del hooks[event_name]

    if changed:
        if not hooks:
            settings.pop("hooks", None)
        _save_settings(settings)

    return changed


def check_hooks() -> dict[str, bool]:
    """Check which obsitocin hooks are currently registered.

    Returns dict mapping event name to registration status.
    """
    settings = _load_settings()
    hooks = settings.get("hooks", {})

    status = {}
    for event_name in build_hooks_config():
        event_hooks = hooks.get(event_name, [])
        status[event_name] = any(_is_obs_matcher_group(h) for h in event_hooks)

    return status
