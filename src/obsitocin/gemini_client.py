import json
import shutil
import subprocess
from pathlib import Path

from obsitocin.config import GEMINI_CLI_BIN, GEMINI_MODEL


def is_gemini_configured() -> bool:
    if Path(GEMINI_CLI_BIN).exists():
        return True
    return shutil.which(str(GEMINI_CLI_BIN)) is not None


def require_gemini_cli() -> str:
    if Path(GEMINI_CLI_BIN).exists():
        return str(Path(GEMINI_CLI_BIN))
    found = shutil.which(str(GEMINI_CLI_BIN))
    if found:
        return found
    raise RuntimeError(
        "Gemini CLI not found. Install it or set OBS_GEMINI_CLI / gemini_cli in config.json."
    )


def run_gemini_prompt(prompt: str, timeout: int = 180) -> str:
    cmd = [
        require_gemini_cli(),
        "--model",
        GEMINI_MODEL,
        "--prompt",
        prompt,
        "--output-format",
        "json",
        "--approval-mode",
        "yolo",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Gemini CLI exited {result.returncode}: {stderr[:500]}")

    stdout = (result.stdout or "").strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout

    if isinstance(parsed, dict) and isinstance(parsed.get("response"), str):
        return parsed["response"]
    return stdout
