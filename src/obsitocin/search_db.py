"""SQLite + FTS5 search database for obsitocin.

Provides structured storage, BM25 keyword search, and vector search
as an upgrade from the monolithic embeddings.json file.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from datetime import datetime
from pathlib import Path

from obsitocin.config import SEARCH_DB_PATH

SCHEMA_VERSION = 2

# ── Schema ──

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS db_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_entries (
    file_id       TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    work_summary  TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'other',
    importance    INTEGER NOT NULL DEFAULT 3,
    memory_type   TEXT NOT NULL DEFAULT 'dynamic',
    tags          TEXT NOT NULL DEFAULT '[]',
    key_concepts  TEXT NOT NULL DEFAULT '[]',
    project       TEXT NOT NULL DEFAULT '',
    timestamp     TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL DEFAULT '',
    source_type   TEXT NOT NULL DEFAULT 'qa',
    full_text     TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_qa_project ON qa_entries(project);
CREATE INDEX IF NOT EXISTS idx_qa_importance ON qa_entries(importance);
CREATE INDEX IF NOT EXISTS idx_qa_source_type ON qa_entries(source_type);
CREATE INDEX IF NOT EXISTS idx_qa_timestamp ON qa_entries(timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(
    title,
    work_summary,
    tags,
    key_concepts,
    full_text,
    content=qa_entries,
    content_rowid=rowid,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS qa_fts_ai AFTER INSERT ON qa_entries
BEGIN
    INSERT INTO qa_fts(rowid, title, work_summary, tags, key_concepts, full_text)
    VALUES (new.rowid, new.title, new.work_summary, new.tags, new.key_concepts, new.full_text);
END;

CREATE TRIGGER IF NOT EXISTS qa_fts_ad AFTER DELETE ON qa_entries
BEGIN
    INSERT INTO qa_fts(qa_fts, rowid, title, work_summary, tags, key_concepts, full_text)
    VALUES ('delete', old.rowid, old.title, old.work_summary, old.tags, old.key_concepts, old.full_text);
END;

CREATE TRIGGER IF NOT EXISTS qa_fts_au AFTER UPDATE ON qa_entries
BEGIN
    INSERT INTO qa_fts(qa_fts, rowid, title, work_summary, tags, key_concepts, full_text)
    VALUES ('delete', old.rowid, old.title, old.work_summary, old.tags, old.key_concepts, old.full_text);
    INSERT INTO qa_fts(rowid, title, work_summary, tags, key_concepts, full_text)
    VALUES (new.rowid, new.title, new.work_summary, new.tags, new.key_concepts, new.full_text);
END;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT NOT NULL REFERENCES qa_entries(file_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text  TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(file_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks(file_id);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    INTEGER PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_updates (
    project       TEXT NOT NULL,
    topic         TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    session_id    TEXT NOT NULL DEFAULT '',
    work_summary  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project, topic)
);

CREATE TABLE IF NOT EXISTS topic_links (
    source_project TEXT NOT NULL,
    source_topic   TEXT NOT NULL,
    target_project TEXT NOT NULL,
    target_topic   TEXT NOT NULL,
    link_type      TEXT NOT NULL DEFAULT 'related',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source_project, source_topic, target_project, target_topic)
);
CREATE INDEX IF NOT EXISTS idx_topic_links_target
    ON topic_links(target_project, target_topic);
"""


# ── Connection ──


def get_connection(
    db_path: Path | None = None, readonly: bool = False
) -> sqlite3.Connection:
    """Open a SQLite connection.

    Use read-only mode for diagnostics/status commands that should not require
    write access to WAL/SHM sidecar files.
    """
    path = str(db_path or SEARCH_DB_PATH)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes/triggers if not present. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM db_meta WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


# ── Vector packing ──


