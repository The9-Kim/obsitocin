import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).parent))
from helpers import create_test_vault


class TestTopicNoteToEmbedText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault_dir = Path(create_test_vault(self.tmp))

    def test_extracts_title_from_docker_note(self):
        from obsitocin.embeddings import topic_note_to_embed_text

        docker_path = (
            self.vault_dir / "projects" / "test-project" / "topics" / "Docker.md"
        )
        text = topic_note_to_embed_text(docker_path)
        self.assertIn("Docker", text)

    def test_extracts_knowledge_bullets(self):
        from obsitocin.embeddings import topic_note_to_embed_text

        docker_path = (
            self.vault_dir / "projects" / "test-project" / "topics" / "Docker.md"
        )
        text = topic_note_to_embed_text(docker_path)
        self.assertIn("컨테이너", text)

    def test_returns_empty_for_nonexistent_file(self):
        from obsitocin.embeddings import topic_note_to_embed_text

        result = topic_note_to_embed_text(Path("/nonexistent/path.md"))
        self.assertEqual(result, "")

    def test_result_is_non_empty_string(self):
        from obsitocin.embeddings import topic_note_to_embed_text

        docker_path = (
            self.vault_dir / "projects" / "test-project" / "topics" / "Docker.md"
        )
        text = topic_note_to_embed_text(docker_path)
        self.assertIsInstance(text, str)
        self.assertTrue(len(text) > 0)


class TestEmbedTopicNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault_dir = Path(create_test_vault(self.tmp))

    def test_embed_topic_notes_returns_count(self):
        from obsitocin.embeddings import embed_topic_notes

        mock_embedding = [0.1] * 16
        with (
            mock.patch(
                "obsitocin.embeddings.get_embeddings_batch",
                return_value=[mock_embedding, mock_embedding],
            ),
            mock.patch(
                "obsitocin.embeddings.EMBEDDINGS_INDEX_PATH",
                Path(self.tmp) / "test_index.json",
            ),
        ):
            count = embed_topic_notes(self.vault_dir)

        self.assertGreaterEqual(count, 2)

    def test_embed_topic_notes_uses_topic_prefix_key(self):
        from obsitocin.embeddings import embed_topic_notes, load_index

        mock_embedding = [0.1] * 16
        index_path = Path(self.tmp) / "test_index.json"
        with (
            mock.patch(
                "obsitocin.embeddings.get_embeddings_batch",
                return_value=[mock_embedding, mock_embedding],
            ),
            mock.patch("obsitocin.embeddings.EMBEDDINGS_INDEX_PATH", index_path),
        ):
            embed_topic_notes(self.vault_dir)

        with mock.patch("obsitocin.embeddings.EMBEDDINGS_INDEX_PATH", index_path):
            index = load_index()

        topic_keys = [k for k in index["entries"].keys() if k.startswith("topic:")]
        self.assertGreaterEqual(len(topic_keys), 1)
        for key in topic_keys:
            parts = key.split(":")
            self.assertEqual(parts[0], "topic")
            self.assertEqual(parts[1], "test-project")

    def test_embed_topic_notes_empty_vault_returns_zero(self):
        from obsitocin.embeddings import embed_topic_notes

        with tempfile.TemporaryDirectory() as empty_tmp:
            count = embed_topic_notes(Path(empty_tmp))
        self.assertEqual(count, 0)

    def test_existing_embeddings_not_reembedded(self):
        from obsitocin.embeddings import embed_topic_notes

        mock_embedding = [0.1] * 16
        index_path = Path(self.tmp) / "test_index.json"
        with (
            mock.patch(
                "obsitocin.embeddings.get_embeddings_batch",
                return_value=[mock_embedding, mock_embedding],
            ),
            mock.patch("obsitocin.embeddings.EMBEDDINGS_INDEX_PATH", index_path),
        ):
            embed_topic_notes(self.vault_dir)

        with (
            mock.patch(
                "obsitocin.embeddings.get_embeddings_batch", return_value=[]
            ) as mock_batch,
            mock.patch("obsitocin.embeddings.EMBEDDINGS_INDEX_PATH", index_path),
        ):
            second_count = embed_topic_notes(self.vault_dir)

        self.assertEqual(second_count, 0)
        mock_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
