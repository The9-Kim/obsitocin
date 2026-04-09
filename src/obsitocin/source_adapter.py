"""Universal source adapter protocol for obsitocin."""

from __future__ import annotations
from typing import Protocol, runtime_checkable

KNOWN_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "claude_code",
        "codex",
        "gemini",
        "claude_ai",
        "slack",
        "jira",
        "confluence",
        "git",
        "manual",
    }
)


@runtime_checkable
class SourceItem(Protocol):
    source_type: str
    content: str
    metadata: dict
    timestamp: str
    project: str
    content_hash: str


class SourceAdapter(Protocol):
    def normalize(self, raw_data: dict) -> dict:
        """Normalize raw source data to SourceItem-shaped dict."""
        ...


def validate_source_item(item: dict) -> bool:
    """Validate that a dict has all required SourceItem fields with correct types."""
    required = {
        "source_type",
        "content",
        "metadata",
        "timestamp",
        "project",
        "content_hash",
    }
    if not required.issubset(item.keys()):
        return False
    if item["source_type"] not in KNOWN_SOURCE_TYPES:
        return False
    if not isinstance(item["metadata"], dict):
        return False
    return True
