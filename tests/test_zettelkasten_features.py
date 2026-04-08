import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock
import importlib
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obsitocin.concepts import build_concept_catalog, concept_note_stem
from obsitocin import memory_query
from obsitocin import obsidian_writer as ow
from obsitocin import cli
from obsitocin import hooks
from obsitocin import maintenance
from obsitocin import processor
from obsitocin import embeddings
from obsitocin import provider
from obsitocin.identity import compute_content_hash
from obsitocin.pii import PIIDetector, risk_meets_threshold


class ConceptCatalogTests(unittest.TestCase):
    def test_build_concept_catalog_merges_aliases(self) -> None:
        qa_list = [
            {"tagging_result": {"key_concepts": ["파이썬 가상환경 (venv)"]}},
            {"tagging_result": {"key_concepts": ["파이썬 가상환경"]}},
        ]

        catalog = build_concept_catalog(qa_list)

        self.assertIn("파이썬 가상환경", catalog["concepts"])
        self.assertEqual(catalog["alias_to_canonical"].get("venv"), "파이썬 가상환경")

    def test_build_concept_catalog_prefers_human_readable_canonical_name(self) -> None:
        qa_list = [
            {"tagging_result": {"key_concepts": ["Docker"]}},
            {"tagging_result": {"key_concepts": ["DOCKER"]}},
        ]

        catalog = build_concept_catalog(qa_list)

        self.assertIn("Docker", catalog["concepts"])
        self.assertNotIn("DOCKER", catalog["concepts"])

    def test_build_concept_catalog_keeps_special_character_concepts_distinct(
        self,
    ) -> None:
        qa_list = [
            {"tagging_result": {"key_concepts": ["C"]}},
            {"tagging_result": {"key_concepts": ["C#"]}},
            {"tagging_result": {"key_concepts": ["C++"]}},
        ]

        catalog = build_concept_catalog(qa_list)

        self.assertIn("C", catalog["concepts"])
        self.assertIn("C#", catalog["concepts"])
        self.assertIn("C++", catalog["concepts"])

    def test_concept_note_stem_avoids_collisions_for_similar_labels(self) -> None:
        self.assertNotEqual(concept_note_stem("CI/CD"), concept_note_stem("CICD"))
        self.assertNotEqual(concept_note_stem("A:B"), concept_note_stem("AB"))


class WriterTests(unittest.TestCase):
    def test_concept_note_preserves_user_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            concept_dir = Path(tmpdir) / "concepts"
            concept_dir.mkdir(parents=True)

            existing = concept_dir / "파이썬 가상환경.md"
            existing.write_text(
                "---\ncreated: 2026-04-01\n---\n"
                "## User Notes\n\n"
                f"{ow.USER_NOTES_START}\n"
                "직접 적은 메모\n"
                f"{ow.USER_NOTES_END}\n"
            )

            qa = {
                "session_id": "ses-demo-1",
                "timestamp": "2026-04-05T10:00:00",
                "cwd": "/tmp/demo-project",
                "tagging_result": {
                    "title": "가상환경 정리",
                    "summary": "파이썬 가상환경 생성과 활성화 흐름을 정리했다.",
                    "tags": ["python", "venv"],
                    "category": "development",
                    "memory_type": "static",
                    "importance": 4,
                    "canonical_concepts": ["파이썬 가상환경"],
                },
            }

            with mock.patch.object(ow, "CONCEPTS_DIR", concept_dir):
                path = ow.write_concept_note(
                    "파이썬 가상환경",
                    [qa],
                    ["파이썬 가상환경", "venv"],
                    None,
                )

            content = path.read_text()
            self.assertIn("직접 적은 메모", content)
            self.assertIn("[[00-projects/demo-project/", content)
            self.assertIn("type: permanent-note", content)
            self.assertIn("- 파이썬 가상환경 생성과 활성화 흐름을 정리했다.", content)

    def test_update_moc_uses_exact_session_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            kg_dir = Path(tmpdir) / "obsitocin"
            kg_dir.mkdir(parents=True)
            moc_path = kg_dir / "_MOC.md"

            with (
                mock.patch.object(ow, "OBS_DIR", kg_dir),
                mock.patch.object(ow, "MOC_PATH", moc_path),
            ):
                ow.update_moc(
                    ["2026-04-05"],
                    ["파이썬 가상환경"],
                    ["00-projects/demo-project/2026-04-05_note_deadbeef"],
                )

            content = moc_path.read_text()
            self.assertIn(
                "[[00-projects/demo-project/2026-04-05_note_deadbeef]]", content
            )
            self.assertNotIn("*", content)

    def test_concept_note_uses_earliest_reference_date_on_first_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            concept_dir = Path(tmpdir) / "concepts"
            concept_dir.mkdir(parents=True)

            qa = {
                "session_id": "ses-demo-2",
                "timestamp": "2026-03-01T08:00:00",
                "cwd": "/tmp/demo-project",
                "tagging_result": {
                    "title": "개념 생성",
                    "summary": "오래된 세션 기준으로 created 날짜를 잡아야 한다.",
                    "tags": ["python"],
                    "category": "development",
                    "memory_type": "static",
                    "importance": 4,
                    "canonical_concepts": ["생성일 테스트"],
                },
            }

            with mock.patch.object(ow, "CONCEPTS_DIR", concept_dir):
                path = ow.write_concept_note(
                    "생성일 테스트", [qa], ["생성일 테스트"], None
                )

            content = path.read_text()
            self.assertIn("created: 2026-03-01", content)


