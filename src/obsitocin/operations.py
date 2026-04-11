"""Operation registry — single source of truth for CLI and MCP commands.

Each operation defines its name, description, parameter spec, and handler.
CLI (cli.py) and MCP (mcp_server.py) both reference this registry.
Parity tests verify structural consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # "str", "int", "bool", "list[str]"
    required: bool = True
    default: Any = None
    help: str = ""


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    params: tuple[Param, ...] = ()
    cli_command: str | None = None  # None = not exposed via CLI
    mcp_tool: str | None = None  # None = not exposed via MCP
    tags: tuple[str, ...] = ()


# ── Operation definitions ──

OPERATIONS: dict[str, Operation] = {}


def _op(op: Operation) -> Operation:
    OPERATIONS[op.name] = op
    return op


# Search & Query
_op(Operation(
    name="search_knowledge",
    description="하이브리드 검색 (BM25 + Vector + RRF)",
    params=(
        Param("query", "str", help="검색 쿼리"),
        Param("top_k", "int", required=False, default=5, help="결과 수"),
    ),
    cli_command="query",
    mcp_tool="search_knowledge",
    tags=("search",),
))

_op(Operation(
    name="concepts",
    description="개념 수준 검색",
    params=(
        Param("query_text", "str", help="검색 쿼리"),
        Param("top_k", "int", required=False, default=5, help="결과 수"),
    ),
    cli_command="concepts",
    mcp_tool=None,
    tags=("search",),
))

_op(Operation(
    name="recall",
    description="다중 쿼리 검색 (키워드 + 시맨틱 + 시간)",
    params=(
        Param("queries", "list[dict]", help="검색 쿼리 목록"),
        Param("top_k", "int", required=False, default=5, help="결과 수"),
    ),
    cli_command=None,
    mcp_tool="recall",
    tags=("search",),
))

_op(Operation(
    name="ask_wiki",
    description="위키 기반 Q&A (LLM 답변 + 출처)",
    params=(
        Param("question", "str", help="질문"),
        Param("project", "str", required=False, help="프로젝트 제한"),
        Param("save", "bool", required=False, default=False, help="답변 저장"),
    ),
    cli_command="ask",
    mcp_tool="ask_wiki",
    tags=("search", "write"),
))

# Read
_op(Operation(
    name="list_topics",
    description="주제 노트 목록",
    params=(
        Param("project", "str", required=False, help="프로젝트 필터"),
    ),
    cli_command=None,
    mcp_tool="list_topics",
    tags=("read",),
))

_op(Operation(
    name="read_topic",
    description="주제 노트 읽기",
    params=(
        Param("project", "str", help="프로젝트명"),
        Param("topic", "str", help="주제명"),
    ),
    cli_command=None,
    mcp_tool="read_topic",
    tags=("read",),
))

_op(Operation(
    name="get_work_log",
    description="일일 작업 로그 조회",
    params=(
        Param("date", "str", required=False, help="날짜 (YYYY-MM-DD)"),
    ),
    cli_command=None,
    mcp_tool="get_work_log",
    tags=("read",),
))

_op(Operation(
    name="get_project_context",
    description="프로젝트 컨텍스트 조회",
    params=(
        Param("project", "str", required=False, help="프로젝트명"),
    ),
    cli_command=None,
    mcp_tool="get_project_context",
    tags=("read",),
))

# Write
_op(Operation(
    name="save_insight",
    description="지식 직접 저장 (LLM 태깅 생략)",
    params=(
        Param("project", "str", help="프로젝트명"),
        Param("topic", "str", help="주제명"),
        Param("knowledge", "list[str]", help="지식 항목"),
        Param("work_summary", "str", help="작업 요약"),
    ),
    cli_command=None,
    mcp_tool="save_insight",
    tags=("write",),
))

_op(Operation(
    name="ingest_source",
    description="외부 소스 수집 (URL/파일)",
    params=(
        Param("source", "str", help="URL 또는 파일 경로"),
        Param("project", "str", required=False, help="프로젝트명"),
        Param("title", "str", required=False, help="제목"),
    ),
    cli_command="ingest",
    mcp_tool="ingest_source",
    tags=("write",),
))

# Pipeline
_op(Operation(
    name="run_pipeline",
    description="태깅 파이프라인 실행",
    params=(
        Param("dry_run", "bool", required=False, default=False, help="미리보기"),
    ),
    cli_command="run",
    mcp_tool=None,
    tags=("pipeline",),
))

_op(Operation(
    name="embed",
    description="임베딩 생성",
    params=(),
    cli_command="embed",
    mcp_tool=None,
    tags=("pipeline",),
))

_op(Operation(
    name="sync",
    description="Git vault 동기화",
    params=(),
    cli_command="sync",
    mcp_tool=None,
    tags=("pipeline",),
))

_op(Operation(
    name="scan",
    description="멀티 에이전트 세션 스캔",
    params=(),
    cli_command="scan",
    mcp_tool=None,
    tags=("pipeline",),
))

_op(Operation(
    name="reindex",
    description="vault에서 search.db 재구축",
    params=(),
    cli_command="reindex",
    mcp_tool=None,
    tags=("pipeline",),
))

# Admin
_op(Operation(
    name="lint",
    description="Vault 품질 점검",
    params=(),
    cli_command="lint",
    mcp_tool=None,
    tags=("admin",),
))

_op(Operation(
    name="doctor",
    description="시스템 진단",
    params=(),
    cli_command="doctor",
    mcp_tool=None,
    tags=("admin",),
))

_op(Operation(
    name="migrate",
    description="embeddings.json → search.db 마이그레이션",
    params=(),
    cli_command="migrate",
    mcp_tool=None,
    tags=("admin",),
))

_op(Operation(
    name="status",
    description="시스템 상태 조회",
    params=(),
    cli_command="status",
    mcp_tool=None,
    tags=("admin",),
))


# ── Helpers ──


def get_cli_operations() -> list[Operation]:
    """Return operations that have a CLI command."""
    return [op for op in OPERATIONS.values() if op.cli_command]


def get_mcp_operations() -> list[Operation]:
    """Return operations that have an MCP tool."""
    return [op for op in OPERATIONS.values() if op.mcp_tool]


def get_mcp_tool_names() -> set[str]:
    """Return the set of MCP tool names from the registry."""
    return {op.mcp_tool for op in OPERATIONS.values() if op.mcp_tool}


def get_cli_command_names() -> set[str]:
    """Return the set of CLI command names from the registry."""
    return {op.cli_command for op in OPERATIONS.values() if op.cli_command}
