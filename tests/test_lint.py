import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent))
from helpers import create_test_vault


class TestCheckBrokenWikilinks(unittest.TestCase):
    def test_broken_link_in_moc_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            moc = vault_dir / "_MOC.md"
            content = moc.read_text()
            moc.write_text(
                content + "\n- [[projects/nonexistent/topics/Ghost|Ghost]]\n"
            )

            from obsitocin.lint import check_broken_wikilinks

            issues = check_broken_wikilinks(vault_dir)

            broken = [i for i in issues if "Ghost" in i["link"]]
            self.assertGreater(len(broken), 0)

    def test_clean_vault_has_no_broken_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_broken_wikilinks

            issues = check_broken_wikilinks(vault_dir)
            self.assertEqual(len(issues), 0)


class TestCheckOrphanTopics(unittest.TestCase):
    def test_orphan_topic_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            topics_dir = vault_dir / "projects" / "test-project" / "topics"

            orphan = topics_dir / "OrphanTopic.md"
            orphan.write_text(
                "---\ntitle: OrphanTopic\n---\n\n# OrphanTopic\n\n## 핵심 지식\n\n- some knowledge\n"
            )

            from obsitocin.lint import check_orphan_topics

            issues = check_orphan_topics(vault_dir)
            orphan_issues = [i for i in issues if "OrphanTopic" in i["path"]]
            self.assertGreater(len(orphan_issues), 0)

    def test_referenced_topic_not_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_orphan_topics

            issues = check_orphan_topics(vault_dir)
            docker_orphan = [i for i in issues if "Docker" in i.get("path", "")]
            self.assertEqual(len(docker_orphan), 0)


class TestCheckThinNotes(unittest.TestCase):
    def test_thin_note_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            topics_dir = vault_dir / "projects" / "test-project" / "topics"

            thin_note = topics_dir / "ThinTopic.md"
            thin_note.write_text(
                "---\ntitle: ThinTopic\nproject: test-project\n"
                "type: topic-note\ncreated: 2026-04-05\nupdated: 2026-04-05\n"
                "sessions: 1\nimportance: 2\n---\n\n# ThinTopic\n\n"
                "## 핵심 지식\n\n- 단 하나의 지식\n\n"
                "## 히스토리\n\n- 2026-04-05 09:00: 테스트\n"
            )
            from obsitocin.lint import check_thin_notes

            issues = check_thin_notes(vault_dir, min_knowledge=2)
            thin = [i for i in issues if "ThinTopic" in i["title"]]
            self.assertGreater(len(thin), 0)

    def test_rich_note_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import check_thin_notes

            issues = check_thin_notes(vault_dir, min_knowledge=2)
            docker_issues = [i for i in issues if "Docker" in i.get("title", "")]
            self.assertEqual(len(docker_issues), 0)


class TestCheckMocConsistency(unittest.TestCase):
    def test_extra_topic_file_not_in_moc(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            topics_dir = vault_dir / "projects" / "test-project" / "topics"

            new_topic = topics_dir / "Kubernetes.md"
            new_topic.write_text(
                "---\ntitle: Kubernetes\nproject: test-project\n"
                "type: topic-note\ncreated: 2026-04-05\nupdated: 2026-04-05\n"
                "sessions: 1\nimportance: 3\n---\n\n# Kubernetes\n\n"
                "## 핵심 지식\n\n- K8s는 컨테이너 오케스트레이션 플랫폼\n\n"
                "## 히스토리\n\n- 2026-04-05 10:00: K8s 학습\n"
            )
            from obsitocin.lint import check_moc_consistency

            issues = check_moc_consistency(vault_dir)
            k8s_issues = [i for i in issues if "Kubernetes" in i.get("path", "")]
            self.assertGreater(len(k8s_issues), 0)

    def test_stale_moc_entry_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            moc = vault_dir / "_MOC.md"
            content = moc.read_text()
            moc.write_text(
                content + "\n  - [[projects/test-project/topics/Deleted|Deleted]] (1)\n"
            )

            from obsitocin.lint import check_moc_consistency

            issues = check_moc_consistency(vault_dir)
            stale = [i for i in issues if i["type"] == "moc_stale_entry"]
            self.assertGreater(len(stale), 0)


class TestRunAllChecks(unittest.TestCase):
    def test_clean_vault_returns_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import run_all_checks

            result = run_all_checks(vault_dir)
            self.assertIn("total_issues", result)
            self.assertIn("checks", result)
            self.assertIn("clean", result)
            self.assertTrue(result["clean"])
            self.assertEqual(result["total_issues"], 0)

    def test_result_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            from obsitocin.lint import run_all_checks

            result = run_all_checks(vault_dir)
            self.assertIn("broken_wikilinks", result["checks"])
            self.assertIn("orphan_topics", result["checks"])
            self.assertIn("thin_notes", result["checks"])
            self.assertIn("moc_consistency", result["checks"])

    def test_dirty_vault_returns_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault_dir = Path(create_test_vault(tmp))
            moc = vault_dir / "_MOC.md"
            content = moc.read_text()
            moc.write_text(content + "\n- [[projects/ghost/topics/Missing|Missing]]\n")

            from obsitocin.lint import run_all_checks

            result = run_all_checks(vault_dir)
            self.assertFalse(result["clean"])
            self.assertGreater(result["total_issues"], 0)


class TestExtractWikilinks(unittest.TestCase):
    def test_extracts_path_with_label(self):
        from obsitocin.lint import _extract_wikilinks

        links = _extract_wikilinks("- [[projects/p/topics/Docker|Docker]]")
        self.assertEqual(links, ["projects/p/topics/Docker"])

    def test_extracts_path_without_label(self):
        from obsitocin.lint import _extract_wikilinks

        links = _extract_wikilinks("- [[daily/2026-04-05]]")
        self.assertEqual(links, ["daily/2026-04-05"])

    def test_no_links_returns_empty(self):
        from obsitocin.lint import _extract_wikilinks

        links = _extract_wikilinks("No links here.")
        self.assertEqual(links, [])
