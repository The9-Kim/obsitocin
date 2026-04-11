#!/usr/bin/env python3
"""Memory query engine: semantic search over accumulated knowledge.

Provides CLI-friendly search and AI-agent-friendly context retrieval.
"""

import json
from datetime import datetime
from pathlib import Path

from obsitocin.config import (
    PROCESSED_DIR,
    PROFILE_PATH,
    SEARCH_DB_PATH,
)
from obsitocin.concepts import build_concept_catalog, concept_lookup_key
from obsitocin.embeddings import (
    build_embeddings_for_qas,
    cosine_similarity,
    get_embedding,
    is_configured,
    load_index,
    start_embed_server,
    stop_embed_server,
)


def _load_all_written_qas() -> list[tuple[str, dict]]:
    """Load all written Q&A pairs from processed directory."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except (json.JSONDecodeError, Exception):
            continue
        if qa.get("status") in ("written", "processed"):
            file_id = filepath.stem
            results.append((file_id, qa))
    return results


def _apply_filters(qa: dict, filters: dict | None) -> bool:
    """Check if a Q&A pair passes the given filters."""
    if not filters:
        return True

    tagging = qa.get("tagging_result", {})

    if "memory_type" in filters:
        if tagging.get("memory_type", "dynamic") != filters["memory_type"]:
            return False

    if "category" in filters:
        if tagging.get("category", "other") != filters["category"]:
            return False

    if "importance_min" in filters:
        if tagging.get("importance", 3) < filters["importance_min"]:
            return False

    if "tags" in filters:
        qa_tags = set(tagging.get("tags", []))
        filter_tags = (
            set(filters["tags"])
            if isinstance(filters["tags"], list)
            else {filters["tags"]}
        )
        if not filter_tags & qa_tags:
            return False

    if "date_from" in filters:
        ts = qa.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.strftime("%Y-%m-%d") < filters["date_from"]:
                return False
        except (ValueError, TypeError):
            return False

    if "date_to" in filters:
        ts = qa.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.strftime("%Y-%m-%d") > filters["date_to"]:
                return False
        except (ValueError, TypeError):
            return False

    return True


def _ensure_index(qa_files: list[tuple[str, dict]]) -> dict:
    index = load_index()
    entries = index.get("entries", {})
    qa_ids = {file_id for file_id, _qa in qa_files}
    missing_ids = sorted(file_id for file_id in qa_ids if file_id not in entries)
    if missing_ids:
        build_embeddings_for_qas(
            [(file_id, qa) for file_id, qa in qa_files if file_id in missing_ids]
        )
        index = load_index()
    return index


def _db_has_entries() -> bool:
    """Check if search.db exists and has data."""
    if not SEARCH_DB_PATH.exists():
        return False
    try:
        from obsitocin.search_db import get_connection, get_db_stats
        conn = get_connection(SEARCH_DB_PATH)
        stats = get_db_stats(conn)
        conn.close()
        return stats.get("entries", 0) > 0
    except Exception:
        return False


def _query_via_db(
    query_text: str,
    top_k: int,
    filters: dict | None,
    mode: str,
    expand: bool = False,
) -> list[dict]:
    """Query using SQLite hybrid search."""
    from obsitocin.hybrid_search import hybrid_query, hybrid_query_multi

    # Query expansion via local Qwen
    queries = [query_text]
    if expand:
        try:
            from obsitocin.query_expansion import expand_query
            queries = expand_query(query_text)
        except Exception:
            pass

    # Generate query embeddings if needed
    query_embeddings: list[list[float]] = []
    if mode in ("hybrid", "vector"):
        if not is_configured():
            if mode == "vector":
                raise RuntimeError(
                    "Embedding model not configured for vector search."
                )
            mode = "bm25"  # fall back to BM25-only
        else:
            try:
                start_embed_server()
                for q in queries:
                    query_embeddings.append(get_embedding(q))
            except Exception:
                if mode == "vector":
                    raise
                mode = "bm25"
            finally:
                if mode == "bm25":
                    stop_embed_server()

    try:
        if len(queries) > 1:
            results = hybrid_query_multi(
                SEARCH_DB_PATH,
                queries,
                query_embeddings,
                top_k=top_k,
                filters=filters,
                mode=mode,
            )
        else:
            emb = query_embeddings[0] if query_embeddings else []
            results = hybrid_query(
                SEARCH_DB_PATH,
                query_text,
                emb,
                top_k=top_k,
                filters=filters,
                mode=mode,
            )
    finally:
        if query_embeddings:
            stop_embed_server()

    # Load staleness data
    stale_topics: set[tuple[str, str]] = set()
    try:
        from obsitocin.search_db import get_connection as _get_conn, ensure_schema as _ensure, get_stale_topics
        _conn = _get_conn(SEARCH_DB_PATH)
        _ensure(_conn)
        for st in get_stale_topics(_conn):
            stale_topics.add((st["project"], st["topic"]))
        _conn.close()
    except Exception:
        pass

    # Normalize to standard result schema
    normalized = []
    for r in results:
        ts = r.get("timestamp", "")
        try:
            dt_parsed = datetime.fromisoformat(ts)
            date_str = dt_parsed.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            date_str = "—"

        tags = r.get("tags", [])
        if isinstance(tags, str):
            import json as _json
            try:
                tags = _json.loads(tags)
            except (ValueError, TypeError):
                tags = []

        concepts = r.get("key_concepts", [])
        if isinstance(concepts, str):
            import json as _json
            try:
                concepts = _json.loads(concepts)
            except (ValueError, TypeError):
                concepts = []

        project = r.get("project", "uncategorized")
        title = r.get("title", "Untitled")
        is_stale = (project, title) in stale_topics

        normalized.append({
            "file_id": r.get("file_id", ""),
            "title": title,
            "work_summary": r.get("work_summary", ""),
            "distilled_knowledge": [],
            "similarity": round(r.get("similarity", r.get("rrf_score", 0)), 4),
            "importance": r.get("importance", 3),
            "topics": concepts,
            "category": r.get("category", "other"),
            "tags": tags,
            "date": date_str,
            "project": project,
            "source_type": r.get("source_type", "qa"),
            "stale": is_stale,
        })
    return normalized


def _query_via_json(
    query_text: str,
    top_k: int,
    filters: dict | None,
) -> list[dict]:
    """Original brute-force JSON-based query (fallback)."""
    if not is_configured():
        raise RuntimeError(
            "Embedding model not configured. Set OBS_EMBED_MODEL_PATH or place a GGUF embedding model under ~/.local/share/obsitocin/models/."
        )

    all_qas = _load_all_written_qas()

    try:
        start_embed_server()
        index = _ensure_index(all_qas) if all_qas else load_index()
        entries = index.get("entries", {})
        if not entries:
            return []

        qa_map = {file_id: qa for file_id, qa in all_qas}
        query_embedding = get_embedding(query_text)

        scored: list[tuple[str, float, dict]] = []
        for file_id, entry in entries.items():
            embedding = entry.get("embedding", [])
            if not embedding:
                continue

            if file_id.startswith("topic:"):
                parts = file_id.split(":", 2)
                topic_project = parts[1] if len(parts) > 1 else "unknown"
                topic_title = parts[2] if len(parts) > 2 else file_id
                if not _apply_filters(
                    {
                        "tagging_result": {
                            "memory_type": "static",
                            "category": "other",
                            "importance": 4,
                            "tags": [],
                        }
                    },
                    filters,
                ):
                    continue
                sim = cosine_similarity(query_embedding, embedding)
                scored.append(
                    (
                        file_id,
                        sim,
                        {
                            "_is_topic_note": True,
                            "_project": topic_project,
                            "_topic": topic_title,
                        },
                    )
                )
                continue

            qa = qa_map.get(file_id)
            if qa is None:
                continue

            if not _apply_filters(qa, filters):
                continue

            sim = cosine_similarity(query_embedding, embedding)
            scored.append((file_id, sim, qa))

        scored.sort(key=lambda x: x[1], reverse=True)
        MIN_SIMILARITY = 0.5

        results = []
        for file_id, sim, qa in scored[:top_k]:
            if sim < MIN_SIMILARITY:
                continue

            if qa.get("_is_topic_note"):
                results.append(
                    {
                        "file_id": file_id,
                        "title": qa["_topic"],
                        "work_summary": f"주제 노트: {qa['_topic']} (프로젝트: {qa['_project']})",
                        "distilled_knowledge": [],
                        "similarity": round(sim, 4),
                        "importance": 4,
                        "topics": [qa["_topic"]],
                        "category": "other",
                        "tags": [],
                        "date": "—",
                        "project": qa["_project"],
                        "source_type": "topic_note",
                    }
                )
                continue

            tagging = qa.get("tagging_result", {})
            ts = qa.get("timestamp", "")
            try:
                dt_parsed = datetime.fromisoformat(ts)
                date_str = dt_parsed.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                date_str = "unknown"

            results.append(
                {
                    "file_id": file_id,
                    "title": tagging.get("title", "Untitled"),
                    "work_summary": tagging.get("work_summary")
                    or tagging.get("summary", ""),
                    "distilled_knowledge": tagging.get("distilled_knowledge", []),
                    "similarity": round(sim, 4),
                    "importance": tagging.get("importance", 3),
                    "topics": tagging.get("key_concepts")
                    or [
                        t["name"]
                        for t in tagging.get("topics", [])
                        if isinstance(t, dict)
                    ]
                    or tagging.get("canonical_concepts", []),
                    "category": tagging.get("category", "other"),
                    "tags": tagging.get("tags", []),
                    "date": date_str,
                    "project": Path(qa.get("cwd", "")).name or "uncategorized",
                }
            )

        return results
    finally:
        stop_embed_server()


def query(
    query_text: str,
    top_k: int = 5,
    filters: dict | None = None,
    mode: str = "hybrid",
    expand: bool | None = None,
) -> list[dict]:
    """Search across all accumulated Q&A pairs.

    Args:
        query_text: Natural language query
        top_k: Number of results to return
        filters: Optional filters (memory_type, category, importance_min, tags, date_from, date_to)
        mode: Search mode — "hybrid" (BM25+vector), "bm25", or "vector"
        expand: Enable multi-query expansion via Qwen. None = use config default.

    Returns list of:
        {file_id, title, work_summary, similarity, importance, topics, category, tags, date, project}
    """
    if expand is None:
        from obsitocin.config import QUERY_EXPANSION
        expand = QUERY_EXPANSION
    if _db_has_entries():
        return _query_via_db(query_text, top_k, filters, mode, expand=expand)
    return _query_via_json(query_text, top_k, filters)


def query_concepts(
    query_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Find concepts most relevant to a query.

    Aggregates Q&A similarities per concept and returns top concepts.
    """
    results = query(query_text, top_k=top_k * 3)
    catalog = build_concept_catalog([qa for _file_id, qa in _load_all_written_qas()])
    canonical_lookup = catalog.get("alias_to_canonical", {})
    query_key = concept_lookup_key(query_text)
    exact_canonical = canonical_lookup.get(query_key)

    concept_scores: dict[str, list[float]] = {}
    for r in results:
        for topic in r.get("topics") or []:
            canonical = canonical_lookup.get(concept_lookup_key(topic), topic)
            concept_scores.setdefault(canonical, []).append(r["similarity"])

    if exact_canonical and exact_canonical not in concept_scores:
        concept_scores[exact_canonical] = []

    aggregated = []
    for concept, scores in concept_scores.items():
        avg_sim = sum(scores) / len(scores) if scores else 0.0
        aggregated.append(
            {
                "concept": concept,
                "avg_similarity": round(avg_sim, 4),
                "occurrences": len(scores),
                "exact_match": concept == exact_canonical,
            }
        )

    aggregated.sort(
        key=lambda x: (x["exact_match"], x["avg_similarity"], x["occurrences"]),
        reverse=True,
    )
    return aggregated[:top_k]