class QueryConceptTests(unittest.TestCase):
    def test_query_concepts_aggregates_to_canonical_name(self) -> None:
        fake_results = [
            {"topics": ["파이썬 가상환경 (venv)"], "similarity": 0.9},
            {"topics": ["파이썬 가상환경"], "similarity": 0.6},
        ]
        qa_entries = [
            (
                "file1",
                {"tagging_result": {"key_concepts": ["파이썬 가상환경 (venv)"]}},
            ),
            (
                "file2",
                {"tagging_result": {"key_concepts": ["파이썬 가상환경"]}},
            ),
        ]

        with (
            mock.patch.object(memory_query, "query", return_value=fake_results),
            mock.patch.object(
                memory_query, "_load_all_written_qas", return_value=qa_entries
            ),
        ):
            results = memory_query.query_concepts("venv", top_k=5)

        self.assertEqual(results[0]["concept"], "파이썬 가상환경")
        self.assertEqual(results[0]["occurrences"], 2)

    def test_query_auto_builds_missing_index_entries(self) -> None:
        qa_entries = [
            (
                "file1",
                {"tagging_result": {"title": "A", "summary": "B", "key_concepts": []}},
            ),
            (
                "file2",
                {"tagging_result": {"title": "C", "summary": "D", "key_concepts": []}},
            ),
        ]
        built = []

        def fake_build(items):
            built.append(items)
            return 2

        with (
            mock.patch.object(memory_query, "_db_has_entries", return_value=False),
            mock.patch.object(
                memory_query, "_load_all_written_qas", return_value=qa_entries
            ),
            mock.patch.object(memory_query, "is_configured", return_value=True),
            mock.patch.object(memory_query, "start_embed_server"),
            mock.patch.object(memory_query, "stop_embed_server"),
            mock.patch.object(
                memory_query,
                "load_index",
                side_effect=[
                    {"entries": {}},
                    {
                        "entries": {
                            "file1": {"embedding": [1.0]},
                            "file2": {"embedding": [1.0]},
                        }
                    },
                ],
            ),
            mock.patch.object(
                memory_query, "build_embeddings_for_qas", side_effect=fake_build
            ),
            mock.patch.object(memory_query, "get_embedding", return_value=[1.0]),
            mock.patch.object(memory_query, "cosine_similarity", return_value=0.8),
        ):
            results = memory_query.query("anything", top_k=2)

        self.assertEqual(len(built), 1)
        self.assertEqual(len(results), 2)

    def test_query_rebuilds_only_missing_file_ids(self) -> None:
        qa_entries = [
            (
                "file1",
                {"tagging_result": {"title": "A", "summary": "B", "key_concepts": []}},
            ),
            (
                "file2",
                {"tagging_result": {"title": "C", "summary": "D", "key_concepts": []}},
            ),
        ]
        built = []

        with (
            mock.patch.object(memory_query, "_db_has_entries", return_value=False),
            mock.patch.object(
                memory_query, "_load_all_written_qas", return_value=qa_entries
            ),
            mock.patch.object(memory_query, "is_configured", return_value=True),
            mock.patch.object(memory_query, "start_embed_server"),
            mock.patch.object(memory_query, "stop_embed_server"),
            mock.patch.object(
                memory_query,
                "load_index",
                side_effect=[
                    {"entries": {"file1": {"embedding": [1.0]}}},
                    {
                        "entries": {
                            "file1": {"embedding": [1.0]},
                            "file2": {"embedding": [1.0]},
                        }
                    },
                ],
            ),
            mock.patch.object(
                memory_query,
                "build_embeddings_for_qas",
                side_effect=lambda items: built.extend(items) or 1,
            ),
            mock.patch.object(memory_query, "get_embedding", return_value=[1.0]),
            mock.patch.object(memory_query, "cosine_similarity", return_value=0.8),
        ):
            results = memory_query.query("anything", top_k=2)

        self.assertEqual(built, [("file2", qa_entries[1][1])])
        self.assertEqual(len(results), 2)


