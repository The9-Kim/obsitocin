import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).parent))
from helpers import create_test_vault


class TestQueryIncludesTopicNotes(unittest.TestCase):
    """search_knowledge/query should include topic:* entries from the index."""

    def test_topic_note_entries_returned_when_indexed(self):
        """When topic:* entries are in the index, query should include them."""
        from obsitocin.memory_query import query
        from obsitocin.embeddings import cosine_similarity

        mock_embedding = [1.0] + [0.0] * 15
        mock_index = {
            "dimensions": 16,
            "entries": {
                "topic:test-project:Docker": {
                    "embedding": mock_embedding,
                    "text_hash": "abc123",
                    "created_at": "2026-04-05T10:00:00",
                    "source_type": "topic_note",
                },
            },
        }

        query_embedding = [1.0] + [0.0] * 15

        with (
            mock.patch("obsitocin.memory_query.is_configured", return_value=True),
            mock.patch("obsitocin.memory_query.start_embed_server"),
            mock.patch("obsitocin.memory_query.stop_embed_server"),
            mock.patch("obsitocin.memory_query.load_index", return_value=mock_index),
            mock.patch(
                "obsitocin.memory_query.get_embedding", return_value=query_embedding
            ),
            mock.patch("obsitocin.memory_query._load_all_written_qas", return_value=[]),
            mock.patch("obsitocin.memory_query._ensure_index", return_value=mock_index),
        ):
            results = query("Docker", top_k=5)

        topic_results = [r for r in results if r.get("source_type") == "topic_note"]
        self.assertGreater(
            len(topic_results), 0, "Topic note should appear in search results"
        )
        self.assertIn("Docker", topic_results[0]["title"])


if __name__ == "__main__":
    unittest.main()
