"""Text chunking for embedding long Q&A entries."""

from collections import deque

DEFAULT_MAX_CHUNK_CHARS = 3000
DEFAULT_OVERLAP_RATIO = 0.15


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[str]:
    """Split text into overlapping chunks at paragraph boundaries.

    If text <= max_chars, returns [text] (single chunk).
    Otherwise splits on double-newline paragraph boundaries.
    Overlap: last ~15% of previous chunk prepended to next.
    Never splits mid-word.
    """
    if not text:
        return [""]

    if len(text) <= max_chars:
        return [text]

    overlap_chars = int(max_chars * overlap_ratio)

    def _split_at_word(s: str, limit: int) -> tuple[str, str]:
        """Split s so head <= limit chars, cut at word boundary."""
        if len(s) <= limit:
            return s, ""
        cut = s.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        return s[:cut], s[cut:].lstrip(" ")

    def _overlap_prefix(chunk: str) -> str:
        """Return last overlap_chars characters of chunk, cut at word boundary."""
        if len(chunk) <= overlap_chars:
            return chunk
        tail = chunk[-overlap_chars:]
        space = tail.find(" ")
        if space != -1:
            tail = tail[space + 1:]
        return tail

    # Flatten text into a deque of segments (split on \n\n, then split long ones)
    segments: deque[str] = deque()
    for para in text.split("\n\n"):
        if len(para) <= max_chars:
            segments.append(para)
        else:
            # Split long paragraph into word-boundary pieces
            remaining = para
            while remaining:
                head, remaining = _split_at_word(remaining, max_chars)
                segments.append(head)

    chunks: list[str] = []
    current_buf = ""

    while segments:
        seg = segments.popleft()
        candidate = (current_buf + "\n\n" + seg) if current_buf else seg

        if len(candidate) <= max_chars:
            current_buf = candidate
        else:
            # current_buf is full — finalize it
            if current_buf:
                chunks.append(current_buf)
                prefix = _overlap_prefix(current_buf)
                sep = "\n\n" if prefix else ""
                available = max_chars - len(prefix) - len(sep)
                # seg might not fit in the new chunk after the overlap prefix
                if len(seg) <= available:
                    current_buf = prefix + sep + seg
                else:
                    # Trim seg to fit; push remainder back
                    head, remainder = _split_at_word(seg, available)
                    current_buf = prefix + sep + head
                    if remainder:
                        segments.appendleft(remainder)
            else:
                # current_buf is empty but seg alone is still too big
                # (shouldn't happen after _split_at_word, but guard anyway)
                head, remainder = _split_at_word(seg, max_chars)
                chunks.append(head)
                current_buf = ""
                if remainder:
                    segments.appendleft(remainder)

    if current_buf or not chunks:
        chunks.append(current_buf)

    return chunks if chunks else [""]


def chunk_by_structure(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> list[str]:
    """Split markdown text at structural boundaries (headings, bullet groups).

    Designed for topic notes where ## headings and bullet groups form
    natural semantic units. Falls back to chunk_text() for non-markdown.
    """
    if not text or len(text) <= max_chars:
        return [text] if text else [""]

    import re

    # Split on ## headings (keep heading with its content)
    sections: list[str] = []
    parts = re.split(r"(?=\n## )", text)
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        if len(stripped) <= max_chars:
            sections.append(stripped)
        else:
            # Section too large — split on bullet groups (empty line between bullets)
            bullet_groups = re.split(r"\n\n+", stripped)
            current = ""
            for group in bullet_groups:
                candidate = (current + "\n\n" + group) if current else group
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        sections.append(current)
                    # If single group exceeds max, fall back to char-level split
                    if len(group) > max_chars:
                        sections.extend(chunk_text(group, max_chars=max_chars, overlap_ratio=0.10))
                        current = ""
                    else:
                        current = group
            if current:
                sections.append(current)

    return sections if sections else chunk_text(text, max_chars=max_chars)


def chunks_for_qa(qa: dict) -> list[str]:
    """Generate chunks from a processed Q&A entry.

    Reuses the same text composition logic as embeddings.py qa_to_embed_text:
    title + summary + tags + concepts + prompt[:2500] + response[:2500]
    """
    tagging = qa.get("tagging_result", {})
    parts = []
    title = tagging.get("title", "")
    if title:
        parts.append(title)
    summary = tagging.get("work_summary") or tagging.get("summary", "")
    if summary:
        parts.append(summary)
    tags = tagging.get("tags", [])
    if tags:
        parts.append(" ".join(tags))
    concepts = tagging.get("key_concepts", [])
    if concepts:
        parts.append(" ".join(concepts))
    prompt = qa.get("prompt", "")
    if prompt:
        parts.append(prompt[:2500])
    response = qa.get("response", "")
    if response:
        parts.append(response[:2500])
    text = "\n\n".join(parts)
    return chunk_text(text)
