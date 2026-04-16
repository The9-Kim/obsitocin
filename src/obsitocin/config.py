import json
import os
import shutil
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "obsitocin"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "obsitocin"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


_CONFIG = _load_config()
_VALIDATION_ERRORS: list[str] = []


def _add_validation_error(message: str) -> None:
    if message not in _VALIDATION_ERRORS:
        _VALIDATION_ERRORS.append(message)


def _get(env_key: str, config_key: str, default: str | Path) -> str:
    val = os.environ.get(env_key)
    if val:
        return val
    val = _CONFIG.get(config_key)
    if val:
        return str(val)
    return str(default)


def _get_bool(env_key: str, config_key: str, default: bool) -> bool:
    raw = os.environ.get(env_key)
    if raw is None:
        raw = _CONFIG.get(config_key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    _add_validation_error(
        f"Invalid boolean for {config_key!r}: {raw!r}. Falling back to {default}."
    )
    return default


def _get_int(
    env_key: str,
    config_key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.environ.get(env_key)
    if raw is None:
        raw = _CONFIG.get(config_key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        _add_validation_error(
            f"Invalid integer for {config_key!r}: {raw!r}. Falling back to {default}."
        )
        return default
    if minimum is not None and value < minimum:
        _add_validation_error(
            f"{config_key!r} must be >= {minimum}. Falling back to {default}."
        )
        return default
    if maximum is not None and value > maximum:
        _add_validation_error(
            f"{config_key!r} must be <= {maximum}. Falling back to {default}."
        )
        return default
    return value


def _get_float(
    env_key: str,
    config_key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.environ.get(env_key)
    if raw is None:
        raw = _CONFIG.get(config_key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _add_validation_error(
            f"Invalid float for {config_key!r}: {raw!r}. Falling back to {default}."
        )
        return default
    if minimum is not None and value < minimum:
        _add_validation_error(
            f"{config_key!r} must be >= {minimum}. Falling back to {default}."
        )
        return default
    if maximum is not None and value > maximum:
        _add_validation_error(
            f"{config_key!r} must be <= {maximum}. Falling back to {default}."
        )
        return default
    return value


def _path_value(value: str | Path) -> Path:
    return Path(str(value)).expanduser().resolve()


def _validate_optional_cli_path(config_key: str, cli_value: str) -> None:
    if not cli_value or cli_value == Path(cli_value).name:
        return
    if not _path_value(cli_value).exists():
        _add_validation_error(
            f"Configured path for {config_key!r} does not exist: {cli_value!r}."
        )


def _find_llama_server() -> str:
    direct = _get("OBS_LLAMA_SERVER", "llama_server", "llama-server")
    _validate_optional_cli_path("llama_server", direct)
    if Path(direct).exists():
        return str(_path_value(direct))
    found = shutil.which(direct)
    if found:
        return found
    return direct


def _find_embed_model() -> Path:
    direct = _get("OBS_EMBED_MODEL_PATH", "embed_model_path", "").strip()
    if direct:
        model_path = _path_value(direct)
        if not model_path.exists():
            _add_validation_error(
                f"Configured path for 'embed_model_path' does not exist: {direct!r}."
            )
        return model_path

    # Search in project models directory
    models_dir = DATA_DIR / "models"
    if models_dir.exists():
        for gguf in models_dir.rglob("*[Ee]mbed*.gguf"):
            return gguf

    # Search in huggingface cache
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_cache.exists():
        for gguf in hf_cache.rglob("*[Ee]mbed*.gguf"):
            return gguf
    return Path("")


def _find_qwen_model() -> Path:
    direct = _get("OBS_QWEN_MODEL_PATH", "qwen_model_path", "").strip()
    if direct:
        model_path = _path_value(direct)
        if not model_path.exists():
            _add_validation_error(
                f"Configured path for 'qwen_model_path' does not exist: {direct!r}."
            )
        return model_path

    # Search in project models directory
    models_dir = DATA_DIR / "models"
    if models_dir.exists():
        for gguf in models_dir.rglob("*Q4_K_M*.gguf"):
            return gguf

    # Search in huggingface cache
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_cache.exists():
        for gguf in hf_cache.rglob("*Q4_K_M*.gguf"):
            return gguf

    return Path("")


def _find_gemini_cli() -> str:
    direct = _get("OBS_GEMINI_CLI", "gemini_cli", "gemini")
    _validate_optional_cli_path("gemini_cli", direct)
    if Path(direct).exists():
        return direct
    found = shutil.which(direct)
    if found:
        return found
    return direct


def _find_claude_cli() -> str:
    direct = _get("OBS_CLAUDE_CLI", "claude_cli", "claude")
    _validate_optional_cli_path("claude_cli", direct)
    if Path(direct).exists():
        return direct
    found = shutil.which(direct)
    if found:
        return found
    return direct


def _find_codex_cli() -> str:
    direct = _get("OBS_CODEX_CLI", "codex_cli", "codex")
    _validate_optional_cli_path("codex_cli", direct)
    if Path(direct).exists():
        return direct
    found = shutil.which(direct)
    if found:
        return found
    return direct


DATA_DIR = _path_value(_get("OBS_DATA_DIR", "data_dir", DEFAULT_DATA_DIR))
QUEUE_DIR = _path_value(_get("OBS_QUEUE_DIR", "queue_dir", DATA_DIR / "queue"))
PROCESSED_DIR = _path_value(
    _get("OBS_PROCESSED_DIR", "processed_dir", DATA_DIR / "processed")
)
LOGS_DIR = _path_value(_get("OBS_LOGS_DIR", "logs_dir", DATA_DIR / "logs"))

_vault_dir_value = _get("OBS_VAULT_DIR", "vault_dir", "").strip()
VAULT_DIR = _path_value(_vault_dir_value) if _vault_dir_value else None
OBS_DIR = VAULT_DIR / "obsitocin" if VAULT_DIR else None

PARA_PROJECTS_DIR = OBS_DIR / "00-projects" if OBS_DIR else None
PARA_AREAS_DIR = OBS_DIR / "10-areas" if OBS_DIR else None
PARA_RESOURCES_DIR = OBS_DIR / "20-resources" if OBS_DIR else None
PARA_ARCHIVES_DIR = OBS_DIR / "30-archives" if OBS_DIR else None

SESSIONS_DIR = PARA_PROJECTS_DIR
CONCEPTS_DIR = PARA_RESOURCES_DIR / "concepts" if PARA_RESOURCES_DIR else None
TOPICS_DIR = PARA_RESOURCES_DIR / "topics" if PARA_RESOURCES_DIR else None
DAILY_DIR = PARA_ARCHIVES_DIR / "daily" if PARA_ARCHIVES_DIR else None

MOC_PATH = OBS_DIR / "_MOC.md" if OBS_DIR else None
PROFILE_PATH = OBS_DIR / "_Profile.md" if OBS_DIR else None

VALID_LLM_PROVIDERS = ("codex", "claude", "gemini", "qwen")
VALID_PII_RISK_LEVELS = ("low", "medium", "high")

LLM_PROVIDER = _get("OBS_LLM_PROVIDER", "llm_provider", "qwen").strip().lower()
if LLM_PROVIDER not in VALID_LLM_PROVIDERS:
    _add_validation_error(
        f"Invalid llm_provider {LLM_PROVIDER!r}. Falling back to 'claude'."
    )
    LLM_PROVIDER = "claude"

PII_ENABLED = _get_bool("OBS_PII_ENABLED", "pii_enabled", False)
PII_REDACT = _get_bool("OBS_PII_REDACT", "pii_redact", False)
PII_SKIP_SENSITIVE = _get_bool("OBS_PII_SKIP_SENSITIVE", "pii_skip_sensitive", False)
PII_RISK_THRESHOLD = (
    _get("OBS_PII_RISK_THRESHOLD", "pii_risk_threshold", "medium").strip().lower()
)
if PII_RISK_THRESHOLD not in VALID_PII_RISK_LEVELS:
    _add_validation_error(
        f"Invalid pii_risk_threshold {PII_RISK_THRESHOLD!r}. Falling back to 'medium'."
    )
    PII_RISK_THRESHOLD = "medium"

GEMINI_CLI_BIN = _find_gemini_cli()
GEMINI_MODEL = _get("OBS_GEMINI_MODEL", "gemini_model", "gemini-3-flash-preview")
CLAUDE_CLI_BIN = _find_claude_cli()
CLAUDE_MODEL = _get("OBS_CLAUDE_MODEL", "claude_model", "claude-haiku-4-5")
CODEX_CLI_BIN = _find_codex_cli()
CODEX_MODEL = _get("OBS_CODEX_MODEL", "codex_model", "gpt-5.4-nano")
LLAMA_SERVER_BIN = _find_llama_server()
QWEN_MODEL_PATH = _find_qwen_model()
QWEN_PORT = _get_int("OBS_QWEN_PORT", "qwen_port", 8199, minimum=1, maximum=65535)
QWEN_CTX_SIZE = _get_int("OBS_QWEN_CTX_SIZE", "qwen_ctx_size", 4096, minimum=1024)
QWEN_TEMPERATURE = _get_float(
    "OBS_QWEN_TEMPERATURE", "qwen_temperature", 0.7, minimum=0.0, maximum=2.0
)
QWEN_TOP_P = _get_float("OBS_QWEN_TOP_P", "qwen_top_p", 0.8, minimum=0.0, maximum=1.0)
QWEN_TOP_K = _get_int("OBS_QWEN_TOP_K", "qwen_top_k", 20, minimum=1)
QWEN_MAX_TOKENS = _get_int(
    "OBS_QWEN_MAX_TOKENS", "qwen_max_tokens", 512, minimum=64, maximum=4096
)
EMBED_MODEL_PATH = _find_embed_model()
EMBED_PORT = _get_int("OBS_EMBED_PORT", "embed_port", 8198, minimum=1, maximum=65535)

MAX_PROMPT_CHARS = _get_int(
    "OBS_MAX_PROMPT_CHARS",
    "max_prompt_chars",
    2500,
    minimum=200,
    maximum=20000,
)
MAX_RESPONSE_CHARS = _get_int(
    "OBS_MAX_RESPONSE_CHARS",
    "max_response_chars",
    2500,
    minimum=200,
    maximum=40000,
)
MAX_TOOL_CONTEXT_CHARS = _get_int(
    "OBS_MAX_TOOL_CONTEXT_CHARS",
    "max_tool_context_chars",
    500,
    minimum=100,
    maximum=10000,
)

EMBEDDINGS_INDEX_PATH = DATA_DIR / "embeddings.json"
SEARCH_DB_PATH = DATA_DIR / "search.db"

VALID_TOKENIZERS = ("unicode", "kiwi")
TOKENIZER = _get("OBS_TOKENIZER", "tokenizer", "unicode").strip().lower()
if TOKENIZER not in VALID_TOKENIZERS:
    _add_validation_error(
        f"Invalid tokenizer {TOKENIZER!r}. Falling back to 'unicode'."
    )
    TOKENIZER = "unicode"

GIT_AUTO_SYNC = _get_bool("OBS_GIT_AUTO_SYNC", "git_auto_sync", True)
GIT_REMOTE = _get("OBS_GIT_REMOTE", "git_remote", "origin").strip()

QUERY_EXPANSION = _get_bool("OBS_QUERY_EXPANSION", "query_expansion", False)


def get_config_validation_errors() -> list[str]:
    return list(_VALIDATION_ERRORS)
