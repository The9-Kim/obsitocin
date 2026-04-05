#!/usr/bin/env python3

"""Embedding engine using a dedicated GGUF embedding model via llama-server."""

import hashlib
import json
import math
import subprocess
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from obsitocin.config import (
    EMBED_MODEL_PATH,
    EMBED_PORT,
    EMBEDDINGS_INDEX_PATH,
    LLAMA_SERVER_BIN,
    LOGS_DIR,
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
)

LOG_FILE = LOGS_DIR / "embeddings.log"

_embed_server_proc = None


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_configured() -> bool:
    return EMBED_MODEL_PATH != Path("") and EMBED_MODEL_PATH.exists()


def start_embed_server() -> subprocess.Popen:
    global _embed_server_proc
    if _embed_server_proc is not None and _embed_server_proc.poll() is None:
        return _embed_server_proc

    if not is_configured():
        raise FileNotFoundError(
            f"Embedding model not found: {EMBED_MODEL_PATH}\n\n"
            "Download an embedding model:\n"
            "  pip install huggingface-hub\n"
            "  hf download Qwen/Qwen3-Embedding-0.6B-GGUF \\\n"
            "    --include '*Q8_0*' \\\n"
            "    --local-dir ~/.local/share/obsitocin/models/Qwen3-Embedding-0.6B-GGUF\n\n"
            "Or set OBS_EMBED_MODEL_PATH=/path/to/embedding-model.gguf"
        )

    server_bin = Path(LLAMA_SERVER_BIN)
    if not server_bin.exists():
        found = shutil.which(str(LLAMA_SERVER_BIN))
        if found:
            server_bin = Path(found)

    cmd = [
        str(server_bin),
        "--model",
        str(EMBED_MODEL_PATH),
        "--port",
        str(EMBED_PORT),
        "--ctx-size",
        "8192",
        "--n-gpu-layers",
        "99",
        "--embeddings",
    ]
    log(f"Starting embedding server: {' '.join(cmd)}")

    server_log = LOGS_DIR / "embed_server.log"
    log_fh = open(server_log, "a")
    _embed_server_proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT)

    health_url = f"http://127.0.0.1:{EMBED_PORT}/health"
    for i in range(60):
        if _embed_server_proc.poll() is not None:
            raise RuntimeError(
                f"Embedding server exited with code {_embed_server_proc.returncode}. "
                f"Check {server_log}"
            )
        try:
            response = urllib.request.urlopen(health_url, timeout=2)
            if response.status == 200:
                log(f"Embedding server ready after {i + 1}s")
                return _embed_server_proc
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)

    stop_embed_server()
    raise TimeoutError("Embedding server failed to start within 60s")


def stop_embed_server() -> None:
    global _embed_server_proc
    if _embed_server_proc is None:
        return
    if _embed_server_proc.poll() is None:
        _embed_server_proc.terminate()
        try:
            _embed_server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _embed_server_proc.kill()
            _embed_server_proc.wait()
    _embed_server_proc = None
    log("Embedding server stopped, VRAM released")


def _embedding_request(payload: dict) -> dict:
    url = f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=120)
    return json.loads(resp.read().decode("utf-8"))


def get_embedding(text: str) -> list[float]:
    body = _embedding_request({"model": "embedding", "input": text[:4000]})
    return body["data"][0]["embedding"]


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    truncated = [text[:4000] for text in texts]
    body = _embedding_request({"model": "embedding", "input": truncated})
    sorted_data = sorted(body["data"], key=lambda item: item["index"])
    return [item["embedding"] for item in sorted_data]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def qa_to_embed_text(qa: dict) -> str:
    tagging = qa.get("tagging_result", {})
    title = tagging.get("title", "")
    summary = tagging.get("summary", "")
    tags = " ".join(tagging.get("tags", []))
    concepts = " ".join(tagging.get("key_concepts", []))
    prompt = qa.get("prompt", "")[:MAX_PROMPT_CHARS]
    response = qa.get("response", "")[:MAX_RESPONSE_CHARS]
    return f"{title}\n{summary}\n{tags} {concepts}\n{prompt}\n{response}"