class GraphScriptTests(unittest.TestCase):
    def test_graph_uses_single_exact_path_concept_namespace(self) -> None:
        graph_image = importlib.import_module("scripts.gen_graph_image")

        class FakeGraph:
            def __init__(self):
                self.nodes = {}
                self.edges = set()

            def add_node(self, node_id, **attrs):
                self.nodes[node_id] = attrs

            def has_node(self, node_id):
                return node_id in self.nodes

            def add_edge(self, left, right, **_attrs):
                self.edges.add((left, right))

        class FakeNx:
            @staticmethod
            def Graph():
                return FakeGraph()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            projects = root / "00-projects" / "demo"
            concepts = root / "20-resources" / "concepts"
            daily = root / "30-archives" / "daily"
            projects.mkdir(parents=True)
            concepts.mkdir(parents=True)
            daily.mkdir(parents=True)

            (projects / "session.md").write_text(
                "**Key Concepts**: [[20-resources/concepts/C++|C++]], [[20-resources/concepts/C#|C#]]\n"
            )
            (concepts / "C++.md").write_text(
                "## Related Concepts\n\n- [[20-resources/concepts/C#|C#]]\n"
            )
            (concepts / "C#.md").write_text("# C#\n")
            (daily / "2026-04-05.md").write_text(
                "[[00-projects/demo/session]]\n[[20-resources/concepts/C++|C++]]\n"
            )

            with (
                mock.patch.object(graph_image, "SESSIONS_DIR", root / "00-projects"),
                mock.patch.object(graph_image, "CONCEPTS_DIR", concepts),
                mock.patch.object(graph_image, "DAILY_DIR", daily),
                mock.patch.object(
                    graph_image,
                    "_load_graph_deps",
                    return_value=(None, None, None, FakeNx()),
                ),
            ):
                graph = graph_image.build_graph()

        self.assertIn("20-resources/concepts/C++", graph.nodes)
        self.assertIn("20-resources/concepts/C#", graph.nodes)
        self.assertNotIn("concepts/C++", graph.nodes)
        self.assertIn(("daily/2026-04-05", "20-resources/concepts/C++"), graph.edges)


class BootstrapTests(unittest.TestCase):
    def test_build_hook_command_uses_requested_python(self) -> None:
        command = hooks.build_hook_command("/tmp/custom-python")
        self.assertIn("/tmp/custom-python", command)
        self.assertIn("obsitocin.qa_logger", command)

    def test_hook_python_path_matches_platform(self) -> None:
        venv_dir = Path("/tmp/demo-venv")
        expected = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        self.assertEqual(str(cli._hook_python_path(venv_dir)), str(venv_dir / expected))

    def test_provider_registry_includes_qwen(self) -> None:
        with (
            mock.patch.object(
                provider, "LLAMA_SERVER_BIN", "/usr/local/bin/llama-server"
            ),
            mock.patch.object(
                provider, "QWEN_MODEL_PATH", Path("/tmp/Qwen3.5-4B-Q4_K_M.gguf")
            ),
        ):
            info = provider.get_provider("qwen")

        self.assertEqual(info.name, "qwen")
        self.assertEqual(info.cli_bin, "/usr/local/bin/llama-server")
        self.assertIn("Qwen3.5-4B-Q4_K_M", info.model)


