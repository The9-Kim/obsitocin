import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PROCESSOR_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "obsitocin" / "processor.py"
)
SPEC = importlib.util.spec_from_file_location("test_processor_module", PROCESSOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Failed to load processor module from {PROCESSOR_PATH}")

PROCESSOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROCESSOR)
build_tagging_prompt = PROCESSOR.build_tagging_prompt


class TestBuildTaggingPromptLegacy(unittest.TestCase):
    def test_qa_prompt_contains_question_label(self):
        p = build_tagging_prompt({"prompt": "test question", "response": "test answer"})
        self.assertIn("질문:", p)

    def test_qa_prompt_contains_json_schema(self):
        p = build_tagging_prompt({"prompt": "q", "response": "a"})
        self.assertIn("JSON 구조", p)
        self.assertIn("should_store", p)
        self.assertIn("topics", p)
        self.assertIn("importance", p)

    def test_no_source_type_defaults_to_qa_path(self):
        p = build_tagging_prompt({"prompt": "hello", "response": "world"})
        self.assertIn("질문:", p)

    def test_source_type_claude_code_uses_qa_path(self):
        p = build_tagging_prompt(
            {
                "source_type": "claude_code",
                "prompt": "hello",
                "response": "world",
            }
        )
        self.assertIn("질문:", p)

    def test_qa_prompt_contains_answer_label(self):
        p = build_tagging_prompt({"prompt": "q", "response": "answer text"})
        self.assertIn("answer text", p)


class TestBuildTaggingPromptGeneric(unittest.TestCase):
    def test_slack_source_uses_generic_path(self):
        p = build_tagging_prompt(
            {
                "source_type": "slack",
                "content": "This is a slack message",
                "metadata": {"channel": "#dev"},
            }
        )
        self.assertNotIn("질문:", p)

    def test_generic_prompt_contains_source_type(self):
        p = build_tagging_prompt(
            {"source_type": "slack", "content": "slack message", "metadata": {}}
        )
        self.assertIn("slack", p)

    def test_generic_prompt_contains_json_schema(self):
        p = build_tagging_prompt(
            {
                "source_type": "jira",
                "content": "Fix the login bug",
                "metadata": {"ticket": "PROJ-123"},
            }
        )
        self.assertIn("JSON 구조", p)
        self.assertIn("should_store", p)
        self.assertIn("topics", p)

    def test_generic_prompt_contains_content(self):
        p = build_tagging_prompt(
            {
                "source_type": "manual",
                "content": "Docker uses namespace isolation",
                "metadata": {},
            }
        )
        self.assertIn("Docker uses namespace isolation", p)

    def test_all_non_qa_source_types_use_generic(self):
        for source_type in ("slack", "jira", "confluence", "git", "manual"):
            with self.subTest(source_type=source_type):
                p = build_tagging_prompt(
                    {
                        "source_type": source_type,
                        "content": "test content",
                        "metadata": {},
                    }
                )
                self.assertNotIn("질문:", p, f"{source_type} should use generic path")
                self.assertIn(
                    "JSON 구조", p, f"{source_type} should include JSON schema"
                )


class TestFallbackTaggingResultSourceAware(unittest.TestCase):
    def test_manual_source_uses_content_field(self):
        result = PROCESSOR.fallback_tagging_result(
            {
                "source_type": "manual",
                "content": "Docker deploy checklist for CI pipeline",
            }
        )

        self.assertEqual(result["category"], "devops")
        self.assertIn("Docker", result["title"])


class TestQualityFilters(unittest.TestCase):
    def test_filters_agent_operational_meta_topics(self):
        qa = {
            "source_type": "claude_code",
            "prompt": "delegate_task 함수 규칙 알려줘",
            "response": "load_skills=[] 와 run_in_background=true 를 넣어야 합니다.",
        }
        result = PROCESSOR.normalize_result(
            {
                "title": "의뢰 프로토콜",
                "should_store": True,
                "topics": [
                    {
                        "name": "의뢰 프로토콜",
                        "knowledge": [
                            "delegate_task 함수를 사용해야 함",
                            "load_skills=[] 매개변수를 반드시 포함해야 함",
                            "run_in_background=true 를 설정해야 함",
                        ],
                    }
                ],
                "work_summary": "에이전트 운영 규칙 정리",
                "tags": ["tooling"],
                "category": "tooling",
                "importance": 4,
            }
        )

        filtered, reason = PROCESSOR.apply_quality_filters(result, qa)

        self.assertFalse(filtered["should_store"])
        self.assertEqual(filtered["topics"], [])
        self.assertEqual(reason, "agent-operational-meta")

    def test_keeps_project_knowledge_topics(self):
        qa = {
            "source_type": "claude_code",
            "prompt": "vault 에서 topic note 승격 기준을 어떻게 둘까?",
            "response": "importance >= 4 인 경우에만 permanent topic 으로 올리자.",
        }
        result = PROCESSOR.normalize_result(
            {
                "title": "토픽 승격 기준",
                "should_store": True,
                "topics": [
                    {
                        "name": "토픽 승격 기준",
                        "knowledge": ["importance >= 4 인 경우에만 permanent topic 생성"],
                    }
                ],
                "work_summary": "토픽 승격 기준 결정",
                "tags": ["knowledge-base"],
                "category": "architecture",
                "importance": 4,
            }
        )

        filtered, reason = PROCESSOR.apply_quality_filters(result, qa)

        self.assertTrue(filtered["should_store"])
        self.assertEqual(len(filtered["topics"]), 1)
        self.assertIsNone(reason)


