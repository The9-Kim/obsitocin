from __future__ import annotations

import re
from datetime import datetime
from importlib import import_module
from pathlib import Path


def _get_vault_dir() -> Path | None:
    from obsitocin.config import OBS_DIR

    return OBS_DIR


def _frontmatter_value(content: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def list_topics(project: str | None = None) -> list[dict]:
    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return []

    projects_dir = vault_dir / "projects"
    if not projects_dir.exists():
        return []

    results: list[dict] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        if project and project_dir.name != project:
            continue

        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue

        for topic_file in sorted(topics_dir.glob("*.md")):
            try:
                content = topic_file.read_text(errors="replace")
            except OSError:
                continue

            title = _frontmatter_value(content, "title") or topic_file.stem
            sessions = int(_frontmatter_value(content, "sessions") or 0)
            importance = int(_frontmatter_value(content, "importance") or 3)
            results.append(
                {
                    "project": project_dir.name,
                    "topic": title,
                    "sessions": sessions,
                    "importance": importance,
                    "path": str(topic_file.relative_to(vault_dir)),
                }
            )

    return results


def read_topic(project: str, topic: str) -> str:
    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return "Error: Vault not configured. Run 'obsitocin init --vault-dir <path>'."

    topics_dir = vault_dir / "projects" / project / "topics"
    if not topics_dir.exists():
        return f"Error: Project '{project}' not found in vault."

    for topic_file in sorted(topics_dir.glob("*.md")):
        try:
            content = topic_file.read_text(errors="replace")
        except OSError:
            continue

        title = _frontmatter_value(content, "title")
        if title and title.lower() == topic.lower():
            return content
        if topic_file.stem.lower() == topic.lower():
            return content

    return f"Error: Topic '{topic}' not found in project '{project}'."


def get_work_log(date: str | None = None) -> str:
    """Read the work log for a given date (YYYY-MM-DD). Defaults to today."""
    from datetime import datetime

    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return "Error: Vault not configured."

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    log_path = vault_dir / "daily" / f"{date}.md"
    if not log_path.exists():
        return f"No work log found for {date}."

    try:
        return log_path.read_text(errors="replace")
    except OSError as e:
        return f"Error reading work log: {e}"


def save_insight(
    project: str,
    topic: str,
    knowledge: list[str],
    work_summary: str = "",
) -> dict:
    """Save knowledge directly to a topic note. No LLM tagging — direct write.

    Returns {"project": str, "topic": str, "success": bool, "path": str | None}
    """
    from datetime import datetime

    from obsitocin.topic_writer import (
        update_moc,
        update_project_index,
        write_topic_note,
    )

    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return {
            "project": project,
            "topic": topic,
            "success": False,
            "path": None,
            "error": "Vault not configured.",
        }

    timestamp = datetime.now().isoformat()
    path = write_topic_note(
        project=project,
        topic=topic,
        new_knowledge=[k for k in knowledge if k],
        work_summary=work_summary or f"직접 저장: {topic}",
        timestamp=timestamp,
        tags=[],
        importance=3,
    )

    if path:
        update_project_index(project)
        update_moc()
        return {"project": project, "topic": topic, "success": True, "path": str(path)}

    return {"project": project, "topic": topic, "success": False, "path": None}


def ingest_source_mcp(
    source: str, project: str | None = None, title: str | None = None
) -> dict:
    ingest_source = import_module("obsitocin.ingest").ingest_source
    return ingest_source(source=source, project=project, title=title)


def ask_wiki(
    question: str,
    project: str | None = None,
    save_to_wiki: bool = False,
) -> dict:
    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return {
            "answer": "Error: Vault not configured.",
            "sources": [],
            "saved": False,
            "saved_path": None,
        }

    all_topics = list_topics(project=project)
    if not all_topics:
        return {
            "answer": "위키에 관련 정보가 없습니다.",
            "sources": [],
            "saved": False,
            "saved_path": None,
        }

    # Use BM25 search if search.db is available, else fallback to substring matching
    top_topics: list[tuple[int, dict]] = []
    try:
        from obsitocin.config import SEARCH_DB_PATH

        if SEARCH_DB_PATH.exists():
            from obsitocin.search_db import bm25_search, get_connection, ensure_schema

            conn = get_connection(SEARCH_DB_PATH)
            ensure_schema(conn)
            bm25_filters = {"source_type": "topic_note"}
            if project:
                bm25_filters["project"] = project
            fts_results = bm25_search(conn, question, top_k=5, filters=bm25_filters)
            conn.close()
            if fts_results:
                # Map BM25 results back to topic_info format
                for r in fts_results:
                    matching = [
                        t for t in all_topics
                        if t["topic"] == r.get("title", "") or t.get("project") == r.get("project", "")
                    ]
                    if matching:
                        top_topics.append((r.get("importance", 3), matching[0]))
                    else:
                        top_topics.append((r.get("importance", 3), {
                            "topic": r.get("title", ""),
                            "project": r.get("project", ""),
                            "importance": r.get("importance", 3),
                            "path": f"projects/{r.get('project', '')}/topics/{r.get('title', '')}",
                        }))
    except Exception:
        pass

    if not top_topics:
        # Fallback: naive substring matching
        question_lower = question.lower()
        scored_topics: list[tuple[int, dict]] = []
        for topic_info in all_topics:
            score = 0
            topic_lower = topic_info["topic"].lower()
            for word in question_lower.split():
                if len(word) > 1 and word in topic_lower:
                    score += 3
            score += topic_info.get("importance", 3)
            scored_topics.append((score, topic_info))

        scored_topics.sort(key=lambda item: item[0], reverse=True)
        top_topics = scored_topics[:5]

    context_pages: list[str] = []
    source_refs: list[str] = []
    for _score, topic_info in top_topics:
        content = read_topic(topic_info["project"], topic_info["topic"])
        if not content.startswith("Error:"):
            context_pages.append(f"## {topic_info['topic']}\n\n{content[:2000]}")
            source_refs.append(
                f"[[{topic_info.get('path', topic_info['topic'])}|{topic_info['topic']}]]"
            )

    if not context_pages:
        return {
            "answer": "관련 위키 페이지를 찾을 수 없습니다.",
            "sources": [],
            "saved": False,
            "saved_path": None,
        }

    from obsitocin.provider import run_provider_prompt

    wiki_context = "\n\n---\n\n".join(context_pages)
    answer_prompt = f"""다음 위키 페이지들을 참고하여 질문에 답변하세요.

위키 내용:
{wiki_context[:8000]}

질문: {question}

규칙:
- 위키에 있는 정보만 사용하세요.
- 위키에 없는 내용은 \"위키에 관련 정보가 없습니다\"라고 답하세요.
- 답변은 한국어로, 간결하고 구체적으로.
- 출처 페이지를 언급하세요."""

    try:
        answer = run_provider_prompt(answer_prompt, timeout=60)
        if not answer or not answer.strip():
            answer = "답변을 생성하지 못했습니다."
    except Exception as exc:
        answer = f"LLM 답변 생성 실패: {exc}"

    saved_path = None
    if save_to_wiki and not answer.startswith(("Error:", "LLM")):
        from obsitocin.topic_writer import (
            update_moc,
            update_project_index,
            write_topic_note,
        )

        target_project = project or (
            top_topics[0][1]["project"] if top_topics else "general"
        )
        title = re.sub(r'[\\/:*?"<>|]', "", question)[:60].strip() or "Q&A"
        path = write_topic_note(
            project=target_project,
            topic=title,
            new_knowledge=[answer[:500]],
            work_summary=f"위키 질의 답변: {title}",
            timestamp=datetime.now().isoformat(),
            tags=["wiki-qa"],
            importance=3,
            page_type="topic",
        )
        if path:
            saved_path = str(path)
            update_project_index(target_project)
            update_moc()

    return {
        "answer": answer,
        "sources": source_refs,
        "saved": saved_path is not None,
        "saved_path": saved_path,
    }


def get_project_context(project: str | None = None) -> str:
    """Get a context summary for a project: topics + recent work + key knowledge.

    Returns a formatted string of project knowledge under 3000 chars.
    Useful for injecting at session start to give Claude prior project knowledge.
    """
    from datetime import datetime, timedelta

    vault_dir = _get_vault_dir()
    if vault_dir is None:
        return "Error: Vault not configured."

    parts: list[str] = []

    topics = list_topics(project=project)
    if not topics:
        if project:
            return f"No topics found for project '{project}'."
        return "No topics found in vault."

    project_names = sorted({t["project"] for t in topics})
    project_label = project if project else ", ".join(project_names)
    parts.append(f"# Project Context: {project_label}\n")

    parts.append("## Topics\n")
    for t in topics:
        importance = t.get("importance", 3)
        line = (
            f"- **{t['topic']}** ({t['sessions']} sessions, importance: {importance})"
        )

        if importance >= 4:
            topic_content = read_topic(t["project"], t["topic"])
            if not topic_content.startswith("Error:"):
                knowledge_pattern = r"## 핵심 지식\s*\n((?:.*\n)*?)(?=\n## |\Z)"
                match = re.search(knowledge_pattern, topic_content)
                if match:
                    bullets = [
                        l.lstrip("- ").strip()
                        for l in match.group(1).strip().split("\n")
                        if l.strip().startswith("- ") and "아직 축적된" not in l
                    ]
                    if bullets:
                        line += f"\n  → {bullets[0]}"
        parts.append(line)

    parts.append("\n## Recent Work Log\n")
    today = datetime.now()
    found_logs = False
    for i in range(3):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        log_content = get_work_log(date)
        if not log_content.startswith("No work log") and not log_content.startswith(
            "Error:"
        ):
            entries = re.findall(r"^- .+$", log_content, re.MULTILINE)
            if entries:
                parts.append(f"**{date}:**")
                parts.extend(entries[:5])
                found_logs = True
    if not found_logs:
        parts.append("No recent work logs found.")

    result = "\n".join(parts)
    if len(result) > 3000:
        result = result[:2997] + "..."

    return result


def search_knowledge(query: str, top_k: int = 5) -> list[dict]:
    from obsitocin.embeddings import log
    from obsitocin.memory_query import query as memory_query

    try:
        return memory_query(query, top_k=top_k)
    except RuntimeError as exc:
        log(f"MCP search_knowledge unavailable: {exc}")
        return []
    except Exception as exc:
        log(f"MCP search_knowledge failed: {exc}")
        return []


def recall_multi(queries: list[dict], top_k: int = 5) -> list[dict]:
    """Execute multiple typed queries and merge results.

    Each query: {"type": "keyword"|"semantic"|"temporal", "text": "...",
                 "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD",
                 "filters": {...}}
    """
    from obsitocin.memory_query import query as memory_query

    all_results: list[dict] = []
    seen_ids: set[str] = set()

    for q in queries:
        qtype = q.get("type", "semantic")
        text = q.get("text", "")
        if not text:
            continue
        filters = dict(q.get("filters") or {})

        if qtype == "keyword":
            mode = "bm25"
        elif qtype == "temporal":
            mode = "bm25"
            if "date_from" in q:
                filters["date_from"] = q["date_from"]
            if "date_to" in q:
                filters["date_to"] = q["date_to"]
        else:
            mode = "hybrid"

        try:
            results = memory_query(text, top_k=top_k, filters=filters or None, mode=mode)
        except Exception:
            results = []

        for r in results:
            fid = r.get("file_id", "")
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                r["matched_by"] = qtype
                all_results.append(r)

    return all_results


def create_server():
    try:
        FastMCP = import_module("fastmcp").FastMCP
    except ImportError as exc:
        raise ImportError(
            "fastmcp is required to run the MCP server.\n"
            "Install it with: pip install 'obsitocin[mcp]'"
        ) from exc

    mcp = FastMCP("obsitocin")

    @mcp.tool(name="search_knowledge")
    def search_knowledge_tool(query: str, top_k: int = 5) -> list[dict]:
        """Search the knowledge vault semantically."""
        return search_knowledge(query, top_k=top_k)

    @mcp.tool(name="list_topics")
    def list_topics_tool(project: str | None = None) -> list[dict]:
        """List all topics in the vault."""
        return list_topics(project=project)

    @mcp.tool(name="read_topic")
    def read_topic_tool(project: str, topic: str) -> str:
        """Read a topic note's full content."""
        return read_topic(project=project, topic=topic)

    @mcp.tool(name="get_work_log")
    def get_work_log_tool(date: str | None = None) -> str:
        """Read the daily work log for a given date."""
        return get_work_log(date=date)

    @mcp.tool(name="save_insight")
    def save_insight_tool(
        project: str,
        topic: str,
        knowledge: list[str],
        work_summary: str = "",
    ) -> dict:
        """Save knowledge directly to a topic note without LLM tagging."""
        return save_insight(
            project=project,
            topic=topic,
            knowledge=knowledge,
            work_summary=work_summary,
        )

    @mcp.tool(name="ingest_source")
    def ingest_source_tool(
        source: str, project: str | None = None, title: str | None = None
    ) -> dict:
        """Ingest an external source (URL or file path) into the vault. Saves raw content and updates related topic notes."""
        return ingest_source_mcp(source=source, project=project, title=title)

    @mcp.tool(name="ask_wiki")
    def ask_wiki_tool(
        question: str,
        project: str | None = None,
        save_to_wiki: bool = False,
    ) -> dict:
        """Ask a question against the wiki and get an LLM-generated answer with source citations."""
        return ask_wiki(
            question=question,
            project=project,
            save_to_wiki=save_to_wiki,
        )

    @mcp.tool(name="get_project_context")
    def get_project_context_tool(project: str | None = None) -> str:
        """Get context summary for a project. Use at session start to recall prior knowledge."""
        return get_project_context(project=project)

    @mcp.tool(name="recall")
    def recall_tool(queries: list[dict], top_k: int = 5) -> list[dict]:
        """Multi-modal search combining keyword, semantic, and temporal queries in one call.

        Each query item: {"type": "keyword"|"semantic"|"temporal", "text": "...",
        "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD", "filters": {...}}
        """
        return recall_multi(queries, top_k=top_k)

    return mcp


if __name__ == "__main__":
    server = create_server()
    server.run()
