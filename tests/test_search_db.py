"""Tests for search_db module — SQLite + FTS5 search database."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obsitocin.search_db import (
    bm25_search,
    delete_qa_entry,
    ensure_schema,
    get_connection,
    get_db_stats,
    get_qa_entry,
    get_schema_version,
    migrate_from_json,
    pack_embedding,
    store_chunk_embeddings,
    unpack_embedding,
    upsert_chunks,
    upsert_qa_entry,
    vector_search,
)


def _make_metadata(**overrides):
    base = {
        "title": "Docker 컨테이너 설정",
        "work_summary": "Docker 이미지 빌드 설정 완료",
        "category": "devops",
        "importance": 4,
        "memory_type": "static",
        "tags": ["docker", "devops"],
        "key_concepts": ["Docker", "컨테이너"],
        "project": "test-project",
        "timestamp": "2026-04-09T10:00:00",
        "content_hash": "abc123",
        "source_type": "qa",
        "full_text": "Docker 컨테이너 이미지 빌드 설정",
    }
    base.update(overrides)
    return base


def _mem_conn():
    """Create an in-memory SQLite connection with schema."""
    conn = get_connection(Path(":memory:"))
    ensure_schema(conn)
    return conn


class TestSchema(unittest.TestCase):
    def test_ensure_schema_idempotent(self):
        conn = _mem_conn()
        ensure_schema(conn)  # call again — should not error
        self.assertEqual(get_schema_version(conn), 1)

    def test_schema_version(self):
        conn = _mem_conn()
        self.assertEqual(get_schema_version(conn), 1)


class TestVectorPacking(unittest.TestCase):
    def test_round_trip(self):
        vec = [0.1, 0.2, -0.5, 1.0, 0.0]
        blob = pack_embedding(vec)
        restored = unpack_embedding(blob)
        self.assertEqual(len(vec), len(restored))
        for a, b in zip(vec, restored):
            self.assertAlmostEqual(a, b, places=5)

    def test_empty_vector(self):
        blob = pack_embedding([])
        self.assertEqual(unpack_embedding(blob), [])

    def test_blob_size(self):
        vec = [1.0] * 384
        blob = pack_embedding(vec)
        self.assertEqual(len(blob), 384 * 4)  # float32 = 4 bytes


class TestQaEntries(unittest.TestCase):
    def test_upsert_insert(self):
        conn = _mem_conn()
        rowid = upsert_qa_entry(conn, "test-001", _make_metadata())
        conn.commit()
        self.assertGreater(rowid, 0)

        entry = get_qa_entry(conn, "test-001")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["title"], "Docker 컨테이너 설정")
        self.assertEqual(entry["importance"], 4)

    def test_upsert_update(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata(title="Old Title"))
        conn.commit()
        upsert_qa_entry(conn, "test-001", _make_metadata(title="New Title"))
        conn.commit()

        entry = get_qa_entry(conn, "test-001")
        self.assertEqual(entry["title"], "New Title")

    def test_delete_cascades(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata())
        chunk_ids = upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "hello", "text_hash": "h1"}
        ])
        store_chunk_embeddings(conn, [(chunk_ids[0], [0.1, 0.2, 0.3])])
        conn.commit()

        deleted = delete_qa_entry(conn, "test-001")
        conn.commit()
        self.assertTrue(deleted)
        self.assertIsNone(get_qa_entry(conn, "test-001"))

        chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE file_id='test-001'").fetchone()[0]
        self.assertEqual(chunks, 0)

    def test_get_nonexistent(self):
        conn = _mem_conn()
        self.assertIsNone(get_qa_entry(conn, "nonexistent"))

    def test_tags_stored_as_json(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata(tags=["a", "b"]))
        conn.commit()
        entry = get_qa_entry(conn, "test-001")
        tags = json.loads(entry["tags"])
        self.assertEqual(tags, ["a", "b"])


class TestChunksAndEmbeddings(unittest.TestCase):
    def test_upsert_chunks(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata())
        chunk_ids = upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "part1", "text_hash": "h1"},
            {"chunk_index": 1, "chunk_text": "part2", "text_hash": "h2"},
        ])
        conn.commit()
        self.assertEqual(len(chunk_ids), 2)

    def test_upsert_replaces_chunks(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata())
        upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "old", "text_hash": "h1"},
        ])
        conn.commit()

        # Replace with new chunks
        chunk_ids = upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "new", "text_hash": "h2"},
        ])
        conn.commit()

        row = conn.execute(
            "SELECT chunk_text FROM chunks WHERE chunk_id=?", (chunk_ids[0],)
        ).fetchone()
        self.assertEqual(row[0], "new")

    def test_store_and_retrieve_embedding(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata())
        chunk_ids = upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "text", "text_hash": "h1"},
        ])
        vec = [0.1, 0.2, 0.3]
        store_chunk_embeddings(conn, [(chunk_ids[0], vec)])
        conn.commit()

        blob = conn.execute(
            "SELECT embedding FROM embeddings WHERE chunk_id=?", (chunk_ids[0],)
        ).fetchone()[0]
        restored = unpack_embedding(blob)
        self.assertEqual(len(restored), 3)
        self.assertAlmostEqual(restored[0], 0.1, places=5)


class TestBM25Search(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()
        upsert_qa_entry(self.conn, "docker-001", _make_metadata(
            title="Docker 컨테이너 설정",
            full_text="Docker 컨테이너 이미지 빌드 배포 설정",
            project="infra",
            importance=4,
        ))
        upsert_qa_entry(self.conn, "python-001", _make_metadata(
            title="Python 가상환경 설정",
            full_text="Python venv virtualenv 가상환경 pip 패키지 설치",
            project="backend",
            importance=3,
            category="development",
        ))
        upsert_qa_entry(self.conn, "react-001", _make_metadata(
            title="React 컴포넌트 패턴",
            full_text="React hooks useState useEffect 컴포넌트 렌더링",
            project="frontend",
            importance=2,
            category="development",
        ))
        self.conn.commit()

    def test_basic_search(self):
        results = bm25_search(self.conn, "Docker")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["file_id"], "docker-001")

    def test_search_returns_metadata(self):
        results = bm25_search(self.conn, "Docker")
        self.assertIn("title", results[0])
        self.assertIn("bm25_score", results[0])
        self.assertIn("project", results[0])

    def test_no_match(self):
        results = bm25_search(self.conn, "xyznonexistent")
        self.assertEqual(len(results), 0)

    def test_filter_importance(self):
        results = bm25_search(self.conn, "설정", filters={"importance_min": 4})
        file_ids = [r["file_id"] for r in results]
        self.assertIn("docker-001", file_ids)
        self.assertNotIn("react-001", file_ids)

    def test_filter_project(self):
        results = bm25_search(self.conn, "설정", filters={"project": "backend"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file_id"], "python-001")

    def test_filter_category(self):
        results = bm25_search(self.conn, "설정 컴포넌트", filters={"category": "development"})
        file_ids = [r["file_id"] for r in results]
        self.assertNotIn("docker-001", file_ids)

    def test_top_k_limit(self):
        results = bm25_search(self.conn, "설정", top_k=1)
        self.assertLessEqual(len(results), 1)

    def test_empty_query(self):
        results = bm25_search(self.conn, "")
        self.assertEqual(len(results), 0)


class TestVectorSearch(unittest.TestCase):
    def setUp(self):
        self.conn = _mem_conn()
        # Create entries with embeddings
        for i, (fid, title, vec) in enumerate([
            ("doc-001", "Docker", [1.0, 0.0, 0.0]),
            ("py-001", "Python", [0.0, 1.0, 0.0]),
            ("react-001", "React", [0.0, 0.0, 1.0]),
        ]):
            upsert_qa_entry(self.conn, fid, _make_metadata(title=title))
            chunk_ids = upsert_chunks(self.conn, fid, [
                {"chunk_index": 0, "chunk_text": title, "text_hash": f"h{i}"}
            ])
            store_chunk_embeddings(self.conn, [(chunk_ids[0], vec)])
        self.conn.commit()

    def test_exact_match(self):
        results = vector_search(self.conn, [1.0, 0.0, 0.0], top_k=3)
        self.assertEqual(results[0]["file_id"], "doc-001")
        self.assertAlmostEqual(results[0]["similarity"], 1.0, places=3)

    def test_no_match(self):
        results = vector_search(self.conn, [0.0, 0.0, 0.0], top_k=3)
        # All similarities should be 0
        for r in results:
            self.assertAlmostEqual(r["similarity"], 0.0, places=3)

    def test_top_k(self):
        results = vector_search(self.conn, [0.5, 0.5, 0.0], top_k=1)
        self.assertEqual(len(results), 1)

    def test_aggregates_chunks(self):
        """Multiple chunks per entry → max similarity wins."""
        upsert_qa_entry(self.conn, "multi-001", _make_metadata(title="Multi"))
        chunk_ids = upsert_chunks(self.conn, "multi-001", [
            {"chunk_index": 0, "chunk_text": "c0", "text_hash": "h0"},
            {"chunk_index": 1, "chunk_text": "c1", "text_hash": "h1"},
        ])
        store_chunk_embeddings(self.conn, [
            (chunk_ids[0], [0.1, 0.0, 0.0]),  # low sim to query
            (chunk_ids[1], [0.9, 0.1, 0.0]),  # high sim to query
        ])
        self.conn.commit()

        results = vector_search(self.conn, [1.0, 0.0, 0.0], top_k=10)
        multi = [r for r in results if r["file_id"] == "multi-001"]
        self.assertEqual(len(multi), 1)
        # Should use the better chunk's similarity
        self.assertGreater(multi[0]["similarity"], 0.9)


class TestMigration(unittest.TestCase):
    def test_migrate_from_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Create mock embeddings.json
            index = {
                "model": "test-model",
                "dimensions": 3,
                "entries": {
                    "qa-001": {
                        "embedding": [0.1, 0.2, 0.3],
                        "text_hash": "hash1",
                        "created_at": "2026-04-09T10:00:00",
                    },
                    "topic:proj:Docker": {
                        "embedding": [0.4, 0.5, 0.6],
                        "text_hash": "hash2",
                        "created_at": "2026-04-09T11:00:00",
                        "source_type": "topic_note",
                    },
                },
            }
            index_path = tmp_path / "embeddings.json"
            index_path.write_text(json.dumps(index))

            # Create mock processed dir
            processed_dir = tmp_path / "processed"
            processed_dir.mkdir()
            qa = {
                "timestamp": "2026-04-09T10:00:00",
                "cwd": "/work/test-project",
                "content_hash": "chash1",
                "source_type": "claude_code",
                "tagging_result": {
                    "title": "Test QA",
                    "work_summary": "Test summary",
                    "tags": ["test"],
                    "key_concepts": ["Testing"],
                    "category": "testing",
                    "importance": 3,
                },
            }
            (processed_dir / "qa-001.json").write_text(json.dumps(qa))

            db_path = tmp_path / "search.db"
            result = migrate_from_json(index_path, processed_dir, db_path)

            self.assertEqual(result["entries_migrated"], 2)
            self.assertEqual(result["chunks_created"], 2)
            self.assertEqual(len(result["errors"]), 0)

            # Verify data in DB
            conn = get_connection(db_path)
            stats = get_db_stats(conn)
            self.assertEqual(stats["entries"], 2)
            self.assertEqual(stats["embeddings"], 2)

            # Check topic note entry
            topic = get_qa_entry(conn, "topic:proj:Docker")
            self.assertIsNotNone(topic)
            self.assertEqual(topic["source_type"], "topic_note")
            conn.close()

    def test_migrate_missing_file(self):
        result = migrate_from_json(
            Path("/nonexistent/embeddings.json"),
            Path("/nonexistent/processed"),
            Path("/tmp/test.db"),
        )
        self.assertEqual(result["entries_migrated"], 0)
        self.assertGreater(len(result["errors"]), 0)


class TestDbStats(unittest.TestCase):
    def test_empty_db(self):
        conn = _mem_conn()
        stats = get_db_stats(conn)
        self.assertEqual(stats["entries"], 0)
        self.assertEqual(stats["chunks"], 0)
        self.assertEqual(stats["embeddings"], 0)

    def test_with_data(self):
        conn = _mem_conn()
        upsert_qa_entry(conn, "test-001", _make_metadata())
        chunk_ids = upsert_chunks(conn, "test-001", [
            {"chunk_index": 0, "chunk_text": "text", "text_hash": "h1"}
        ])
        store_chunk_embeddings(conn, [(chunk_ids[0], [0.1, 0.2])])
        conn.commit()

        stats = get_db_stats(conn)
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["chunks"], 1)
        self.assertEqual(stats["embeddings"], 1)


if __name__ == "__main__":
    unittest.main()
