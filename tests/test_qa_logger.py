"""Tests for qa_logger queue output format — SourceItem compatibility."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestQaLoggerOutputFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.queue_dir = Path(self.tmp) / "queue"
        self.queue_dir.mkdir()

    def _create_stop_event(self, session_id="test-ses", cwd="/tmp/project"):
        return {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "cwd": cwd,
            "stop_hook_active": False,
            "transcript_path": "",
            "last_assistant_message": "Here is the answer.",
        }

    def _create_prompt_file(self, session_id, prompt="What is Docker?"):
        """Create a prompt file as if handle_prompt_submit was called."""
        prompt_file = self.queue_dir / f"{session_id}_prompt.json"
        entries = [
            {
                "session_id": session_id,
                "timestamp": "2026-04-05T10:00:00",
                "cwd": "/tmp/project",
                "prompt": prompt,
            }
        ]
        prompt_file.write_text(json.dumps(entries, ensure_ascii=False))

    def _run_handle_stop(self, session_id, event=None):
        """Run handle_stop with patched QUEUE_DIR and trigger_processor."""
        import obsitocin.qa_logger as qa_logger_module

        if event is None:
            event = self._create_stop_event(session_id)

        with (
            mock.patch.object(qa_logger_module, "QUEUE_DIR", self.queue_dir),
            mock.patch.object(qa_logger_module, "trigger_processor"),
        ):
            qa_logger_module.handle_stop(event)

        qa_files = [
            f for f in self.queue_dir.glob("*.json") if not f.stem.endswith("_prompt")
        ]
        return qa_files

    def test_handle_stop_adds_source_type(self):
        """Queue JSON must include source_type='claude_code'."""
        session_id = "test-source-type"
        self._create_prompt_file(session_id)

        qa_files = self._run_handle_stop(session_id)
        self.assertEqual(len(qa_files), 1)
        qa = json.loads(qa_files[0].read_text())

        self.assertIn("source_type", qa)
        self.assertEqual(qa["source_type"], "claude_code")

    def test_handle_stop_adds_source_metadata(self):
        """Queue JSON must include source_metadata dict."""
        session_id = "test-source-meta"
        self._create_prompt_file(session_id)

        qa_files = self._run_handle_stop(session_id)
        qa = json.loads(qa_files[0].read_text())

        self.assertIn("source_metadata", qa)
        self.assertIsInstance(qa["source_metadata"], dict)
        self.assertIn("session_id", qa["source_metadata"])
        self.assertEqual(qa["source_metadata"]["session_id"], session_id)

    def test_handle_stop_preserves_existing_fields(self):
        """All original fields must still be present."""
        session_id = "test-preserve-fields"
        self._create_prompt_file(session_id, prompt="What is venv?")

        qa_files = self._run_handle_stop(session_id)
        qa = json.loads(qa_files[0].read_text())

        required_legacy_fields = {
            "session_id",
            "timestamp",
            "cwd",
            "prompt",
            "response",
            "content_hash",
            "status",
            "transcript_path",
        }
        for field in required_legacy_fields:
            self.assertIn(field, qa, f"Missing legacy field: {field}")

    def test_handle_stop_no_prompt_file_also_has_source_type(self):
        """Even when no prompt file exists, source_type should be present."""
        session_id = "test-no-prompt"
        # Don't create prompt file

        event = self._create_stop_event(session_id)
        event["last_assistant_message"] = "Response without prompt"
        qa_files = self._run_handle_stop(session_id, event=event)

        self.assertEqual(len(qa_files), 1)
        qa = json.loads(qa_files[0].read_text())
        self.assertEqual(qa.get("source_type"), "claude_code")
        self.assertIn("source_metadata", qa)
        self.assertIsInstance(qa["source_metadata"], dict)

    def test_source_metadata_contains_transcript_path(self):
        """source_metadata must include transcript_path."""
        session_id = "test-transcript-meta"
        self._create_prompt_file(session_id)

        qa_files = self._run_handle_stop(session_id)
        qa = json.loads(qa_files[0].read_text())

        self.assertIn("transcript_path", qa["source_metadata"])


if __name__ == "__main__":
    unittest.main()
