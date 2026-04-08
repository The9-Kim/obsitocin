"""Tests for hybrid_search module — RRF fusion + hybrid query."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obsitocin.hybrid_search import hybrid_query, rrf_combine
from obsitocin.search_db import (
    ensure_schema,
    get_connection,
    store_chunk_embeddings,
    upsert_chunks,
    upsert_qa_entry,
)


def _make_bm25_result(file_id, score=-1.0, **extra):
    return {"file_id": file_id, "bm25_score": score, "title": file_id, **extra}


def _make_vec_result(file_id, sim=0.9, **extra):
    return {"file_id": file_id, "similarity": sim, "title": file_id, **extra}


class TestRRFCombine(unittest.TestCase):
    def test_overlapping_results_rank_higher(self):
        bm25 = [_make_bm25_result("A"), _make_bm25_result("B"), _make_bm25_result("C")]
        vec = [_make_vec_result("B"), _make_vec_result("A"), _make_vec_result("D")]
        combined = rrf_combine(bm25, vec, k=60)

        # A and B appear in both, so they should rank higher than C and D
        top_ids = [r["file_id"] for r in combined[:2]]
        self.assertIn("A", top_ids)
        self.assertIn("B", top_ids)

    def test_disjoint_results(self):
        bm25 = [_make_bm25_result("A"), _make_bm25_result("B")]
        vec = [_make_vec_result("C"), _make_vec_result("D")]
        combined = rrf_combine(bm25, vec, k=60)

        all_ids = {r["file_id"] for r in combined}
        self.assertEqual(all_ids, {"A", "B", "C", "D"})

    def test_empty_bm25(self):
        combined = rrf_combine([], [_make_vec_result("A")])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["file_id"], "A")

    def test_empty_vector(self):
        combined = rrf_combine([_make_bm25_result("A")], [])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["file_id"], "A")

    def test_both_empty(self):
        self.assertEqual(rrf_combine([], []), [])

    def test_rrf_score_present(self):
        combined = rrf_combine(
            [_make_bm25_result("A")], [_make_vec_result("A")]
        )
        self.assertIn("rrf_score", combined[0])
        self.assertGreater(combined[0]["rrf_score"], 0)

    def test_rank_fields(self):
        combined = rrf_combine(
            [_make_bm25_result("A")], [_make_vec_result("A")]
        )
        self.assertEqual(combined[0]["bm25_rank"], 1)
        self.assertEqual(combined[0]["vector_rank"], 1)

    def test_k_parameter_affects_scores(self):
        bm25 = [_make_bm25_result("A")]
        vec = [_make_vec_result("A")]
        low_k = rrf_combine(bm25, vec, k=1)
        high_k = rrf_combine(bm25, vec, k=100)
        # Lower k gives higher scores
        self.assertGreater(low_k[0]["rrf_score"], high_k[0]["rrf_score"])


class TestHybridQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "test.db"
        conn = get_connection(self.db_path)
        ensure_schema(conn)

        # Insert test data
        for fid, title, full_text, vec in [
            ("docker-001", "Docker 설정", "Docker 컨테이너 이미지 빌드", [1.0, 0.0, 0.0]),
            ("python-001", "Python 설정", "Python venv 가상환경 설치", [0.0, 1.0, 0.0]),
            ("react-001", "React 패턴", "React hooks 컴포넌트 렌더링", [0.0, 0.0, 1.0]),
        ]:
            upsert_qa_entry(conn, fid, {
                "title": title,
                "work_summary": title,
                "full_text": full_text,
                "project": "test",
                "importance": 3,
            })
            chunk_ids = upsert_chunks(conn, fid, [
                {"chunk_index": 0, "chunk_text": full_text, "text_hash": f"h_{fid}"}
            ])
            store_chunk_embeddings(conn, [(chunk_ids[0], vec)])

        conn.commit()
        conn.close()

    def test_hybrid_mode(self):
        results = hybrid_query(
            self.db_path, "Docker", [0.9, 0.1, 0.0], top_k=3
        )
        self.assertGreater(len(results), 0)
        # Docker should rank first (matches both BM25 and vector)
        self.assertEqual(results[0]["file_id"], "docker-001")

    def test_bm25_only_mode(self):
        results = hybrid_query(
            self.db_path, "Docker", [0.0, 0.0, 0.0], top_k=3, mode="bm25"
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["file_id"], "docker-001")

    def test_vector_only_mode(self):
        results = hybrid_query(
            self.db_path, "", [1.0, 0.0, 0.0], top_k=3, mode="vector"
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["file_id"], "docker-001")

    def test_top_k_respected(self):
        results = hybrid_query(
            self.db_path, "설정", [0.5, 0.5, 0.0], top_k=1
        )
        self.assertLessEqual(len(results), 1)

    def test_no_bm25_results_falls_back_to_vector(self):
        results = hybrid_query(
            self.db_path, "xyznonexistent", [1.0, 0.0, 0.0], top_k=3
        )
        # BM25 returns nothing, but vector search still finds results
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["file_id"], "docker-001")

    def test_filters_applied(self):
        # Add a high-importance entry
        conn = get_connection(self.db_path)
        upsert_qa_entry(conn, "important-001", {
            "title": "중요 Docker 설정",
            "full_text": "Docker 중요 설정",
            "importance": 5,
            "project": "test",
        })
        chunk_ids = upsert_chunks(conn, "important-001", [
            {"chunk_index": 0, "chunk_text": "Docker 중요", "text_hash": "hi"}
        ])
        store_chunk_embeddings(conn, [(chunk_ids[0], [0.8, 0.0, 0.0])])
        conn.commit()
        conn.close()

        results = hybrid_query(
            self.db_path, "Docker", [0.9, 0.0, 0.0],
            top_k=5, filters={"importance_min": 5}
        )
        file_ids = [r["file_id"] for r in results]
        self.assertIn("important-001", file_ids)
        self.assertNotIn("docker-001", file_ids)


if __name__ == "__main__":
    unittest.main()