class PipelineSafetyTests(unittest.TestCase):
    def test_pii_detector_scans_and_redacts_api_key(self) -> None:
        detector = PIIDetector()
        sample = "token=sk-abcdefghijklmnopqrstuvwxyz123456"

        result = detector.scan(sample)

        self.assertTrue(result.detected)
        self.assertEqual(result.risk_level, "high")
        self.assertIn("api_key", result.pii_types)
        self.assertIn("[REDACTED-API-KEY]", detector.redact(sample))

    def test_risk_threshold_comparison(self) -> None:
        self.assertTrue(risk_meets_threshold("high", "medium"))
        self.assertTrue(risk_meets_threshold("medium", "medium"))
        self.assertFalse(risk_meets_threshold("low", "medium"))

    def test_process_file_uses_fallback_tagging_when_provider_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir) / "queue"
            processed_dir = Path(tmpdir) / "processed"
            queue_dir.mkdir(parents=True)
            processed_dir.mkdir(parents=True)
            queue_file = queue_dir / "item.json"
            queue_file.write_text(
                json.dumps(
                    {
                        "session_id": "ses-1",
                        "timestamp": "2026-04-05T10:00:00",
                        "cwd": "/tmp/project",
                        "prompt": "Python API 에러를 디버깅했다",
                        "response": "traceback과 fix를 정리했다",
                        "content_hash": compute_content_hash(
                            "Python API 에러를 디버깅했다",
                            "traceback과 fix를 정리했다",
                            "/tmp/project",
                        ),
                        "status": "pending",
                        "transcript_path": "",
                    },
                    ensure_ascii=False,
                )
            )

            with (
                mock.patch.object(processor, "QUEUE_DIR", queue_dir),
                mock.patch.object(processor, "PROCESSED_DIR", processed_dir),
                mock.patch.object(processor, "call_tagging", return_value=None),
            ):
                ok = processor.process_file(queue_file, provider_name="claude")

            self.assertTrue(ok)
            written = json.loads((processed_dir / "item.json").read_text())
            self.assertTrue(written["tagging_fallback"])
            self.assertIn(written["status"], ("processed", "written"))
            self.assertIn("developer-qna", written["tagging_result"]["tags"])

    def test_process_file_marks_duplicates_by_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = Path(tmpdir) / "queue"
            processed_dir = Path(tmpdir) / "processed"
            queue_dir.mkdir(parents=True)
            processed_dir.mkdir(parents=True)
            content_hash = compute_content_hash(
                "same prompt", "same response", "/tmp/project"
            )

            (processed_dir / "existing.json").write_text(
                json.dumps(
                    {
                        "content_hash": content_hash,
                        "status": "written",
                        "tagging_result": {"title": "existing"},
                    },
                    ensure_ascii=False,
                )
            )
            queue_file = queue_dir / "incoming.json"
            queue_file.write_text(
                json.dumps(
                    {
                        "session_id": "ses-2",
                        "timestamp": "2026-04-05T10:00:00",
                        "cwd": "/tmp/project",
                        "prompt": "same prompt",
                        "response": "same response",
                        "content_hash": content_hash,
                        "status": "pending",
                    },
                    ensure_ascii=False,
                )
            )

            with (
                mock.patch.object(processor, "QUEUE_DIR", queue_dir),
                mock.patch.object(processor, "PROCESSED_DIR", processed_dir),
            ):
                ok = processor.process_file(queue_file, provider_name="claude")

            self.assertTrue(ok)
            duplicate = json.loads((processed_dir / "incoming.json").read_text())
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(duplicate["duplicate_of"], "existing.json")

    def test_verify_state_reports_orphan_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = Path(tmpdir) / "processed"
            processed_dir.mkdir(parents=True)
            index_path = Path(tmpdir) / "embeddings.json"
            index_path.write_text(
                json.dumps(
                    {
                        "entries": {
                            "missing": {"embedding": [1.0]},
                        }
                    }
                )
            )

            with (
                mock.patch.object(maintenance, "PROCESSED_DIR", processed_dir),
                mock.patch.object(maintenance, "EMBEDDINGS_INDEX_PATH", index_path),
                mock.patch.object(maintenance, "QUEUE_DIR", Path(tmpdir) / "queue"),
            ):
                report = maintenance.verify_state()

            self.assertEqual(report["orphan_embeddings"], ["missing"])

    def test_config_validation_reports_invalid_provider(self) -> None:
        import obsitocin.config as config_module

        with mock.patch.dict(
            os.environ, {"OBS_LLM_PROVIDER": "invalid-provider"}, clear=False
        ):
            reloaded = importlib.reload(config_module)
            try:
                self.assertIn(
                    "Invalid llm_provider",
                    " ".join(reloaded.get_config_validation_errors()),
                )
                self.assertEqual(reloaded.LLM_PROVIDER, "claude")
            finally:
                importlib.reload(config_module)


