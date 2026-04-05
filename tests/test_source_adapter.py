import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obsitocin.source_adapter import (
    SourceItem,
    SourceAdapter,
    validate_source_item,
    KNOWN_SOURCE_TYPES,
)


class SourceAdapterTests(unittest.TestCase):
    def test_valid_claude_code_item(self) -> None:
        """Test that a complete valid item passes validation."""
        item = {
            "source_type": "claude_code",
            "content": "x",
            "metadata": {},
            "timestamp": "2026-04-05T10:00:00",
            "project": "test",
            "content_hash": "abc",
        }
        self.assertTrue(validate_source_item(item))

    def test_missing_required_field(self) -> None:
        """Test that missing content_hash fails validation."""
        item = {
            "source_type": "claude_code",
            "content": "x",
            "metadata": {},
            "timestamp": "2026-04-05T10:00:00",
            "project": "test",
        }
        self.assertFalse(validate_source_item(item))

    def test_unknown_source_type(self) -> None:
        """Test that unknown source_type fails validation."""
        item = {
            "source_type": "unknown_xyz",
            "content": "x",
            "metadata": {},
            "timestamp": "2026-04-05T10:00:00",
            "project": "test",
            "content_hash": "abc",
        }
        self.assertFalse(validate_source_item(item))

    def test_empty_dict(self) -> None:
        """Test that empty dict fails validation."""
        self.assertFalse(validate_source_item({}))

    def test_all_known_source_types(self) -> None:
        """Test that all known source types pass validation."""
        for source_type in KNOWN_SOURCE_TYPES:
            item = {
                "source_type": source_type,
                "content": "x",
                "metadata": {},
                "timestamp": "2026-04-05T10:00:00",
                "project": "test",
                "content_hash": "abc",
            }
            self.assertTrue(
                validate_source_item(item),
                f"Failed for source_type: {source_type}",
            )

    def test_metadata_must_be_dict(self) -> None:
        """Test that metadata must be a dict, not a string."""
        item = {
            "source_type": "claude_code",
            "content": "x",
            "metadata": "string",
            "timestamp": "2026-04-05T10:00:00",
            "project": "test",
            "content_hash": "abc",
        }
        self.assertFalse(validate_source_item(item))

    def test_import_all_exports(self) -> None:
        """Test that SourceItem, SourceAdapter, validate_source_item are importable."""
        # If we got here, imports succeeded
        self.assertIsNotNone(SourceItem)
        self.assertIsNotNone(SourceAdapter)
        self.assertIsNotNone(validate_source_item)


if __name__ == "__main__":
    unittest.main()
