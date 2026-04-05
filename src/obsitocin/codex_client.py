import json
import re
import shutil
import subprocess
from pathlib import Path

from obsitocin.config import CODEX_CLI_BIN, CODEX_MODEL

TAGGING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "tags",
        "category",
        "key_concepts",
        "memory_type",
        "importance",
    ],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "key_concepts": {"type": "array", "items": {"type": "string"}},
        "memory_type": {"type": "string", "enum": ["static", "dynamic"]},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
    },
}


def is_codex_configured() -> bool:
    if Path(CODEX_CLI_BIN).exists():
        return True
    return shutil.which(str(CODEX_CLI_BIN)) is not None


def require_codex_cli() -> str:
    if Path(CODEX_CLI_BIN).exists():
        return str(Path(CODEX_CLI_BIN))
    found = shutil.which(str(CODEX_CLI_BIN))
    if found:
        return found
    raise RuntimeError(
        "Codex CLI not found. Install it or set OBS_CODEX_CLI / codex_cli in config.json."
    )


def run_codex_prompt(prompt: str, timeout: int = 300) -> str:
    schema_path = Path("/tmp/obsitocin-codex-schema.json")
    schema_path.write_text(json.dumps(TAGGING_SCHEMA))

    cmd = [
        require_codex_cli(),
        "exec",
        "--full-auto",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema_path),
        "--model",
        CODEX_MODEL,
        prompt,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"Codex CLI exited {result.returncode}: {stderr[:500]}")

    stdout = (result.stdout or "").strip()
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", stdout, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return json.dumps({"response": json.dumps(parsed)})
        except json.JSONDecodeError:
            pass

    return stdout