def pack_embedding(vector: list[float]) -> bytes:
    """Pack float list to struct-packed float32 BLOB."""
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> list[float]:
    """Unpack BLOB to float list."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ── CRUD: qa_entries ──


def upsert_qa_entry(conn: sqlite3.Connection, file_id: str, metadata: dict) -> int:
    """Insert or update a qa_entry. Returns rowid."""
    tags_json = json.dumps(metadata.get("tags", []), ensure_ascii=False)
    concepts_json = json.dumps(metadata.get("key_concepts", []), ensure_ascii=False)
    now = datetime.now().isoformat()

    conn.execute(
        """INSERT INTO qa_entries(
            file_id, title, work_summary, category, importance, memory_type,
            tags, key_concepts, project, timestamp, content_hash, source_type,
            full_text, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET
            title=excluded.title, work_summary=excluded.work_summary,
            category=excluded.category, importance=excluded.importance,
            memory_type=excluded.memory_type, tags=excluded.tags,
            key_concepts=excluded.key_concepts, project=excluded.project,
            timestamp=excluded.timestamp, content_hash=excluded.content_hash,
            source_type=excluded.source_type, full_text=excluded.full_text,
            updated_at=excluded.updated_at
        """,
        (
            file_id,
            metadata.get("title", ""),
            metadata.get("work_summary", ""),
            metadata.get("category", "other"),
            metadata.get("importance", 3),
            metadata.get("memory_type", "dynamic"),
            tags_json,
            concepts_json,
            metadata.get("project", ""),
            metadata.get("timestamp", ""),
            metadata.get("content_hash", ""),
            metadata.get("source_type", "qa"),
            metadata.get("full_text", ""),
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT rowid FROM qa_entries WHERE file_id=?", (file_id,)
    ).fetchone()
    return row[0] if row else 0


def delete_qa_entry(conn: sqlite3.Connection, file_id: str) -> bool:
    """Delete entry + cascading chunks/embeddings. Returns True if deleted."""
    cursor = conn.execute("DELETE FROM qa_entries WHERE file_id=?", (file_id,))
    return cursor.rowcount > 0


def get_qa_entry(conn: sqlite3.Connection, file_id: str) -> dict | None:
    """Fetch a single qa_entry by file_id."""
    row = conn.execute(
        "SELECT * FROM qa_entries WHERE file_id=?", (file_id,)
    ).fetchone()
    if not row:
        return None
    return dict(row)


# ── CRUD: chunks + embeddings ──


def upsert_chunks(
    conn: sqlite3.Connection,
    file_id: str,
    chunks: list[dict],
) -> list[int]:
    """Replace all chunks for file_id. Returns list of chunk_ids.

    Each chunk dict: {"chunk_index": int, "chunk_text": str, "text_hash": str}
    """
    conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
    chunk_ids = []
    for chunk in chunks:
        cursor = conn.execute(
            "INSERT INTO chunks(file_id, chunk_index, chunk_text, text_hash) VALUES (?, ?, ?, ?)",
            (file_id, chunk["chunk_index"], chunk["chunk_text"], chunk["text_hash"]),
        )
        chunk_ids.append(cursor.lastrowid)
    return chunk_ids


def store_chunk_embeddings(
    conn: sqlite3.Connection,
    chunk_embeddings: list[tuple[int, list[float]]],
) -> int:
    """Store embeddings for chunks. Returns count stored."""
    count = 0
    for chunk_id, vector in chunk_embeddings:
        blob = pack_embedding(vector)
        conn.execute(
            "INSERT OR REPLACE INTO embeddings(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, blob),
        )
        count += 1
    return count


# ── Search: BM25 ──


def _build_filter_clause(filters: dict | None) -> tuple[str, list]:
    """Build SQL WHERE clause from filters dict."""
    if not filters:
        return "", []
    clauses = []
    params = []
    if "importance_min" in filters:
        clauses.append("e.importance >= ?")
        params.append(filters["importance_min"])
    if "category" in filters:
        clauses.append("e.category = ?")
        params.append(filters["category"])
    if "project" in filters:
        clauses.append("e.project = ?")
        params.append(filters["project"])
    if "source_type" in filters:
        clauses.append("e.source_type = ?")
        params.append(filters["source_type"])
    if "date_from" in filters:
        clauses.append("e.timestamp >= ?")
        params.append(filters["date_from"])
    if "date_to" in filters:
        clauses.append("e.timestamp <= ?")
        params.append(filters["date_to"])
    if "memory_type" in filters:
        clauses.append("e.memory_type = ?")
        params.append(filters["memory_type"])
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def _tokenize_query(query_text: str) -> str:
    """Pre-tokenize query using configured tokenizer for FTS5 MATCH."""
    try:
        from obsitocin.tokenizer import get_tokenizer

        tokenizer = get_tokenizer()
        tokens = tokenizer.tokenize(query_text)
        if tokens:
            return " ".join(tokens)
    except Exception:
        pass
    return query_text


def bm25_search(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    top_k: int = 20,
    filters: dict | None = None,
) -> list[dict]:
    """FTS5 BM25 keyword search. Returns [{file_id, bm25_score, ...metadata}]."""
    tokenized = _tokenize_query(query_text)
    if not tokenized.strip():
        return []

    filter_clause, filter_params = _build_filter_clause(filters)

    # FTS5 MATCH with bm25() ranking
    sql = f"""
        SELECT e.file_id, e.title, e.work_summary, e.category,
               e.importance, e.memory_type, e.tags, e.key_concepts,
               e.project, e.timestamp, e.source_type,
               bm25(qa_fts) AS bm25_score
        FROM qa_fts
        JOIN qa_entries e ON qa_fts.rowid = e.rowid
        WHERE qa_fts MATCH ?
        {filter_clause}
        ORDER BY bm25_score
        LIMIT ?
    """
    params = [tokenized] + filter_params + [top_k]

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []

    results = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["key_concepts"] = json.loads(d.get("key_concepts", "[]"))
        results.append(d)
    return results


# ── Search: Vector ──


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def vector_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    *,
    top_k: int = 20,
    filters: dict | None = None,
) -> list[dict]:
    """Brute-force cosine similarity over stored embeddings.

    Aggregates to parent file_id using max chunk similarity.
    Returns [{file_id, similarity, ...metadata}].
    """
    filter_clause, filter_params = _build_filter_clause(filters)

    sql = f"""
        SELECT c.file_id, c.chunk_id, emb.embedding,
               e.title, e.work_summary, e.category, e.importance,
               e.memory_type, e.tags, e.key_concepts, e.project,
               e.timestamp, e.source_type
        FROM embeddings emb
        JOIN chunks c ON emb.chunk_id = c.chunk_id
        JOIN qa_entries e ON c.file_id = e.file_id
        WHERE 1=1 {filter_clause}
    """
    rows = conn.execute(sql, filter_params).fetchall()

    # Compute similarities, aggregate by file_id (max chunk sim)
    file_best: dict[str, tuple[float, dict]] = {}
    for row in rows:
        d = dict(row)
        embedding = unpack_embedding(d.pop("embedding"))
        sim = _cosine_similarity(query_embedding, embedding)

        fid = d["file_id"]
        if fid not in file_best or sim > file_best[fid][0]:
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["key_concepts"] = json.loads(d.get("key_concepts", "[]"))
            d["similarity"] = sim
            file_best[fid] = (sim, d)

    results = [info for _sim, info in file_best.values()]
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


# ── Migration ──


def migrate_from_json(
    index_path: Path,
    processed_dir: Path,
    db_path: Path,
    vault_dir: Path | None = None,
) -> dict:
    """Migrate embeddings.json + processed/*.json into search.db.

    Returns {"entries_migrated": int, "chunks_created": int, "errors": list[str]}
    """
    import hashlib

    errors: list[str] = []

    if not index_path.exists():
        return {"entries_migrated": 0, "chunks_created": 0, "errors": ["embeddings.json not found"]}

    try:
        index = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"entries_migrated": 0, "chunks_created": 0, "errors": [str(e)]}

    entries = index.get("entries", {})
    if not entries:
        return {"entries_migrated": 0, "chunks_created": 0, "errors": ["No entries in index"]}

    # Load processed QA metadata
    qa_map: dict[str, dict] = {}
    if processed_dir.exists():
        for f in processed_dir.glob("*.json"):
            try:
                qa = json.loads(f.read_text())
                qa_map[f.stem] = qa
            except (json.JSONDecodeError, OSError):
                continue

    conn = get_connection(db_path)
    ensure_schema(conn)

    migrated = 0
    chunks_created = 0

    for file_id, entry in entries.items():
        embedding = entry.get("embedding", [])
        if not embedding:
            continue

        try:
            if file_id.startswith("topic:"):
                parts = file_id.split(":", 2)
                project = parts[1] if len(parts) > 1 else ""
                title = parts[2] if len(parts) > 2 else file_id
                metadata = {
                    "title": title,
                    "work_summary": f"주제 노트: {title}",
                    "category": "other",
                    "importance": 4,
                    "memory_type": "static",
                    "tags": [],
                    "key_concepts": [title],
                    "project": project,
                    "timestamp": entry.get("created_at", ""),
                    "content_hash": entry.get("text_hash", ""),
                    "source_type": "topic_note",
                    "full_text": title,
                }
            else:
                qa = qa_map.get(file_id, {})
                tagging = qa.get("tagging_result", {})
                metadata = {
                    "title": tagging.get("title", ""),
                    "work_summary": tagging.get("work_summary") or tagging.get("summary", ""),
                    "category": tagging.get("category", "other"),
                    "importance": tagging.get("importance", 3),
                    "memory_type": tagging.get("memory_type", "dynamic"),
                    "tags": tagging.get("tags", []),
                    "key_concepts": tagging.get("key_concepts", []),
                    "project": Path(qa.get("cwd", "")).name if qa.get("cwd") else "",
                    "timestamp": qa.get("timestamp", ""),
                    "content_hash": qa.get("content_hash", entry.get("text_hash", "")),
                    "source_type": qa.get("source_type", "qa"),
                    "full_text": "",
                }

            upsert_qa_entry(conn, file_id, metadata)

            # Create single chunk with the embed text hash
            t_hash = entry.get("text_hash", "")
            chunk_ids = upsert_chunks(conn, file_id, [
                {"chunk_index": 0, "chunk_text": "", "text_hash": t_hash}
            ])
            if chunk_ids:
                store_chunk_embeddings(conn, [(chunk_ids[0], embedding)])
                chunks_created += 1

            migrated += 1
        except Exception as e:
            errors.append(f"{file_id}: {e}")

    conn.commit()
    conn.close()
    return {"entries_migrated": migrated, "chunks_created": chunks_created, "errors": errors}


# ── Topic updates (staleness tracking) ──


def upsert_topic_update(
    conn: sqlite3.Connection,
    project: str,
    topic: str,
    session_id: str = "",
    work_summary: str = "",
) -> None:
    """Record that a topic note was updated."""
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO topic_updates(project, topic, updated_at, session_id, work_summary)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(project, topic) DO UPDATE SET
            updated_at=excluded.updated_at,
            session_id=excluded.session_id,
            work_summary=excluded.work_summary
        """,
        (project, topic, now, session_id, work_summary),
    )


def get_topic_update(
    conn: sqlite3.Connection, project: str, topic: str
) -> dict | None:
    """Get the last update info for a topic."""
    row = conn.execute(
        "SELECT * FROM topic_updates WHERE project=? AND topic=?",
        (project, topic),
    ).fetchone()
    return dict(row) if row else None


def get_stale_topics(
    conn: sqlite3.Connection, stale_days: int = 7
) -> list[dict]:
    """Find topics with recent Q&A activity but stale notes.

    A topic is stale if it was last updated more than stale_days ago
    AND there are newer qa_entries referencing it.
    """
    sql = """
        SELECT tu.project, tu.topic, tu.updated_at,
               MAX(e.timestamp) AS latest_qa_timestamp,
               COUNT(e.file_id) AS pending_qa_count
        FROM topic_updates tu
        JOIN qa_entries e ON e.project = tu.project
            AND (e.key_concepts LIKE '%' || tu.topic || '%'
                 OR e.title LIKE '%' || tu.topic || '%')
        WHERE e.timestamp > tu.updated_at
        GROUP BY tu.project, tu.topic
        HAVING pending_qa_count > 0
        ORDER BY pending_qa_count DESC
    """
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# ── Topic links ──


VALID_LINK_TYPES = ("related", "uses", "extends", "conflicts_with", "part_of")


def upsert_topic_link(
    conn: sqlite3.Connection,
    source_project: str,
    source_topic: str,
    target_project: str,
    target_topic: str,
    link_type: str = "related",
) -> None:
    """Create or update a typed link between two topics."""
    if link_type not in VALID_LINK_TYPES:
        link_type = "related"
    conn.execute(
        """INSERT INTO topic_links(
            source_project, source_topic, target_project, target_topic, link_type
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_project, source_topic, target_project, target_topic)
        DO UPDATE SET link_type=excluded.link_type
        """,
        (source_project, source_topic, target_project, target_topic, link_type),
    )


def get_topic_links(
    conn: sqlite3.Connection, project: str, topic: str
) -> list[dict]:
    """Get all outgoing links from a topic."""
    rows = conn.execute(
        """SELECT target_project, target_topic, link_type, created_at
        FROM topic_links
        WHERE source_project=? AND source_topic=?
        ORDER BY link_type, target_topic""",
        (project, topic),
    ).fetchall()
    return [dict(r) for r in rows]


def get_topic_backlinks(
    conn: sqlite3.Connection, project: str, topic: str
) -> list[dict]:
    """Get all incoming links to a topic."""
    rows = conn.execute(
        """SELECT source_project, source_topic, link_type, created_at
        FROM topic_links
        WHERE target_project=? AND target_topic=?
        ORDER BY link_type, source_topic""",
        (project, topic),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_topic_link(
    conn: sqlite3.Connection,
    source_project: str,
    source_topic: str,
    target_project: str,
    target_topic: str,
) -> bool:
    """Delete a link between two topics."""
    cursor = conn.execute(
        """DELETE FROM topic_links
        WHERE source_project=? AND source_topic=?
          AND target_project=? AND target_topic=?""",
        (source_project, source_topic, target_project, target_topic),
    )
    return cursor.rowcount > 0


# ── Stats ──


def get_db_stats(conn: sqlite3.Connection) -> dict:
    """Return database statistics."""
    stats = {}
    try:
        stats["entries"] = conn.execute("SELECT COUNT(*) FROM qa_entries").fetchone()[0]
        stats["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        stats["embeddings"] = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        stats = {"entries": 0, "chunks": 0, "embeddings": 0}
    return stats