def topic_note_to_embed_text(note_path: Path) -> str:
    """Extract title + 핵심 지식 from a topic note .md for embedding."""
    try:
        content = note_path.read_text(errors="replace")
    except OSError:
        return ""

    import re

    title_m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else note_path.stem

    project_m = re.search(r"^project:\s*(.+)$", content, re.MULTILINE)
    project = project_m.group(1).strip() if project_m else ""

    pattern = r"## 핵심 지식\s*\n((?:.*\n)*?)(?=\n## |\Z)"
    match = re.search(pattern, content)
    knowledge_items: list[str] = []
    if match:
        for line in match.group(1).strip().split("\n"):
            stripped = line.lstrip("- ").strip()
            if stripped:
                knowledge_items.append(stripped)

    parts = [f"{project} {title}" if project else title]
    parts.extend(knowledge_items[:10])
    return "\n".join(parts)


def embed_topic_notes(vault_dir: Path) -> int:
    """Embed all topic notes under vault/projects/*/topics/. Returns count of newly embedded."""
    projects_dir = vault_dir / "projects"
    if not projects_dir.exists():
        return 0

    notes: list[tuple[str, Path]] = []
    import re

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue
        for f in sorted(topics_dir.glob("*.md")):
            try:
                content = f.read_text(errors="replace")
            except OSError:
                continue
            title_m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else f.stem
            key = f"topic:{project_dir.name}:{title}"
            notes.append((key, f))

    if not notes:
        return 0

    index = load_index()
    entries = index.get("entries", {})

    to_embed: list[tuple[str, str]] = []
    for key, note_path in notes:
        embed_text = topic_note_to_embed_text(note_path)
        t_hash = text_hash(embed_text)
        existing = entries.get(key)
        if existing and existing.get("text_hash") == t_hash:
            continue
        to_embed.append((key, embed_text))

    if not to_embed:
        log("All topic notes already embedded, skipping")
        return 0

    log(f"Generating embeddings for {len(to_embed)} topic notes")
    texts = [text for _key, text in to_embed]
    try:
        embeddings = get_embeddings_batch(texts)
    except Exception as error:
        log(f"Batch topic embedding failed, falling back: {error}")
        embeddings = []
        for text in texts:
            try:
                embeddings.append(get_embedding(text))
            except Exception:
                embeddings.append([])

    updated = 0
    for i, (key, embed_text) in enumerate(to_embed):
        if i < len(embeddings) and embeddings[i]:
            entries[key] = {
                "embedding": embeddings[i],
                "text_hash": text_hash(embed_text),
                "created_at": datetime.now().isoformat(),
                "source_type": "topic_note",
            }
            if not index.get("dimensions"):
                index["dimensions"] = len(embeddings[i])
            updated += 1

    index["entries"] = entries
    save_index(index)
    return updated


def load_index() -> dict:
    if EMBEDDINGS_INDEX_PATH.exists():
        try:
            return json.loads(EMBEDDINGS_INDEX_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"model": "", "dimensions": 0, "entries": {}}


def save_index(index: dict) -> None:
    EMBEDDINGS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False))
    log(f"Saved embeddings index: {len(index.get('entries', {}))} entries")


def build_embeddings_for_qas(qa_files: list[tuple[str, dict]]) -> int:
    index = load_index()
    entries = index.get("entries", {})
    to_embed: list[tuple[str, dict, str]] = []

    for file_id, qa in qa_files:
        embed_text = qa_to_embed_text(qa)
        t_hash = text_hash(embed_text)
        existing = entries.get(file_id)
        if existing and existing.get("text_hash") == t_hash:
            continue
        to_embed.append((file_id, qa, embed_text))

    if not to_embed:
        log("All Q&As already embedded, skipping")
        return 0

    log(f"Generating embeddings for {len(to_embed)} Q&A pairs")
    texts = [text for _file_id, _qa, text in to_embed]
    try:
        embeddings = get_embeddings_batch(texts)
    except Exception as error:
        log(f"Batch embedding failed, falling back to individual: {error}")
        embeddings = []
        for text in texts:
            try:
                embeddings.append(get_embedding(text))
            except Exception as nested_error:
                log(f"Individual embedding failed: {nested_error}")
                embeddings.append([])

    updated = 0
    for i, (file_id, _qa, embed_text) in enumerate(to_embed):
        if i < len(embeddings) and embeddings[i]:
            entries[file_id] = {
                "embedding": embeddings[i],
                "text_hash": text_hash(embed_text),
                "created_at": datetime.now().isoformat(),
            }
            if not index.get("dimensions"):
                index["dimensions"] = len(embeddings[i])
            updated += 1

    index["model"] = (
        EMBED_MODEL_PATH.stem if EMBED_MODEL_PATH != Path("") else "unknown"
    )
    index["entries"] = entries
    save_index(index)
    return updated
