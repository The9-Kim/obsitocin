"""Hybrid search: BM25 + vector with Reciprocal Rank Fusion."""

from __future__ import annotations

from pathlib import Path

from obsitocin.search_db import (
    bm25_search,
    get_connection,
    ensure_schema,
    vector_search,
)


def rrf_combine(
    bm25_results: list[dict],
    vector_results: list[dict],
    *,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion.

    score = 1/(k + bm25_rank) + 1/(k + vector_rank)

    Returns results sorted by rrf_score descending, with merged metadata.
    """
    # Build rank maps (1-indexed)
    bm25_map: dict[str, tuple[int, dict]] = {}
    for rank, r in enumerate(bm25_results, 1):
        bm25_map[r["file_id"]] = (rank, r)

    vector_map: dict[str, tuple[int, dict]] = {}
    for rank, r in enumerate(vector_results, 1):
        vector_map[r["file_id"]] = (rank, r)

    all_ids = set(bm25_map.keys()) | set(vector_map.keys())

    scored: list[dict] = []
    for fid in all_ids:
        bm25_rank = bm25_map[fid][0] if fid in bm25_map else len(bm25_results) + 1
        vec_rank = vector_map[fid][0] if fid in vector_map else len(vector_results) + 1

        rrf_score = 1.0 / (k + bm25_rank) + 1.0 / (k + vec_rank)

        # Merge metadata: prefer vector result (has similarity), fallback to bm25
        if fid in vector_map:
            meta = dict(vector_map[fid][1])
        else:
            meta = dict(bm25_map[fid][1])

        meta["rrf_score"] = round(rrf_score, 6)
        meta["bm25_rank"] = bm25_rank
        meta["vector_rank"] = vec_rank
        scored.append(meta)

    scored.sort(key=lambda x: x["rrf_score"], reverse=True)
    return scored


def hybrid_query(
    db_path: Path,
    query_text: str,
    query_embedding: list[float],
    *,
    top_k: int = 5,
    filters: dict | None = None,
    mode: str = "hybrid",
    rrf_k: int = 60,
) -> list[dict]:
    """Execute hybrid search.

    Args:
        db_path: Path to search.db
        query_text: Natural language query for BM25
        query_embedding: Dense vector for vector search
        top_k: Number of results to return
        filters: Optional filters (importance_min, category, project, etc.)
        mode: "hybrid" | "bm25" | "vector"
        rrf_k: RRF constant (default 60)

    Returns list of result dicts sorted by relevance.
    """
    conn = get_connection(db_path)
    ensure_schema(conn)
    expand_k = top_k * 4

    try:
        if mode == "bm25":
            results = bm25_search(conn, query_text, top_k=top_k, filters=filters)
            for r in results:
                r["rrf_score"] = abs(r.get("bm25_score", 0))
            return results[:top_k]

        if mode == "vector":
            results = vector_search(
                conn, query_embedding, top_k=top_k, filters=filters
            )
            for r in results:
                r["rrf_score"] = r.get("similarity", 0)
            return results[:top_k]

        # Hybrid mode
        bm25_results = bm25_search(
            conn, query_text, top_k=expand_k, filters=filters
        )
        vector_results = vector_search(
            conn, query_embedding, top_k=expand_k, filters=filters
        )

        if not bm25_results and not vector_results:
            return []

        if not bm25_results:
            for r in vector_results:
                r["rrf_score"] = r.get("similarity", 0)
            return vector_results[:top_k]

        if not vector_results:
            for r in bm25_results:
                r["rrf_score"] = abs(r.get("bm25_score", 0))
            return bm25_results[:top_k]

        combined = rrf_combine(bm25_results, vector_results, k=rrf_k)
        return combined[:top_k]
    finally:
        conn.close()
