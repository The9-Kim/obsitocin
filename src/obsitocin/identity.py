import hashlib
import json


def compute_source_hash(source_type: str, content: str, metadata: dict) -> str:
    """Compute content hash for any source type.

    Args:
        source_type: Source identifier (e.g., "claude_code", "slack")
        content: Content to hash
        metadata: Additional metadata dict (keys will be sorted for determinism)

    Returns:
        16-character hex string
    """
    meta_str = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    payload = "\n\n".join([source_type.strip(), content.strip(), meta_str])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_content_hash(prompt: str, response: str, cwd: str = "") -> str:
    """Legacy Q&A hash — DO NOT CHANGE. Existing processed/ data depends on this."""
    payload = "\n\n".join([cwd.strip(), prompt.strip(), response.strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ensure_content_hash(qa: dict) -> str:
    content_hash = str(qa.get("content_hash", "")).strip()
    if content_hash:
        return content_hash
    content_hash = compute_content_hash(
        str(qa.get("prompt", "")),
        str(qa.get("response", "")),
        str(qa.get("cwd", "")),
    )
    qa["content_hash"] = content_hash
    return content_hash
