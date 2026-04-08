import tempfile
import unittest
from pathlib import Path
from unittest import mock
from datetime import datetime

import sys

sys.path.insert(0, str(Path(__file__).parent))
from helpers import create_test_vault, make_processed_qa


class TestUpdateMocSummaries(unittest.TestCase):
    def test_moc_includes_one_line_summary(self):
        """Verify that MOC includes one-line summaries from first knowledge bullet."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))

            from obsitocin.topic_writer import update_moc

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                update_moc()

            moc_content = (vault_dir / "_MOC.md").read_text()
            # Should contain em dash separator for summaries
            self.assertIn("—", moc_content)

    def test_moc_format_with_summary(self):
        """Verify exact format: [[path|title]] (N) — summary"""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))

            from obsitocin.topic_writer import update_moc

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                update_moc()

            moc_content = (vault_dir / "_MOC.md").read_text()
            # Should contain Docker entry with summary
            self.assertIn("Docker", moc_content)
            # Entry should have session count in parens and em dash
            import re

            # Pattern: [[path|title]] (N) — summary
            pattern = r"\[\[.+\|Docker\]\] \(\d+\) —"
            self.assertRegex(moc_content, pattern)

    def test_topic_without_knowledge_no_suffix(self):
        """Topics with no real knowledge should not have — suffix."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            topics_dir = vault_dir / "projects" / "test-project" / "topics"

            # Create a topic with only placeholder knowledge
            empty_topic = topics_dir / "EmptyTopic.md"
            empty_topic.write_text("""\
---
title: EmptyTopic
project: test-project
tags:
  - test
type: topic-note
created: 2026-04-05
updated: 2026-04-05
sessions: 1
importance: 1
---

# EmptyTopic

## 핵심 지식

- (아직 축적된 지식 없음)

## 히스토리

- 2026-04-05: 테스트

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->
<!-- OBSITOCIN:END USER NOTES -->
""")
            from obsitocin.topic_writer import update_moc

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                update_moc()

            moc_content = (vault_dir / "_MOC.md").read_text()
            # EmptyTopic line should not have " — " suffix
            lines = [l for l in moc_content.split("\n") if "EmptyTopic" in l]
            self.assertTrue(len(lines) > 0)
            # The EmptyTopic line should NOT have " — "
            for line in lines:
                self.assertNotIn(
                    " — ", line, f"Empty topic should not have summary: {line}"
                )

    def test_summary_truncated_to_80_chars(self):
        """Verify that summaries are truncated to 80 characters."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            topics_dir = vault_dir / "projects" / "test-project" / "topics"

            # Create a topic with a very long knowledge bullet
            long_topic = topics_dir / "LongTopic.md"
            long_knowledge = "이것은 매우 긴 지식 항목입니다. " * 10  # Very long string
            long_topic.write_text(f"""\
---
title: LongTopic
project: test-project
tags:
  - test
type: topic-note
created: 2026-04-05
updated: 2026-04-05
sessions: 1
importance: 1
---

# LongTopic

## 핵심 지식

- {long_knowledge}

## 히스토리

- 2026-04-05: 테스트

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->
<!-- OBSITOCIN:END USER NOTES -->
""")
            from obsitocin.topic_writer import update_moc

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                update_moc()

            moc_content = (vault_dir / "_MOC.md").read_text()
            lines = [l for l in moc_content.split("\n") if "LongTopic" in l]
            self.assertTrue(len(lines) > 0)
            for line in lines:
                # Extract the summary part after " — "
                if " — " in line:
                    summary_part = line.split(" — ")[1]
                    self.assertLessEqual(
                        len(summary_part),
                        80,
                        f"Summary should be <= 80 chars: {summary_part}",
                    )


if __name__ == "__main__":
    unittest.main()


class TestTopicPromotionThreshold(unittest.TestCase):
    def test_low_importance_new_topic_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(tmp) / "obsitocin"
            qa = {
                "cwd": "/tmp/test-project",
                "timestamp": "2026-04-05T12:00:00",
                "tagging_result": {
                    "topics": [
                        {
                            "name": "가벼운 토픽",
                            "knowledge": ["한 번 나온 메모"],
                        }
                    ],
                    "work_summary": "가벼운 메모 기록",
                    "tags": ["test"],
                    "importance": 2,
                },
            }

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                result = write_notes_for_qa(qa)

            self.assertEqual(result["topics_written"], 0)
            self.assertFalse(
                (
                    vault_dir
                    / "projects"
                    / "test-project"
                    / "topics"
                    / "가벼운 토픽.md"
                ).exists()
            )

    def test_low_importance_can_update_existing_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(tmp) / "obsitocin"
            topics_dir = vault_dir / "projects" / "test-project" / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            existing_file = topics_dir / "기존 토픽.md"
            existing_file.write_text(
                """\
