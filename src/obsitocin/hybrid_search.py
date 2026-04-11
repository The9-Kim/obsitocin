"""Hybrid search: BM25 + vector with Reciprocal Rank Fusion."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from obsitocin.search_db import (
    _cosine_similarity,
    bm25_search,
    get_connection,
    ensure_schema,
    unpack_embedding,
    vector_search,
)


# ── 4-Layer Dedup ──


def deduplicate_results(
    results: list[dict],
    *,
    cosine_threshold: float = 0.85,
    type_diversity_cap: float = 0.6,
    max_per_file: int = 1,
) -> list[dict]:
    """Post-process search results with 4-layer deduplication.

    1. Best chunk per file_id — keep only the highest-scoring result per file.
    2. Cosine similarity threshold — drop results too similar to a kept one.
    3. Type diversity cap — no single source_type may exceed 60% of results.
    4. Per-file limit — enforce max_per_file (default 1).
    """
    if not results:
        return results

    # Layer 1: Best per file_id
    best_per_file: dict[str, dict] = {}
    for r in results:
        fid = r.get("file_id", "")
        score = r.get("rrf_score", 0) or r.get("similarity", 0)
        prev = best_per_file.get(fid)
        if prev is None or score > (prev.get("rrf_score", 0) or prev.get("similarity", 0)):
            best_per_file[fid] = r
    deduped = sorted(
        best_per_file.values(),
        key=lambda x: x.get("rrf_score", 0) or x.get("similarity", 0),
        reverse=True,
    )

    # Layer 2: Cosine similarity dedup (skip if no embeddings available)
    kept_embeddings: list[tuple[dict, list[float]]] = []
    cosine_filtered: list[dict] = []
    for r in deduped:
        emb = r.get("_embedding")
        if emb and kept_embeddings:
            too_similar = any(
                _cosine_similarity(emb, kept_emb) > cosine_threshold
                for _, kept_emb in kept_embeddings
            )
            if too_similar:
                continue
        cosine_filtered.append(r)
        if emb:
            kept_embeddings.append((r, emb))
    deduped = cosine_filtered

    # Layer 3: Type diversity cap
    if deduped:
        type_counts: dict[str, int] = {}
        for r in deduped:
            st = r.get("source_type", "qa")
            type_counts[st] = type_counts.get(st, 0) + 1
        max_allowed = max(1, int(len(deduped) * type_diversity_cap))
        diversity_filtered: list[dict] = []
        running_counts: dict[str, int] = {}
        for r in deduped:
            st = r.get("source_type", "qa")
            cnt = running_counts.get(st, 0)
            if cnt < max_allowed:
                diversity_filtered.append(r)
                running_counts[st] = cnt + 1
        deduped = diversity_filtered

    return deduped


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
            results = bm25_search(conn, query_text, top_k=expand_k, filters=filters)
            for r in results:
                r["rrf_score"] = abs(r.get("bm25_score", 0))
            return deduplicate_results(results)[:top_k]

        if mode == "vector":
            results = vector_search(
                conn, query_embedding, top_k=expand_k, filters=filters
            )
            for r in results:
                r["rrf_score"] = r.get("similarity", 0)
            return deduplicate_results(results)[:top_k]

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
            return deduplicate_results(vector_results)[:top_k]

        if not vector_results:
            for r in bm25_results:
                r["rrf_score"] = abs(r.get("bm25_score", 0))
            return deduplicate_results(bm25_results)[:top_k]

        combined = rrf_combine(bm25_results, vector_results, k=rrf_k)
        return deduplicate_results(combined)[:top_k]
    finally:
        conn.close()


def hybrid_query_multi(
    db_path: Path,
    queries: Sequence[str],
    query_embeddings: Sequence[list[float]],
    *,
    top_k: int = 5,
    filters: dict | None = None,
    mode: str = "hybrid",
    rrf_k: int = 60,
) -> list[dict]:
    """Run hybrid search for multiple query variants and fuse results via RRF.

    Each query/embedding pair is searched independently, then all result lists
    are combined using Reciprocal Rank Fusion.
    """
    if len(queries) <= 1:
        emb = query_embeddings[0] if query_embeddings else []
        return hybrid_query(
            db_path, queries[0], emb,
            top_k=top_k, filters=filters, mode=mode, rrf_k=rrf_k,
        )

    conn = get_connection(db_path)
    ensure_schema(conn)
    expand_k = top_k * 4

    try:
        # Collect per-query results
        all_ranked: list[list[dict]] = []
        for i, q_text in enumerate(queries):
            q_emb = query_embeddings[i] if i < len(query_embeddings) else []

            if mode == "bm25":
                results = bm25_search(conn, q_text, top_k=expand_k, filters=filters)
            elif mode == "vector" and q_emb:
                results = vector_search(conn, q_emb, top_k=expand_k, filters=filters)
            elif mode == "hybrid":
                bm25_r = bm25_search(conn, q_text, top_k=expand_k, filters=filters)
                vec_r = vector_search(conn, q_emb, top_k=expand_k, filters=filters) if q_emb else []
                if bm25_r and vec_r:
                    results = rrf_combine(bm25_r, vec_r, k=rrf_k)
                elif vec_r:
                    results = vec_r
                else:
                    results = bm25_r
            else:
                results = bm25_search(conn, q_text, top_k=expand_k, filters=filters)

            all_ranked.append(results)

        # Cross-query RRF fusion
        file_scores: dict[str, tuple[float, dict]] = {}
        for ranked in all_ranked:
            for rank, r in enumerate(ranked, 1):
                fid = r["file_id"]
                score = 1.0 / (rrf_k + rank)
                prev_score, prev_meta = file_scores.get(fid, (0.0, {}))
                if not prev_meta:
                    prev_meta = dict(r)
                file_scores[fid] = (prev_score + score, prev_meta)

        combined = []
        for fid, (score, meta) in file_scores.items():
            meta["rrf_score"] = round(score, 6)
            combined.append(meta)

        combined.sort(key=lambda x: x["rrf_score"], reverse=True)
        return deduplicate_results(combined)[:top_k]
    finally:
        conn.close()
