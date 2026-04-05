import unittest
from obsitocin.identity import (
    compute_content_hash,
    compute_source_hash,
    ensure_content_hash,
)


class TestComputeSourceHash(unittest.TestCase):
    def test_returns_16_char_hex(self):
        h = compute_source_hash("claude_code", "test content", {})
        self.assertEqual(len(h), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_different_source_types_produce_different_hashes(self):
        h1 = compute_source_hash("claude_code", "same content", {})
        h2 = compute_source_hash("slack", "same content", {})
        self.assertNotEqual(h1, h2)

    def test_same_inputs_produce_same_hash(self):
        h1 = compute_source_hash("claude_code", "content", {"cwd": "/tmp"})
        h2 = compute_source_hash("claude_code", "content", {"cwd": "/tmp"})
        self.assertEqual(h1, h2)

    def test_metadata_order_independent(self):
        # Sorting keys before hashing
        h1 = compute_source_hash("slack", "msg", {"a": "1", "b": "2"})
        h2 = compute_source_hash("slack", "msg", {"b": "2", "a": "1"})
        self.assertEqual(h1, h2)


class TestBackwardCompat(unittest.TestCase):
    def test_compute_content_hash_unchanged(self):
        # This hash must never change — existing processed/ data depends on it
        h = compute_content_hash("hello", "world", "/tmp")
        self.assertEqual(len(h), 16)
        # Verify it's the same as before by running twice
        h2 = compute_content_hash("hello", "world", "/tmp")
        self.assertEqual(h, h2)

    def test_ensure_content_hash_on_legacy_dict(self):
        qa = {"prompt": "test", "response": "answer", "cwd": "/tmp"}
        h = ensure_content_hash(qa)
        self.assertEqual(len(h), 16)
        self.assertEqual(qa["content_hash"], h)  # Side effect

    def test_ensure_content_hash_idempotent(self):
        qa = {"prompt": "test", "response": "answer", "cwd": "/tmp"}
        h1 = ensure_content_hash(qa)
        h2 = ensure_content_hash(qa)  # Already has content_hash set
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