class EmbeddingBackendTests(unittest.TestCase):
    def test_embeddings_is_not_configured_without_model(self) -> None:
        with mock.patch.object(embeddings, "EMBED_MODEL_PATH", Path("")):
            self.assertFalse(embeddings.is_configured())

    def test_build_embeddings_for_qas_uses_batch_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "embeddings.json"
            qa_files = [
                (
                    "file1",
                    {
                        "tagging_result": {
                            "title": "제목",
                            "summary": "요약",
                            "tags": ["python"],
                            "key_concepts": ["개념"],
                        },
                        "prompt": "질문",
                        "response": "답변",
                    },
                )
            ]

            with (
                mock.patch.object(embeddings, "EMBEDDINGS_INDEX_PATH", index_path),
                mock.patch.object(
                    embeddings, "get_embeddings_batch", return_value=[[0.1, 0.2, 0.3]]
                ),
            ):
                count = embeddings.build_embeddings_for_qas(qa_files)
                saved = json.loads(index_path.read_text())

            self.assertEqual(count, 1)
            self.assertEqual(saved["dimensions"], 3)
            self.assertIn("file1", saved["entries"])

    def test_build_embeddings_for_qas_falls_back_to_individual_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "embeddings.json"
            qa_files = [
                (
                    "file1",
                    {
                        "tagging_result": {"title": "제목", "summary": "요약"},
                        "prompt": "질문",
                        "response": "답변",
                    },
                )
            ]

            with (
                mock.patch.object(embeddings, "EMBEDDINGS_INDEX_PATH", index_path),
                mock.patch.object(
                    embeddings,
                    "get_embeddings_batch",
                    side_effect=RuntimeError("batch failed"),
                ),
                mock.patch.object(embeddings, "get_embedding", return_value=[0.4, 0.5]),
            ):
                count = embeddings.build_embeddings_for_qas(qa_files)
                saved = json.loads(index_path.read_text())

            self.assertEqual(count, 1)
            self.assertEqual(saved["dimensions"], 2)

    def test_query_starts_and_stops_embed_server(self) -> None:
        qa_entries = [
            (
                "file1",
                {
                    "timestamp": "2026-04-05T10:00:00",
                    "cwd": "/tmp/project",
                    "tagging_result": {
                        "title": "제목",
                        "summary": "요약",
                        "memory_type": "dynamic",
                        "importance": 3,
                        "category": "development",
                        "key_concepts": [],
                        "tags": [],
                    },
                },
            )
        ]
        with (
            mock.patch.object(memory_query, "is_configured", return_value=True),
            mock.patch.object(
                memory_query, "_load_all_written_qas", return_value=qa_entries
            ),
            mock.patch.object(
                memory_query,
                "load_index",
                return_value={"entries": {"file1": {"embedding": [1.0, 0.0]}}},
            ),
            mock.patch.object(memory_query, "start_embed_server") as start_mock,
            mock.patch.object(memory_query, "stop_embed_server") as stop_mock,
            mock.patch.object(memory_query, "get_embedding", return_value=[1.0, 0.0]),
            mock.patch.object(memory_query, "cosine_similarity", return_value=0.95),
        ):
            results = memory_query.query("테스트", top_k=1)

        self.assertEqual(len(results), 1)
        start_mock.assert_called_once()
        stop_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
