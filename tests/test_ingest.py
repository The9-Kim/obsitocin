import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).parent))


class TestIngestLocalFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "raw").mkdir()
        (self.vault / "projects" / "test" / "sources").mkdir(parents=True)
        (self.vault / "projects" / "test" / "topics").mkdir(parents=True)
        (self.vault / "daily").mkdir()

        self.source_file = Path(self.tmp.name) / "article.md"
        self.source_file.write_text(
            "# Docker Guide\n\nDocker is a container platform.", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_local_file_creates_raw(self):
        ingest = importlib.import_module("obsitocin.ingest")

        mock_result = {
            "title": "Docker Guide",
            "should_store": True,
            "topics": [{"name": "Docker", "knowledge": ["container platform"]}],
            "work_summary": "Docker guide",
            "tags": ["docker"],
            "category": "devops",
            "importance": 3,
            "distilled_knowledge": ["Docker is a container platform"],
        }
        with (
            mock.patch("obsitocin.ingest.OBS_DIR", self.vault),
            mock.patch("obsitocin.processor.call_tagging", return_value=mock_result),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault),
        ):
            result = ingest.ingest_source(
                str(self.source_file), project="test", title="Docker Guide"
            )

        self.assertTrue(result["success"])
        self.assertIsNotNone(result["raw_path"])
        raw_files = list((self.vault / "raw").glob("*.md"))
        self.assertGreater(len(raw_files), 0)

    def test_ingest_creates_source_page(self):
        ingest = importlib.import_module("obsitocin.ingest")

        mock_result = {
            "title": "Docker",
            "should_store": True,
            "topics": [],
            "work_summary": "Docker",
            "tags": ["docker"],
            "category": "devops",
            "importance": 3,
            "distilled_knowledge": ["Docker is a container platform"],
        }
        with (
            mock.patch("obsitocin.ingest.OBS_DIR", self.vault),
            mock.patch("obsitocin.processor.call_tagging", return_value=mock_result),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault),
        ):
            result = ingest.ingest_source(
                str(self.source_file), project="test", title="Docker Guide"
            )

        self.assertTrue(result["success"])
        source_files = list((self.vault / "projects" / "test" / "sources").glob("*.md"))
        self.assertGreater(len(source_files), 0)

    def test_ingest_nonexistent_file_returns_error(self):
        ingest = importlib.import_module("obsitocin.ingest")

        with mock.patch("obsitocin.ingest.OBS_DIR", self.vault):
            result = ingest.ingest_source("/nonexistent/path.md", project="test")

        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"].lower())


class TestIngestURL(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        (self.vault / "raw").mkdir()
        (self.vault / "projects" / "test" / "sources").mkdir(parents=True)
        (self.vault / "projects" / "test" / "topics").mkdir(parents=True)
        (self.vault / "daily").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_url_with_mock_fetch(self):
        ingest = importlib.import_module("obsitocin.ingest")

        mock_result = {
            "title": "Article",
            "should_store": True,
            "topics": [{"name": "AI", "knowledge": ["AI is transforming tech"]}],
            "work_summary": "AI article",
            "tags": ["ai"],
            "category": "domain",
            "importance": 4,
            "distilled_knowledge": ["AI is transforming tech"],
        }
        with (
            mock.patch("obsitocin.ingest.OBS_DIR", self.vault),
            mock.patch(
                "obsitocin.ingest._fetch_url",
                return_value="<html><body>AI is transforming tech</body></html>",
            ),
            mock.patch("obsitocin.processor.call_tagging", return_value=mock_result),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault),
        ):
            result = ingest.ingest_source(
                "https://example.com/article", project="test", title="AI Article"
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["topics_updated"], 1)


class TestIngestCliAndMcp(unittest.TestCase):
    def test_build_parser_includes_ingest(self):
        build_parser = importlib.import_module("obsitocin.cli").build_parser

        parser = build_parser()
        args = parser.parse_args(["ingest", "https://example.com"])

        self.assertEqual(args.command, "ingest")
        self.assertEqual(args.source, "https://example.com")
        self.assertTrue(callable(args.handler))

    def test_ingest_source_mcp_delegates(self):
        ingest_source_mcp = importlib.import_module(
            "obsitocin.mcp_server"
        ).ingest_source_mcp

        expected = {
            "success": True,
            "raw_path": "r",
            "source_page": "s",
            "topics_updated": 1,
        }
        with mock.patch(
            "obsitocin.ingest.ingest_source", return_value=expected
        ) as patched:
            result = ingest_source_mcp(
                "https://example.com", project="test", title="Example"
            )

        patched.assert_called_once_with(
            source="https://example.com", project="test", title="Example"
        )
        self.assertEqual(result, expected)
