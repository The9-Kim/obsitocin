import json
import shutil
import subprocess
from pathlib import Path

from obsitocin.config import CLAUDE_CLI_BIN, CLAUDE_MODEL


def is_claude_configured() -> bool:
    if Path(CLAUDE_CLI_BIN).exists():
        return True
    return shutil.which(str(CLAUDE_CLI_BIN)) is not None


def require_claude_cli() -> str:
    if Path(CLAUDE_CLI_BIN).exists():
        return str(Path(CLAUDE_CLI_BIN))
    found = shutil.which(str(CLAUDE_CLI_BIN))
    if found:
        return found
    raise RuntimeError(
        "Claude CLI not found. Install it or set OBS_CLAUDE_CLI / claude_cli in config.json."
    )


def run_claude_prompt(prompt: str, timeout: int = 180) -> str:
    cmd = [
        require_claude_cli(),
        "--print",
        "--model",
        CLAUDE_MODEL,
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Claude CLI exited {result.returncode}: {stderr[:500]}")

    stdout = (result.stdout or "").strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout

    if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
        return parsed["response"]
    return stdout
