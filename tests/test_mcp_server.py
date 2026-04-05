import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helpers import create_test_vault


def load_mcp_server():
    return importlib.import_module("obsitocin.mcp_server")


class TestListTopics(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_list_topics_returns_docker(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            topics = mcp_server.list_topics()

        self.assertTrue(topics)
        self.assertIn("Docker", [topic["topic"] for topic in topics])

    def test_list_topics_filtered_by_project(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            topics = mcp_server.list_topics(project="test-project")

        self.assertTrue(topics)
        for topic in topics:
            self.assertEqual(topic["project"], "test-project")

    def test_list_topics_empty_vault_returns_empty(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            topics = mcp_server.list_topics()

        self.assertEqual(topics, [])

    def test_list_topics_includes_sessions_and_importance(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            topics = mcp_server.list_topics()

        self.assertTrue(topics)
        for topic in topics:
            self.assertIn("sessions", topic)
            self.assertIn("importance", topic)
            self.assertIn("path", topic)


class TestReadTopic(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_read_existing_topic(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.read_topic("test-project", "Docker")

        self.assertIn("Docker", content)
        self.assertNotIn("Error:", content)

    def test_read_nonexistent_topic_returns_error_string(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.read_topic("test-project", "NonExistentTopic")

        self.assertIn("Error:", content)

    def test_read_nonexistent_project_returns_error_string(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.read_topic("nonexistent-project", "Docker")

        self.assertIn("Error:", content)

    def test_read_topic_vault_not_configured(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            content = mcp_server.read_topic("any", "topic")

        self.assertIn("Error:", content)


class TestSearchKnowledge(unittest.TestCase):
    def test_search_without_embed_returns_empty_list(self):
        mcp_server = load_mcp_server()

        with mock.patch(
            "obsitocin.memory_query.query", side_effect=RuntimeError("embed missing")
        ):
            result = mcp_server.search_knowledge("Docker containers")

        self.assertEqual(result, [])

    def test_search_returns_results_when_query_succeeds(self):
        mcp_server = load_mcp_server()

        expected = [{"title": "Docker 기초 개념"}]
        with mock.patch("obsitocin.memory_query.query", return_value=expected):
            result = mcp_server.search_knowledge("Docker")

        self.assertEqual(result, expected)


class TestCreateServer(unittest.TestCase):
    def test_create_server_is_exported(self):
        mcp_server = load_mcp_server()

        self.assertTrue(callable(mcp_server.create_server))


class TestGetWorkLog(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_get_existing_work_log(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.get_work_log("2026-04-05")
        self.assertNotIn("Error:", content)
        self.assertIn("작업 로그", content)

    def test_get_nonexistent_date_returns_message(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.get_work_log("1999-01-01")
        self.assertIn("No work log found", content)

    def test_get_work_log_vault_not_configured(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            content = mcp_server.get_work_log("2026-04-05")
        self.assertIn("Error:", content)

    def test_get_today_log_returns_string(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            content = mcp_server.get_work_log()
        self.assertIsInstance(content, str)


class TestSaveInsight(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_insight_creates_topic_note(self):
        mcp_server = load_mcp_server()
        with (
            mock.patch.object(
                mcp_server, "_get_vault_dir", return_value=self.vault_dir
            ),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault_dir),
        ):
            result = mcp_server.save_insight("test-project", "Redis", ["인메모리 캐시"])
        self.assertTrue(result["success"])
        self.assertEqual(result["project"], "test-project")

    def test_save_insight_roundtrip_with_read_topic(self):
        mcp_server = load_mcp_server()
        with (
            mock.patch.object(
                mcp_server, "_get_vault_dir", return_value=self.vault_dir
            ),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault_dir),
        ):
            mcp_server.save_insight(
                "test-project", "Redis", ["Redis는 인메모리 캐시 DB"]
            )
            content = mcp_server.read_topic("test-project", "Redis")
        self.assertIn("Redis", content)
        self.assertNotIn("Error:", content)

    def test_save_insight_vault_not_configured(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            result = mcp_server.save_insight("p", "t", ["k"])
        self.assertFalse(result["success"])


class TestGetProjectContext(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_returns_string(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            result = mcp_server.get_project_context("test-project")
        self.assertIsInstance(result, str)

    def test_contains_project_name(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            result = mcp_server.get_project_context("test-project")
        self.assertIn("test-project", result)

    def test_contains_docker_topic(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            result = mcp_server.get_project_context("test-project")
        self.assertIn("Docker", result)

    def test_length_under_3000(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            result = mcp_server.get_project_context("test-project")
        self.assertLessEqual(len(result), 3000)

    def test_vault_not_configured(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            result = mcp_server.get_project_context("test-project")
        self.assertIn("Error:", result)

    def test_nonexistent_project(self):
        mcp_server = load_mcp_server()
        with mock.patch.object(
            mcp_server, "_get_vault_dir", return_value=self.vault_dir
        ):
            result = mcp_server.get_project_context("nonexistent-project-xyz")
        self.assertIsInstance(result, str)


class TestAskWiki(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(create_test_vault(self.tmp_dir.name))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_ask_returns_answer(self):
        mcp_server = load_mcp_server()

        with (
            mock.patch.object(
                mcp_server, "_get_vault_dir", return_value=self.vault_dir
            ),
            mock.patch(
                "obsitocin.provider.run_provider_prompt",
                return_value="Docker는 컨테이너 기반 가상화 기술입니다.",
            ),
        ):
            result = mcp_server.ask_wiki("Docker란?", project="test-project")
        self.assertIn("answer", result)
        self.assertIn("sources", result)
        self.assertIsInstance(result["answer"], str)
        self.assertTrue(len(result["answer"]) > 0)

    def test_ask_with_save(self):
        mcp_server = load_mcp_server()

        with (
            mock.patch.object(
                mcp_server, "_get_vault_dir", return_value=self.vault_dir
            ),
            mock.patch(
                "obsitocin.provider.run_provider_prompt",
                return_value="Docker는 컨테이너 기술입니다.",
            ),
            mock.patch("obsitocin.topic_writer.OBS_DIR", self.vault_dir),
        ):
            result = mcp_server.ask_wiki(
                "Docker란?", project="test-project", save_to_wiki=True
            )
        self.assertTrue(result["saved"])
        self.assertIsNotNone(result["saved_path"])

    def test_ask_empty_vault(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(
            mcp_server,
            "_get_vault_dir",
            return_value=Path(self.tmp_dir.name) / "empty",
        ):
            result = mcp_server.ask_wiki("anything")
        self.assertIn("없습니다", result["answer"])

    def test_ask_vault_not_configured(self):
        mcp_server = load_mcp_server()

        with mock.patch.object(mcp_server, "_get_vault_dir", return_value=None):
            result = mcp_server.ask_wiki("test")
        self.assertIn("Error:", result["answer"])


if __name__ == "__main__":
    unittest.main()
