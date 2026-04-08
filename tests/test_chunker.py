import unittest

from obsitocin.chunker import chunk_text, chunks_for_qa, DEFAULT_MAX_CHUNK_CHARS


class TestChunkText(unittest.TestCase):
    def test_short_text_single_chunk(self):
        text = "Hello world"
        result = chunk_text(text, max_chars=100)
        self.assertEqual(result, [text])

    def test_long_text_splits(self):
        # Build text clearly over max_chars using distinct paragraphs
        para = "A" * 50
        text = "\n\n".join([para] * 10)  # ~540 chars with separators
        result = chunk_text(text, max_chars=150)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), 150)

    def test_overlap_present(self):
        # Two paragraphs that together exceed max_chars but each fits alone
        para_a = "X " * 60  # ~120 chars
        para_b = "Y " * 60  # ~120 chars
        para_c = "Z " * 60  # ~120 chars
        text = "\n\n".join([para_a, para_b, para_c])
        result = chunk_text(text, max_chars=150, overlap_ratio=0.15)
        self.assertGreater(len(result), 1)
        # Adjacent chunks must share some characters (overlap)
        for i in range(len(result) - 1):
            # The end of chunk i should appear somewhere in chunk i+1
            tail = result[i][-20:].strip()
            self.assertTrue(
                tail in result[i + 1],
                f"No overlap found between chunk {i} and chunk {i+1}",
            )

    def test_empty_text(self):
        result = chunk_text("")
        self.assertEqual(result, [""])

    def test_exact_boundary(self):
        text = "A" * DEFAULT_MAX_CHUNK_CHARS
        result = chunk_text(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    def test_single_long_paragraph(self):
        # Single paragraph with no \n\n but longer than max_chars
        text = "word " * 200  # ~1000 chars, no paragraph breaks
        result = chunk_text(text, max_chars=100)
        self.assertGreater(len(result), 1)
        for chunk in result:
            self.assertLessEqual(len(chunk), 100)
        # Ensure no mid-word splits (each chunk ends on word boundary or is a word itself)
        for chunk in result:
            self.assertFalse(chunk.endswith("-"), "Unexpected hyphenation")

    def test_chunks_for_qa(self):
        qa = {
            "tagging_result": {
                "title": "How to use Docker",
                "work_summary": "Overview of Docker containers",
                "tags": ["docker", "containers"],
                "key_concepts": ["image", "volume"],
            },
            "prompt": "What is Docker?",
            "response": "Docker is a containerization platform.",
        }
        result = chunks_for_qa(qa)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        # All content should fit in a single chunk for this small qa
        self.assertEqual(len(result), 1)
        self.assertIn("How to use Docker", result[0])
        self.assertIn("docker", result[0])

    def test_chunks_for_qa_long_response(self):
        # Large prompt+response should still produce valid chunks
        qa = {
            "tagging_result": {
                "title": "Big answer",
                "work_summary": "Summary here",
                "tags": ["tag1"],
                "key_concepts": [],
            },
            "prompt": "Q " * 1500,   # 3000 chars, truncated to 2500
            "response": "A " * 1500, # 3000 chars, truncated to 2500
        }
        result = chunks_for_qa(qa)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)
        for chunk in result:
            self.assertLessEqual(len(chunk), DEFAULT_MAX_CHUNK_CHARS)


if __name__ == "__main__":
    unittest.main()