def get_context(
    query_text: str,
    top_k: int = 5,
) -> str:
    """Get combined user profile + relevant memories as a context string.

    Designed for AI agents to consume as additional context.
    """
    parts = []

    # User profile
    if PROFILE_PATH and PROFILE_PATH.exists():
        profile_content = PROFILE_PATH.read_text()
        # Strip frontmatter
        if profile_content.startswith("---"):
            end = profile_content.find("---", 3)
            if end != -1:
                profile_content = profile_content[end + 3 :].strip()
        parts.append(profile_content)
    else:
        parts.append("# User Profile\n\n_No profile available yet._")

    # Relevant memories
    try:
        results = query(query_text, top_k=top_k)
        if results:
            memory_lines = ["\n# Relevant Memories\n"]
            for i, r in enumerate(results, 1):
                memory_lines.append(
                    f"{i}. [{r['similarity']:.2f}] {r['title']} "
                    f"({r['date']}) — {r['memory_type']}, importance: {r['importance']}"
                )
                memory_lines.append(f"   {r['summary']}")
                if r["concepts"]:
                    memory_lines.append(f"   Concepts: {', '.join(r['concepts'])}")
                memory_lines.append("")
            parts.append("\n".join(memory_lines))
        else:
            parts.append("\n# Relevant Memories\n\n_No relevant memories found._")
    except Exception as e:
        parts.append(f"\n# Relevant Memories\n\n_Search failed: {e}_")

    return "\n".join(parts)


def format_results_table(results: list[dict]) -> str:
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        sim_bar = "█" * int(r["similarity"] * 10)
        lines.append(
            f"  {i}. [{r['similarity']:.2f}] {sim_bar} {r['title']} "
            f"({r['project']}, {r['date']})"
        )
        lines.append(f"     importance: {r['importance']}, category: {r['category']}")
        lines.append(f"     {r['work_summary']}")
        knowledge = r.get("distilled_knowledge", [])
        if knowledge:
            for k in knowledge[:3]:
                lines.append(f"       • {k}")
        if r["topics"]:
            lines.append(f"     Topics: {', '.join(r['topics'])}")
        lines.append("")

    return "\n".join(lines)


def format_concept_results_table(results: list[dict]) -> str:
    if not results:
        return "No concepts found."

    lines = []
    for i, result in enumerate(results, 1):
        lines.append(
            f"  {i}. [{result['avg_similarity']:.2f}] {result['concept']} "
            f"({result['occurrences']} matches)"
        )
    return "\n".join(lines)