---
title: 기존 토픽
project: test-project
tags:
  - test
type: topic-note
created: 2026-04-04
updated: 2026-04-04
sessions: 1
importance: 4
---

# 기존 토픽

## 핵심 지식

- 기존 지식

## 히스토리

- 2026-04-04 10:00: 초기 기록

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->
<!-- OBSITOCIN:END USER NOTES -->
"""
            )
            qa = {
                "cwd": "/tmp/test-project",
                "timestamp": datetime.now().isoformat(),
                "tagging_result": {
                    "topics": [
                        {
                            "name": "기존 토픽",
                            "knowledge": ["추가 지식"],
                        }
                    ],
                    "work_summary": "기존 토픽 보강",
                    "tags": ["test"],
                    "importance": 3,
                },
            }

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                result = write_notes_for_qa(qa)

            self.assertEqual(result["topics_written"], 1)
            updated = existing_file.read_text()
            self.assertIn("추가 지식", updated)


class TestSessionRawPreservation(unittest.TestCase):
    def _make_vault(self, tmp: str) -> Path:
        vault_dir = Path(tmp) / "obsitocin"
        (vault_dir / "raw" / "sessions").mkdir(parents=True, exist_ok=True)
        (vault_dir / "projects" / "test-project" / "topics").mkdir(parents=True, exist_ok=True)
        (vault_dir / "daily").mkdir(parents=True, exist_ok=True)
        return vault_dir

    def test_write_notes_creates_session_raw(self):
        """write_notes_for_qa creates a file under raw/sessions/."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = self._make_vault(tmp)
            qa = make_processed_qa()

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                write_notes_for_qa(qa)

            date_str = "2026-04-05"
            sessions_dir = vault_dir / "raw" / "sessions" / date_str
            files = list(sessions_dir.glob("*.md"))
            self.assertEqual(len(files), 1, f"Expected 1 session raw file, got {files}")

    def test_session_raw_idempotent(self):
        """Calling write_notes_for_qa twice with same content_hash produces only one file."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = self._make_vault(tmp)
            qa = make_processed_qa()

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                write_notes_for_qa(qa)
                write_notes_for_qa(qa)

            date_str = "2026-04-05"
            sessions_dir = vault_dir / "raw" / "sessions" / date_str
            files = list(sessions_dir.glob("*.md"))
            self.assertEqual(len(files), 1, f"Expected 1 file after 2 calls, got {files}")

    def test_session_raw_frontmatter(self):
        """Session raw file contains expected frontmatter fields."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = self._make_vault(tmp)
            qa = make_processed_qa()

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                write_notes_for_qa(qa)

            date_str = "2026-04-05"
            sessions_dir = vault_dir / "raw" / "sessions" / date_str
            raw_file = next(sessions_dir.glob("*.md"))
            content = raw_file.read_text(encoding="utf-8")

            self.assertIn("type: session-raw", content)
            self.assertIn(f"content_hash: {qa['content_hash']}", content)
            self.assertIn(f"session_id: {qa['session_id']}", content)

    def test_session_raw_preserves_content(self):
        """Session raw file preserves original prompt and response verbatim."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = self._make_vault(tmp)
            qa = make_processed_qa()

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                write_notes_for_qa(qa)

            date_str = "2026-04-05"
            sessions_dir = vault_dir / "raw" / "sessions" / date_str
            raw_file = next(sessions_dir.glob("*.md"))
            content = raw_file.read_text(encoding="utf-8")

            self.assertIn(qa["prompt"], content)
            self.assertIn(qa["response"], content)

    def test_daily_log_links_to_raw(self):
        """Daily work log entry contains a wikilink to the raw session file."""
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = self._make_vault(tmp)
            qa = make_processed_qa()

            from obsitocin.topic_writer import write_notes_for_qa

            with mock.patch("obsitocin.topic_writer.OBS_DIR", vault_dir):
                write_notes_for_qa(qa)

            date_str = "2026-04-05"
            log_file = vault_dir / "daily" / f"{date_str}.md"
            self.assertTrue(log_file.exists(), "Daily log file should exist")
            log_content = log_file.read_text(encoding="utf-8")
            self.assertIn("원문", log_content)
            self.assertIn("raw/sessions/", log_content)
