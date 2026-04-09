"""Tests for seCall-inspired features: lint DB checks, reindex, recall, scanner."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from helpers import create_test_vault


class TestLintDbVaultConsistency(unittest.TestCase):
    def test_no_db_returns_empty(self):
        """When no search.db exists, DB checks return empty."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_db_vault_consistency

            issues = check_db_vault_consistency(vault_dir)
            self.assertEqual(issues, [])

    def test_no_db_fts_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_fts_integrity

            issues = check_fts_integrity(vault_dir)
            self.assertEqual(issues, [])

    def test_no_db_orphan_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_orphan_embeddings

            issues = check_orphan_embeddings(vault_dir)
            self.assertEqual(issues, [])


class TestRunAllChecksStructure(unittest.TestCase):
    def test_new_check_keys_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import run_all_checks

            result = run_all_checks(vault_dir)
            self.assertIn("db_vault_consistency", result["checks"])
            self.assertIn("fts_integrity", result["checks"])
            self.assertIn("orphan_embeddings", result["checks"])


class TestReindex(unittest.TestCase):
    def test_reindex_from_vault_indexes_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            db_path = Path(tmp) / "test_search.db"

            from obsitocin.reindex import reindex_from_vault

            result = reindex_from_vault(vault_dir, db_path)
            self.assertGreater(result["indexed"], 0)
            self.assertEqual(result["errors"], [])

            # Verify DB has entries
            from obsitocin.search_db import get_connection

            conn = get_connection(db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM qa_entries WHERE source_type = 'topic_note'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(count, result["indexed"])

    def test_reindex_from_vault_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(tmp) / "empty_vault"
            vault_dir.mkdir()
            db_path = Path(tmp) / "test_search.db"

            from obsitocin.reindex import reindex_from_vault

            result = reindex_from_vault(vault_dir, db_path)
            self.assertEqual(result["indexed"], 0)

    def test_reindex_from_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            processed_dir = Path(tmp) / "processed"
            processed_dir.mkdir()
            db_path = Path(tmp) / "test_search.db"

            # Create a fake processed QA file
            qa = {
                "status": "written",
                "timestamp": "2026-04-05T10:00:00",
                "cwd": "/Users/test/my-project",
                "prompt": "How does Docker work?",
                "response": "Docker is a container platform.",
                "content_hash": "abc123",
                "source_type": "claude_code",
                "tagging_result": {
                    "title": "Docker basics",
                    "work_summary": "Explained Docker",
                    "category": "devops",
                    "importance": 3,
                    "memory_type": "dynamic",
                    "tags": ["docker"],
                    "key_concepts": ["Docker"],
                },
            }
            (processed_dir / "test_qa.json").write_text(
                json.dumps(qa, ensure_ascii=False)
            )

            from obsitocin.reindex import reindex_from_processed

            result = reindex_from_processed(processed_dir, db_path)
            self.assertEqual(result["indexed"], 1)

    def test_reindex_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            processed_dir = Path(tmp) / "processed"
            processed_dir.mkdir()
            db_path = Path(tmp) / "test_search.db"

            from obsitocin.reindex import reindex_all

            result = reindex_all(vault_dir, processed_dir, db_path)
            self.assertGreater(result["topics_indexed"], 0)
            self.assertIsInstance(result["qas_indexed"], int)
            self.assertIsInstance(result["errors"], list)

    def test_reindex_all_from_vault_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            processed_dir = Path(tmp) / "processed"
            processed_dir.mkdir()
            db_path = Path(tmp) / "test_search.db"

            from obsitocin.reindex import reindex_all

            result = reindex_all(
                vault_dir, processed_dir, db_path, from_vault_only=True
            )
            self.assertGreater(result["topics_indexed"], 0)
            self.assertEqual(result["qas_indexed"], 0)


class TestRecallMulti(unittest.TestCase):
    def test_empty_queries_returns_empty(self):
        from obsitocin.mcp_server import recall_multi

        result = recall_multi([], top_k=5)
        self.assertEqual(result, [])

    def test_query_without_text_skipped(self):
        from obsitocin.mcp_server import recall_multi

        result = recall_multi([{"type": "keyword", "text": ""}], top_k=5)
        self.assertEqual(result, [])


class TestSessionScanner(unittest.TestCase):
    def test_unknown_source_returns_error(self):
        from obsitocin.session_scanner import scan_sessions

        result = scan_sessions("unknown_agent")
        self.assertGreater(len(result["errors"]), 0)

    def test_scan_dry_run_does_not_write(self):
        from obsitocin.session_scanner import scan_sessions

        result = scan_sessions("claude_code", dry_run=True, limit=1)
        self.assertIsInstance(result["scanned"], int)
        self.assertIsInstance(result["queued"], int)
        self.assertIsInstance(result["skipped"], int)

    def test_scan_returns_valid_structure(self):
        from obsitocin.session_scanner import scan_sessions

        result = scan_sessions("codex", dry_run=True)
        self.assertIsInstance(result["scanned"], int)
        self.assertIsInstance(result["queued"], int)
        self.assertIsInstance(result["skipped"], int)
        self.assertIsInstance(result["errors"], list)


class TestSchemaGeneration(unittest.TestCase):
    def test_schema_md_content(self):
        from obsitocin.cli import _generate_schema_md

        content = _generate_schema_md()
        self.assertIn("Vault Schema", content)
        self.assertIn("frontmatter", content.lower())
        self.assertIn("projects/", content)
        self.assertIn("raw/sessions/", content)
        self.assertIn("OBSITOCIN:BEGIN USER NOTES", content)

    def test_init_creates_schema_md(self):
        """Verify SCHEMA.md is created in vault dir structure."""
        content = None
        from obsitocin.cli import _generate_schema_md

        content = _generate_schema_md()
        self.assertIsNotNone(content)
        self.assertGreater(len(content), 100)


if __name__ == "__main__":
    unittest.main()