class TestProcessFileLegacyCompat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.queue_dir = self.base / "queue"
        self.processed_dir = self.base / "processed"
        self.logs_dir = self.base / "logs"
        self.queue_dir.mkdir()
        self.processed_dir.mkdir()
        self.logs_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_processor_dirs(self):
        return mock.patch.multiple(
            PROCESSOR,
            QUEUE_DIR=self.queue_dir,
            PROCESSED_DIR=self.processed_dir,
            LOGS_DIR=self.logs_dir,
            LOG_FILE=self.logs_dir / "processor.log",
        )

    def _patch_topic_writer(self):
        return mock.patch(
            "obsitocin.topic_writer.write_notes_for_qa",
            return_value={"topics_written": 0},
        )

    def _read_processed(self, filename: str) -> dict:
        return json.loads((self.processed_dir / filename).read_text())

    def test_legacy_queue_processed_with_default_source_type(self):
        qa = {
            "session_id": "test-ses",
            "timestamp": "2026-04-05T10:00:00",
            "cwd": "/tmp/test-project",
            "prompt": "Docker란 무엇인가?",
            "response": "Docker는 컨테이너 플랫폼입니다.",
            "content_hash": "abc123def456abcd",
            "status": "pending",
            "transcript_path": "",
        }
        path = self.queue_dir / "legacy.json"
        path.write_text(json.dumps(qa, ensure_ascii=False))

        mock_result = PROCESSOR.normalize_result(
            {
                "title": "Docker 테스트",
                "should_store": True,
                "topics": [{"name": "Docker", "knowledge": ["컨테이너 기술"]}],
                "work_summary": "Docker 학습",
                "tags": ["docker"],
                "category": "devops",
                "importance": 3,
            }
        )

        with (
            self._patch_processor_dirs(),
            mock.patch.object(PROCESSOR, "call_tagging", return_value=mock_result),
            self._patch_topic_writer(),
        ):
            result = PROCESSOR.process_file(path)

        self.assertTrue(result)
        processed = self._read_processed("legacy.json")
        self.assertEqual(processed["status"], "processed")
        self.assertEqual(processed["source_type"], "claude_code")
        self.assertEqual(processed["content_hash"], "abc123def456abcd")

    def test_claude_code_source_uses_qa_prompt_path(self):
        qa = {
            "session_id": "test-ses-2",
            "timestamp": "2026-04-05T11:00:00",
            "cwd": "/tmp/test-project",
            "prompt": "테스트 질문",
            "response": "테스트 답변",
            "content_hash": "xyz123abc456xyz1",
            "status": "pending",
            "transcript_path": "",
            "source_type": "claude_code",
            "source_metadata": {"session_id": "test-ses-2", "transcript_path": ""},
        }
        path = self.queue_dir / "claude_code.json"
        path.write_text(json.dumps(qa, ensure_ascii=False))

        mock_result = PROCESSOR.normalize_result(
            {
                "title": "테스트",
                "should_store": True,
                "topics": [{"name": "테스트", "knowledge": ["테스트 사실"]}],
                "work_summary": "테스트 작업",
                "tags": ["test"],
                "category": "testing",
                "importance": 2,
            }
        )

        with (
            self._patch_processor_dirs(),
            mock.patch.object(
                PROCESSOR, "call_tagging", return_value=mock_result
            ) as call_tagging,
            self._patch_topic_writer(),
        ):
            result = PROCESSOR.process_file(path)

        self.assertTrue(result)
        call_prompt = call_tagging.call_args.args[0]
        self.assertIn("질문:", call_prompt)
        processed = self._read_processed("claude_code.json")
        self.assertEqual(processed["source_type"], "claude_code")
        self.assertEqual(
            processed["source_metadata"],
            {"session_id": "test-ses-2", "transcript_path": ""},
        )

    def test_manual_source_uses_generic_prompt_and_preserves_metadata(self):
        qa = {
            "session_id": "manual-ses",
            "timestamp": "2026-04-05T12:00:00",
            "cwd": "/tmp/test-project",
            "content": "Docker deployment checklist for CI pipeline rollout",
            "status": "pending",
            "source_type": "manual",
            "source_metadata": {"author": "tester", "channel": "notes"},
        }
        path = self.queue_dir / "manual.json"
        path.write_text(json.dumps(qa, ensure_ascii=False))

        mock_result = PROCESSOR.normalize_result(
            {
                "title": "수동 메모",
                "should_store": True,
                "topics": [{"name": "Docker", "knowledge": ["배포 체크리스트"]}],
                "work_summary": "수동 메모 처리",
                "tags": ["docker"],
                "category": "devops",
                "importance": 3,
            }
        )

        with (
            self._patch_processor_dirs(),
            mock.patch.object(
                PROCESSOR, "call_tagging", return_value=mock_result
            ) as call_tagging,
            self._patch_topic_writer(),
        ):
            result = PROCESSOR.process_file(path)

        self.assertTrue(result)
        call_prompt = call_tagging.call_args.args[0]
        self.assertNotIn("질문:", call_prompt)
        self.assertIn("manual", call_prompt)
        processed = self._read_processed("manual.json")
        self.assertEqual(processed["source_type"], "manual")
        self.assertEqual(
            processed["source_metadata"], {"author": "tester", "channel": "notes"}
        )
        self.assertTrue(processed["content_hash"])

    def test_find_existing_by_content_hash_still_detects_duplicates(self):
        existing = {
            "content_hash": "duplicate-hash",
            "status": "processed",
        }
        (self.processed_dir / "existing.json").write_text(
            json.dumps(existing, ensure_ascii=False)
        )
        queued = {
            "session_id": "dup-ses",
            "timestamp": "2026-04-05T13:00:00",
            "cwd": "/tmp/test-project",
            "prompt": "중복 질문",
            "response": "중복 답변",
            "content_hash": "duplicate-hash",
            "status": "pending",
        }
        path = self.queue_dir / "duplicate.json"
        path.write_text(json.dumps(queued, ensure_ascii=False))

        with self._patch_processor_dirs(), self._patch_topic_writer():
            result = PROCESSOR.process_file(path)

        self.assertTrue(result)
        processed = self._read_processed("duplicate.json")
        self.assertEqual(processed["status"], "duplicate")
        self.assertEqual(processed["duplicate_of"], "existing.json")

    def test_process_file_filters_agent_operational_meta(self):
        qa = {
            "session_id": "meta-ses",
            "timestamp": "2026-04-05T14:00:00",
            "cwd": "/tmp/test-project",
            "prompt": "delegate_task 규칙 알려줘",
            "response": "load_skills=[] 와 run_in_background=true 를 설정하세요.",
            "content_hash": "meta123abc456def7",
            "status": "pending",
            "transcript_path": "",
            "source_type": "claude_code",
            "source_metadata": {},
        }
        path = self.queue_dir / "meta.json"
        path.write_text(json.dumps(qa, ensure_ascii=False))

        mock_result = PROCESSOR.normalize_result(
            {
                "title": "의뢰 프로토콜",
                "should_store": True,
                "topics": [
                    {
                        "name": "의뢰 프로토콜",
                        "knowledge": [
                            "delegate_task 함수를 사용해야 함",
                            "load_skills=[] 매개변수를 반드시 포함해야 함",
                            "run_in_background=true 를 설정해야 함",
                        ],
                    }
                ],
                "work_summary": "에이전트 운영 규칙 정리",
                "tags": ["tooling"],
                "category": "tooling",
                "importance": 4,
            }
        )

        with (
            self._patch_processor_dirs(),
            mock.patch.object(PROCESSOR, "call_tagging", return_value=mock_result),
            self._patch_topic_writer() as write_topics,
        ):
            result = PROCESSOR.process_file(path)

        self.assertTrue(result)
        processed = self._read_processed("meta.json")
        self.assertEqual(processed["status"], "filtered")
        self.assertEqual(processed["filter_reason"], "agent-operational-meta")
        write_topics.assert_not_called()
