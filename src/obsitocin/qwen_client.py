import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from obsitocin.config import (
    LLAMA_SERVER_BIN,
    LOGS_DIR,
    QWEN_CTX_SIZE,
    QWEN_MAX_TOKENS,
    QWEN_MODEL_PATH,
    QWEN_PORT,
    QWEN_TEMPERATURE,
    QWEN_TOP_K,
    QWEN_TOP_P,
)

_qwen_server_proc = None


def is_qwen_configured() -> bool:
    server_ok = Path(LLAMA_SERVER_BIN).exists() or shutil.which(str(LLAMA_SERVER_BIN))
    return bool(server_ok) and QWEN_MODEL_PATH != Path("") and QWEN_MODEL_PATH.exists()


def start_qwen_server() -> subprocess.Popen:
    global _qwen_server_proc
    if _qwen_server_proc is not None and _qwen_server_proc.poll() is None:
        return _qwen_server_proc

    server_bin = Path(LLAMA_SERVER_BIN)
    if not server_bin.exists():
        found = shutil.which(str(LLAMA_SERVER_BIN))
        if found:
            server_bin = Path(found)

    if not server_bin.exists():
        raise FileNotFoundError(
            f"llama-server not found: {LLAMA_SERVER_BIN}\n\n"
            "Install llama.cpp:\n"
            "  macOS: brew install llama.cpp\n"
            "  Linux: build from source (https://github.com/ggml-org/llama.cpp)\n\n"
            "Or set OBS_LLAMA_SERVER=/path/to/llama-server"
        )

    if QWEN_MODEL_PATH == Path("") or not QWEN_MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Qwen GGUF model not found: {QWEN_MODEL_PATH}\n\n"
            "Download the model:\n"
            "  pip install huggingface-hub\n"
            "  hf download unsloth/Qwen3.5-4B-GGUF --include '*Q4_K_M*'\n\n"
            "Or set OBS_QWEN_MODEL_PATH=/path/to/Qwen3.5-4B-Q4_K_M.gguf"
        )

    cmd = [
        str(server_bin),
        "--model",
        str(QWEN_MODEL_PATH),
        "--port",
        str(QWEN_PORT),
        "--ctx-size",
        str(QWEN_CTX_SIZE),
        "--n-gpu-layers",
        "99",
        "--chat-template-kwargs",
        '{"enable_thinking": false}',
    ]

    server_log = LOGS_DIR / "qwen_server.log"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_fh = open(server_log, "a")
    _qwen_server_proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT)

    health_url = f"http://127.0.0.1:{QWEN_PORT}/health"
    for _ in range(60):
        if _qwen_server_proc.poll() is not None:
            raise RuntimeError(
                f"qwen llama-server exited with code {_qwen_server_proc.returncode}. Check {server_log}"
            )
        try:
            response = urllib.request.urlopen(health_url, timeout=2)
            if response.status == 200:
                return _qwen_server_proc
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)

    stop_qwen_server()
    raise TimeoutError("Qwen llama-server failed to start within 60s")


def stop_qwen_server() -> None:
    global _qwen_server_proc
    if _qwen_server_proc is None:
        return
    if _qwen_server_proc.poll() is None:
        _qwen_server_proc.terminate()
        try:
            _qwen_server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _qwen_server_proc.kill()
            _qwen_server_proc.wait()
    _qwen_server_proc = None


def run_qwen_prompt(prompt: str, timeout: int = 180) -> str:
    import json as _json

    start_qwen_server()

    payload = _json.dumps(
        {
            "model": "qwen",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a knowledge extraction engine for a work knowledge base. "
                        "Analyze conversations and output structured JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": QWEN_MAX_TOKENS,
            "temperature": QWEN_TEMPERATURE,
            "top_p": QWEN_TOP_P,
            "top_k": QWEN_TOP_K,
        }
    ).encode("utf-8")

    url = f"http://127.0.0.1:{QWEN_PORT}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = _json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"] or ""
