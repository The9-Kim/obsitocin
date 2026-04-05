from dataclasses import dataclass
from typing import Callable

from obsitocin.config import (
    CLAUDE_CLI_BIN,
    CLAUDE_MODEL,
    CODEX_CLI_BIN,
    CODEX_MODEL,
    GEMINI_CLI_BIN,
    GEMINI_MODEL,
    LLM_PROVIDER,
    LLAMA_SERVER_BIN,
    QWEN_MODEL_PATH,
    VALID_LLM_PROVIDERS,
)


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    cli_bin: str
    model: str
    run_prompt: Callable[[str, int], str]
    is_configured: Callable[[], bool]


def _get_codex_info(override_provider: str | None = None) -> ProviderInfo:
    from obsitocin.codex_client import (
        is_codex_configured,
        run_codex_prompt,
    )

    return ProviderInfo(
        name="codex",
        cli_bin=str(CODEX_CLI_BIN),
        model=CODEX_MODEL,
        run_prompt=run_codex_prompt,
        is_configured=is_codex_configured,
    )


def _get_claude_info(override_provider: str | None = None) -> ProviderInfo:
    from obsitocin.claude_client import (
        is_claude_configured,
        run_claude_prompt,
    )

    return ProviderInfo(
        name="claude",
        cli_bin=str(CLAUDE_CLI_BIN),
        model=CLAUDE_MODEL,
        run_prompt=run_claude_prompt,
        is_configured=is_claude_configured,
    )


def _get_gemini_info(override_provider: str | None = None) -> ProviderInfo:
    from obsitocin.gemini_client import (
        is_gemini_configured,
        run_gemini_prompt,
    )

    return ProviderInfo(
        name="gemini",
        cli_bin=str(GEMINI_CLI_BIN),
        model=GEMINI_MODEL,
        run_prompt=run_gemini_prompt,
        is_configured=is_gemini_configured,
    )


def _get_qwen_info(override_provider: str | None = None) -> ProviderInfo:
    from obsitocin.qwen_client import is_qwen_configured, run_qwen_prompt

    return ProviderInfo(
        name="qwen",
        cli_bin=str(LLAMA_SERVER_BIN),
        model=QWEN_MODEL_PATH.name
        if QWEN_MODEL_PATH != QWEN_MODEL_PATH.__class__("")
        else "qwen",
        run_prompt=run_qwen_prompt,
        is_configured=is_qwen_configured,
    )


_PROVIDER_REGISTRY: dict[str, Callable[[str | None], ProviderInfo]] = {
    "codex": _get_codex_info,
    "claude": _get_claude_info,
    "gemini": _get_gemini_info,
    "qwen": _get_qwen_info,
}


def get_provider(name: str | None = None) -> ProviderInfo:
    provider_name = (name or LLM_PROVIDER).strip().lower()
    if provider_name not in _PROVIDER_REGISTRY:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Valid options: {', '.join(VALID_LLM_PROVIDERS)}"
        )
    return _PROVIDER_REGISTRY[provider_name](provider_name)


def run_provider_prompt(
    prompt: str,
    provider_name: str | None = None,
    timeout: int = 300,
) -> str:
    provider = get_provider(provider_name)
    return provider.run_prompt(prompt, timeout)
