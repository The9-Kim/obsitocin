#!/usr/bin/env python3

import json
import re
import time
from datetime import datetime
from pathlib import Path

from obsitocin.config import (
    DATA_DIR,
    LOGS_DIR,
    MAX_PROMPT_CHARS,
    MAX_RESPONSE_CHARS,
    MAX_TOOL_CONTEXT_CHARS,
    PII_ENABLED,
    PII_REDACT,
    PII_RISK_THRESHOLD,
    PII_SKIP_SENSITIVE,
    PROCESSED_DIR,
    QUEUE_DIR,
)
from obsitocin.identity import compute_source_hash, ensure_content_hash
from obsitocin.pii import PIIDetector, risk_meets_threshold
from obsitocin.provider import get_provider, run_provider_prompt

LOG_FILE = LOGS_DIR / "processor.log"

ALLOWED_CATEGORIES = {
    "development",
    "debugging",
    "architecture",
    "devops",
    "data",
    "testing",
    "tooling",
    "policy",
    "domain",
    "other",
}

SYSTEM_INSTRUCTION = (
    "You are a knowledge extraction engine for a work knowledge base. "
    "Analyze conversations and return valid JSON only. "
    "All output text (title, topics, distilled_knowledge, work_summary) MUST be in Korean. "
    "Tags should be lowercase-kebab-case in English. "
    "topics should be encyclopedia-level reusable subjects (technology, domain, policy, process) in Korean."
)

FALLBACK_TAG_KEYWORDS = {
    "python": ["python", "pytest", "pip", "venv", "django", "flask"],
    "javascript": ["javascript", "typescript", "react", "node", "npm", "pnpm"],
    "testing": ["test", "pytest", "unit test", "integration", "qa"],
    "debugging": ["error", "debug", "bug", "traceback", "exception", "fix"],
    "architecture": ["architecture", "design", "pattern", "refactor", "structure"],
    "devops": ["docker", "kubernetes", "deploy", "ci", "cd", "pipeline"],
    "data": ["sql", "database", "query", "table", "schema", "pandas"],
    "tooling": ["cli", "script", "hook", "tool", "command", "automation"],
    "api": ["api", "endpoint", "rest", "graphql", "http"],
}

FALLBACK_CATEGORY_KEYWORDS = {
    "debugging": ["error", "bug", "traceback", "exception", "failed"],
    "testing": ["test", "assert", "mock", "fixture", "qa"],
    "architecture": ["architecture", "design", "pattern", "refactor", "module"],
    "devops": ["docker", "deploy", "ci", "cd", "kubernetes", "infra"],
    "data": ["sql", "database", "query", "schema", "table"],
    "tooling": ["cli", "tool", "script", "hook", "automation"],
    "development": ["implement", "feature", "build", "code", "function"],
}

PII_DETECTOR = PIIDetector()

AGENT_OPERATIONAL_STRONG_MARKERS = {
    "delegate_task",
    "load_skills",
    "run_in_background",
    "oracle",
    "artistry",
}

AGENT_OPERATIONAL_MEDIUM_MARKERS = {
    "agent",
    "agents",
    "에이전트",
    "전문가",
    "claude code",
    "codex",
    "opencode",
    "grep",
    "ast-grep",
    "lsp",
    "tooling",
    "도구",
    "세션",
    "background",
}


def log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_pending_files() -> list[Path]:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(QUEUE_DIR.glob("*.json")):
        if f.stem.endswith("_prompt"):
            continue
        try:
            data = json.loads(f.read_text())
            if data.get("status") == "pending":
                files.append(f)
        except Exception:
            continue
    return files


def extract_tool_summary(transcript_path: str) -> dict:
    if not transcript_path:
        return {}

    try:
        files_modified: list[str] = []
        files_seen: set[str] = set()
        commands_executed: list[str] = []
        tool_counts: dict[str, int] = {}

        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message", entry)
                if msg.get("role") != "assistant":
                    continue

                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue

                    name = block.get("name", "")
                    inp = block.get("input", {})
                    tool_counts[name] = tool_counts.get(name, 0) + 1

                    if name in ("Write", "Edit"):
                        fpath = inp.get("file_path", "")
                        if (
                            fpath
                            and fpath not in files_seen
                            and len(files_modified) < 30
                        ):
                            files_seen.add(fpath)
                            files_modified.append(fpath)
                    elif name == "Bash":
                        cmd = inp.get("command", "")
                        if cmd and len(commands_executed) < 20:
                            commands_executed.append(cmd[:120])

        if not tool_counts:
            return {}

        return {
            "files_modified": files_modified,
            "commands_executed": commands_executed,
            "tool_counts": tool_counts,
        }
    except FileNotFoundError:
        log(f"Transcript file not found: {transcript_path}")
        return {}
    except Exception as e:
        log(f"Failed to extract tool summary: {e}")
        return {}


def _build_tool_context(tool_summary: dict | None) -> str:
    if not tool_summary or not any(
        tool_summary.get(k)
        for k in ("files_modified", "commands_executed", "tool_counts")
    ):
        return ""
    parts = []
    files = tool_summary.get("files_modified", [])
    if files:
        parts.append("Files modified: " + ", ".join(files))
    cmds = tool_summary.get("commands_executed", [])
    if cmds:
        parts.append("Commands run: " + "; ".join(cmds))
    counts = tool_summary.get("tool_counts", {})
    if counts:
        counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        parts.append(f"Tool usage: {counts_str}")
    ctx = "\n\nContext (tools used during session):\n" + "\n".join(parts)
    return ctx[:MAX_TOOL_CONTEXT_CHARS]


def _scan_existing_topics(cwd: str) -> list[str]:
    from obsitocin.config import OBS_DIR

    if not OBS_DIR:
        return []
    projects_dir = OBS_DIR / "projects"
    if not projects_dir.exists():
        return []

    topics: list[str] = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        topics_dir = project_dir / "topics"
        if not topics_dir.exists():
            continue
        for f in topics_dir.glob("*.md"):
            try:
                content = f.read_text(errors="replace")
            except OSError:
                continue
            match = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            if match:
                title = match.group(1).strip()
                project = project_dir.name
                topics.append(f"{title} [{project}]")
    return topics


def _build_existing_topics_context(cwd: str) -> str:
    existing_topics = _scan_existing_topics(cwd)
    if not existing_topics:
        return ""
    topics_list = ", ".join(existing_topics[:50])
    return (
        f"\n\n=== 기존 주제 목록 (반드시 이 중에서 먼저 선택) ===\n{topics_list}\n"
        "위 목록에 해당 내용의 주제가 이미 있으면, 반드시 기존 이름을 정확히 복사해서 사용해라. "
        "새 주제를 만들지 마라."
    )


def _build_qa_tagging_prompt(qa: dict, tool_summary: dict | None = None) -> str:
    prompt_text = qa.get("prompt", "")[:MAX_PROMPT_CHARS]
    response_text = qa.get("response", "")[:MAX_RESPONSE_CHARS]
    tool_context = _build_tool_context(tool_summary)
    existing_context = _build_existing_topics_context(qa.get("cwd", ""))

    return f"""다음 대화를 분석하고 JSON으로만 응답하세요.

질문: {prompt_text}

답변: {response_text}{tool_context}

JSON 구조:
{{
  "should_store": true,
  "title": "간결한 한글 제목 (10단어 이내)",
  "topics": [
    {{
      "name": "주제명",
      "knowledge": ["이 주제에 대해 배운 구체적 사실 1", "사실 2"]
    }}
  ],
  "work_summary": "무엇을 했는지 한 줄 요약",
  "tags": ["lowercase-kebab-case", "3-5개"],
  "category": "development | debugging | architecture | devops | data | testing | tooling | policy | domain | other",
  "importance": 3
}}

규칙:
- should_store: 나중에 다시 볼 가치가 있는 내용이면 true.
  false 조건:
  · 인사, 단순 확인("네", "진행해줘", "커밋해줘"), 빈 응답
  · 단순 명령 + 수행 보고 ("파일 수정 완료", "커밋 완료", "PR 생성 완료")
  · 시스템 내부 동작 보고 (배경 작업 ID, 세션 관리, 도구 호출 결과)
  · 에이전트/도구 자체의 운영 규칙 (예: delegate_task, load_skills, run_in_background, Oracle/Artistry 라우팅)
    단, 현재 프로젝트의 구현 대상이나 아키텍처 결정과 직접 관련된 경우는 제외
  · 대화에 새로운 사실/방법/규칙/결정이 전혀 없는 경우

- topics: 주제별로 배운 지식을 분리해서 넣어라. 최대 3개 주제.
  - name: 백과사전/위키 문서 제목 수준. 기술, 도메인 지식, 정책, 프로세스 모두 가능.
    좋은 예: "Python 가상환경 (venv)", "결제 취소 정책", "코드 리뷰 프로세스"
    나쁜 예: "요청 수락", "코드 실행", "데이터 관리" (대화 행위이거나 너무 막연함)
  - knowledge: 해당 주제에 대해 이 대화에서 배운 구체적 사실/방법/결정/규칙.
    요약하지 말고 fact를 나열. 해당 주제에 속하는 것만.
    좋은 예: "pip install -e . 로 개발 모드 설치 가능", "환불은 7일 이내만 가능"
    나쁜 예: "가상환경에 대해 논의했습니다", "작업 완료", "배경 작업 bg_xxx 완료"
    knowledge가 하나도 없으면 해당 topic을 넣지 마라.
  대화에 주제가 하나면 1개만 넣어라. 억지로 여러 개 만들지 마라.
  기존 주제 목록이 제공되면:
    1. 같은 내용이나 유사한 주제가 있으면 기존 이름을 정확히 복사해서 사용해라.
    2. "Python 가상환경 (venv)"이 이미 있는데 "Python 가상환경 관리"로 변형하지 마라.
    3. 기존 목록에 없는 완전히 새로운 주제일 때만 새 이름을 만들어라.{existing_context}
  에이전트 운영 메타지식은 주제로 만들지 마라. 현재 프로젝트 산출물에 직접 영향을 주는 지식만 주제로 승격해라.

- knowledge 추출 시 주의:
  질문에 잘못된 가정이 있고 답변에서 정정했다면, 정정된 내용만 추출해라.
  질문의 잘못된 가정을 사실로 추출하지 마라.
  예: 질문 "A가 기본이지?" → 답변 "아니, B가 기본이다" → knowledge: "B가 기본이다" (O), "A가 기본이다" (X)

- work_summary: 작업 로그에 쓸 한 줄. "무엇을 했는지" 중심.
- tags: 영문 lowercase kebab-case. 3-5개.
- category: 가장 적합한 하나만.
- importance: 1(사소) ~ 5(핵심 지식/정책/아키텍처 결정)."""


def _build_generic_tagging_prompt(item: dict, tool_summary: dict | None = None) -> str:
    source_type = item.get("source_type", "unknown")
    content = str(item.get("content", ""))[:MAX_PROMPT_CHARS]
    metadata = item.get("metadata", {})
    existing_context = _build_existing_topics_context(
        item.get("project", item.get("cwd", ""))
    )

    meta_context = ""
    if metadata:
        meta_str = ", ".join(f"{k}: {v}" for k, v in metadata.items())
        meta_context = f"\n\n메타데이터: {meta_str}"

    return f"""다음 콘텐츠를 분석하고 JSON으로만 응답하세요.

콘텐츠 유형: {source_type}
콘텐츠: {content}{meta_context}

JSON 구조:
{{
  "should_store": true,
  "title": "간결한 한글 제목 (10단어 이내)",
  "topics": [
    {{
      "name": "주제명",
      "knowledge": ["이 콘텐츠에서 배운 구체적 사실 1", "사실 2"]
    }}
  ],
  "work_summary": "무엇에 관한 내용인지 한 줄 요약",
  "tags": ["lowercase-kebab-case", "3-5개"],
  "category": "development | debugging | architecture | devops | data | testing | tooling | policy | domain | other",
  "importance": 3
}}

규칙:
- should_store: 나중에 다시 볼 가치가 있는 내용이면 true.
  false 조건: 단순 알림, 빈 내용, 실질적 정보 없는 메시지.
- topics: 이 콘텐츠에서 다루는 주제별 지식. 최대 3개.
  - name: 백과사전/위키 수준의 재사용 가능한 주제명
  - knowledge: 해당 주제에 대한 구체적 사실/정보
- work_summary: 이 콘텐츠가 무엇에 관한 것인지 한 줄.
- tags: 영문 lowercase kebab-case. 3-5개.
- category: 가장 적합한 하나만.
- importance: 1(사소) ~ 5(핵심 지식/정책/결정).
- 기존 주제 목록이 제공되면, 유사한 주제가 이미 있을 경우 기존 이름을 정확히 복사해서 사용해라. 새 주제를 만들지 마라.{existing_context}"""


def build_tagging_prompt(item: dict, tool_summary: dict | None = None) -> str:
    source_type = item.get("source_type", "claude_code")
    if source_type in {"claude_code", "unknown"}:
        return _build_qa_tagging_prompt(item, tool_summary)
    if source_type in {"slack", "jira", "confluence", "git", "manual"}:
        return _build_generic_tagging_prompt(item, tool_summary)
    return _build_qa_tagging_prompt(item, tool_summary)


def extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _coerce_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    else:
        raw_items = []

    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _normalize_tag(tag: str) -> str:
    normalized = tag.strip().lower().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"[^a-z0-9-]", "", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def _topic_knowledge(topic: dict) -> list[str]:
    return [str(item).strip() for item in topic.get("knowledge", []) if str(item).strip()]


def _is_agent_operational_topic(topic: dict, qa: dict) -> bool:
    if str(qa.get("source_type", "claude_code")).strip() != "claude_code":
        return False

    fragments = [
        str(topic.get("name", "")),
        * _topic_knowledge(topic),
        str(qa.get("prompt", "")),
        str(qa.get("response", "")),
    ]
    text = "\n".join(fragment for fragment in fragments if fragment).lower()
    if not text:
        return False

    strong_hits = sum(1 for marker in AGENT_OPERATIONAL_STRONG_MARKERS if marker in text)
    medium_hits = sum(1 for marker in AGENT_OPERATIONAL_MEDIUM_MARKERS if marker in text)
    return strong_hits >= 2 or (strong_hits >= 1 and medium_hits >= 2)


def apply_quality_filters(result: dict, qa: dict) -> tuple[dict, str | None]:
    filtered_topics = [
        topic
        for topic in result.get("topics", [])
        if not _is_agent_operational_topic(topic, qa)
    ]

    if len(filtered_topics) == len(result.get("topics", [])):
        return result, None

    updated = dict(result)
    updated["topics"] = filtered_topics
    updated["key_concepts"] = [topic["name"] for topic in filtered_topics]
    updated["distilled_knowledge"] = [
        knowledge for topic in filtered_topics for knowledge in _topic_knowledge(topic)
    ]

    if filtered_topics:
        return updated, "filtered-agent-operational-topics"

    updated["should_store"] = False
    updated["summary"] = "에이전트 운영 메타지식은 저장하지 않음"
    updated["work_summary"] = "에이전트 운영 메타지식은 저장하지 않음"
    return updated, "agent-operational-meta"


def normalize_result(result: dict | None) -> dict | None:
    if not isinstance(result, dict):
        return None

    title = str(result.get("title", "")).strip() or "Untitled"

    should_store = result.get("should_store", True)
    if not isinstance(should_store, bool):
        should_store = str(should_store).strip().lower() not in ("false", "0", "no")

    raw_topics = result.get("topics") or result.get("key_concepts") or []
    topics: list[dict] = []
    if isinstance(raw_topics, list):
        for item in raw_topics[:3]:
            if isinstance(item, dict) and "name" in item:
                topics.append(
                    {
                        "name": str(item["name"]).strip(),
                        "knowledge": _coerce_string_list(item.get("knowledge")),
                    }
                )
            elif isinstance(item, str) and item.strip():
                topics.append({"name": item.strip(), "knowledge": []})

    work_summary = str(result.get("work_summary") or result.get("summary", "")).strip()

    tags = [_normalize_tag(tag) for tag in _coerce_string_list(result.get("tags"))]
    tags = [tag for tag in tags if tag]
    if not tags:
        tags = ["developer-qna"]

    category = str(result.get("category", "other")).strip().lower()
    if category not in ALLOWED_CATEGORIES:
        category = "other"

    importance = result.get("importance", 3)
    if not isinstance(importance, int) or not (1 <= importance <= 5):
        importance = 3

    memory_type = "static" if importance >= 4 else "dynamic"

    topic_names = [t["name"] for t in topics]
    all_knowledge = []
    for t in topics:
        all_knowledge.extend(t["knowledge"])

    return {
        "title": title,
        "should_store": should_store,
        "topics": topics,
        "work_summary": work_summary,
        "tags": tags[:6],
        "category": category,
        "importance": importance,
        "memory_type": memory_type,
        "summary": work_summary,
        "key_concepts": topic_names,
        "distilled_knowledge": all_knowledge,
    }


def fallback_tagging_result(qa: dict, tool_summary: dict | None = None) -> dict:
    source_type = str(qa.get("source_type", "claude_code")).strip() or "claude_code"
    if source_type == "claude_code":
        prompt_text = str(qa.get("prompt", "")).strip()
        response_text = str(qa.get("response", "")).strip()
    else:
        content = str(qa.get("content", "")).strip()
        prompt_text = content[:500]
        response_text = ""
    combined = f"{prompt_text}\n{response_text}".lower()

    tags = [
        tag
        for tag, patterns in FALLBACK_TAG_KEYWORDS.items()
        if any(pattern in combined for pattern in patterns)
    ]
    if tool_summary and tool_summary.get("tool_counts"):
        tags.append("tooling")
    tags.append("developer-qna")

    category = "other"
    for candidate, patterns in FALLBACK_CATEGORY_KEYWORDS.items():
        if any(pattern in combined for pattern in patterns):
            category = candidate
            break

    topics: list[str] = []
    for source in (prompt_text, response_text):
        topics.extend(re.findall(r"`([^`]{2,60})`", source))
    if not topics:
        if "venv" in combined:
            topics.append("파이썬 가상환경 (venv)")
        elif "docker" in combined:
            topics.append("Docker")
        elif "api" in combined:
            topics.append("API")

    title_source = prompt_text or response_text or "개발자 대화"
    title = re.sub(r"\s+", " ", title_source).strip()[:40] or "개발자 대화"

    is_trivial = len(prompt_text) < 20 and len(response_text) < 20
    work_summary = (
        f"LLM 태깅 실패 — 원문 기반 메타데이터. {prompt_text[:80].strip()}"
        if prompt_text.strip()
        else "LLM 태깅 실패 — 기본 메타데이터 생성"
    )

    topic_objects = [{"name": t, "knowledge": []} for t in topics[:3]]

    return normalize_result(
        {
            "title": title,
            "should_store": not is_trivial,
            "topics": topic_objects,
            "work_summary": work_summary,
            "tags": tags,
            "category": category,
            "importance": 2,
        }
    ) or {
        "title": "개발자 대화",
        "should_store": False,
        "topics": [],
        "work_summary": "LLM 태깅 실패 — 기본 메타데이터 생성",
        "tags": ["developer-qna"],
        "category": "other",
        "importance": 2,
        "memory_type": "dynamic",
        "summary": "LLM 태깅 실패 — 기본 메타데이터 생성",
        "key_concepts": [],
        "distilled_knowledge": [],
    }


def apply_pii_policy(
    qa: dict, *, enabled: bool, redact: bool, skip_sensitive: bool
) -> dict:
    prompt_text = str(qa.get("prompt", ""))
    response_text = str(qa.get("response", ""))
    combined = f"{prompt_text}\n{response_text}"
    result = PII_DETECTOR.scan(combined)
    qa["pii_scan"] = {
        "detected": result.detected,
        "risk_level": result.risk_level,
        "pii_types": result.pii_types,
    }

    if not enabled or not result.detected:
        return {"qa": qa, "action": "allow"}

    if skip_sensitive and risk_meets_threshold(result.risk_level, PII_RISK_THRESHOLD):
        qa["status"] = "skipped"
        qa["skip_reason"] = f"pii:{result.risk_level}"
        return {"qa": qa, "action": "skip"}

    if redact:
        qa["prompt"] = PII_DETECTOR.redact(prompt_text)
        qa["response"] = PII_DETECTOR.redact(response_text)
        qa["pii_scan"]["redacted"] = True
        return {"qa": qa, "action": "redact"}

    return {"qa": qa, "action": "allow"}


def find_existing_by_content_hash(content_hash: str) -> tuple[Path, dict] | None:
    if not content_hash:
        return None
    for filepath in sorted(PROCESSED_DIR.glob("*.json")):
        try:
            qa = json.loads(filepath.read_text())
        except Exception:
            continue
        if qa.get("content_hash") == content_hash:
            return filepath, qa
    return None


def preview_pending_run(
    provider_name: str | None = None,
    *,
    pii_enabled: bool | None = None,
    pii_redact: bool | None = None,
    pii_skip_sensitive: bool | None = None,
) -> dict:
    pending = get_pending_files()
    active_provider = provider_name or "default"
    enabled = PII_ENABLED if pii_enabled is None else pii_enabled
    redact = PII_REDACT if pii_redact is None else pii_redact
    skip_sensitive = (
        PII_SKIP_SENSITIVE if pii_skip_sensitive is None else pii_skip_sensitive
    )
    enabled = enabled or redact or skip_sensitive

    details: list[dict] = []
    for filepath in pending:
        try:
            qa = json.loads(filepath.read_text())
        except Exception:
            details.append({"file": filepath.name, "action": "invalid-json"})
            continue
        content_hash = ensure_content_hash(qa)
        duplicate = find_existing_by_content_hash(content_hash)
        if duplicate is not None:
            details.append(
                {
                    "file": filepath.name,
                    "action": "duplicate",
                    "duplicate_of": duplicate[0].name,
                }
            )
            continue
        policy = apply_pii_policy(
            dict(qa),
            enabled=enabled,
            redact=redact,
            skip_sensitive=skip_sensitive,
        )
        action = "process"
        if policy["action"] == "skip":
            action = "skip-sensitive"
        elif policy["action"] == "redact":
            action = "process-redacted"
        details.append({"file": filepath.name, "action": action})

    return {
        "provider": active_provider,
        "pending": len(pending),
        "details": details,
    }


def call_tagging(prompt: str, provider_name: str | None = None) -> dict | None:
    provider_label = provider_name or "default"
    try:
        provider = get_provider(provider_name)
        provider_label = provider.name
        content = run_provider_prompt(
            f"{SYSTEM_INSTRUCTION}\n\n{prompt}",
            provider_name=provider_name,
        )
    except Exception as e:
        log(f"{provider_label} tagging failed: {e}")
        return None

    content = content.strip()
    log(f"Raw output length: {len(content)} chars")

    try:
        outer = json.loads(content)
        if isinstance(outer, dict) and isinstance(outer.get("response"), str):
            content = outer["response"]
    except json.JSONDecodeError:
        pass

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = extract_json(content)

    if parsed is None:
        log(f"Failed to parse JSON from output: {content[:200]}...")
        return None

    return normalize_result(parsed)


def process_file(
    filepath: Path,
    provider_name: str | None = None,
    *,
    pii_enabled: bool | None = None,
    pii_redact: bool | None = None,
    pii_skip_sensitive: bool | None = None,
) -> bool:
    try:
        qa = json.loads(filepath.read_text())
    except Exception as e:
        log(f"Failed to read {filepath.name}: {e}")
        return False

    source_type = str(qa.get("source_type", "claude_code")).strip() or "claude_code"
    if "source_type" not in qa:
        qa["source_type"] = source_type
    if "source_metadata" not in qa:
        qa["source_metadata"] = {}
    if not qa.get("content_hash") and source_type != "claude_code":
        content = str(qa.get("content", ""))
        metadata = qa.get("source_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        qa["content_hash"] = compute_source_hash(source_type, content, metadata)

    if source_type == "claude_code":
        prompt_text = str(qa.get("prompt", "")).strip()
        response_text = str(qa.get("response", "")).strip()
        has_content = bool(prompt_text or response_text)
        empty_reason = "Empty Q&A (no prompt, no response)"
    else:
        prompt_text = str(qa.get("content", "")).strip()
        response_text = ""
        has_content = bool(prompt_text)
        empty_reason = f"Empty source item (no content, source_type={source_type})"

    if not has_content:
        log(f"{empty_reason}: {filepath.name}, skipping")
        filepath.unlink()
        return True

    content_hash = ensure_content_hash(qa)
    duplicate = find_existing_by_content_hash(content_hash)
    if duplicate is not None:
        qa["status"] = "duplicate"
        qa["duplicate_of"] = duplicate[0].name
        qa["processed_at"] = datetime.now().isoformat()
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out_file = PROCESSED_DIR / filepath.name
        out_file.write_text(json.dumps(qa, ensure_ascii=False, indent=2))
        filepath.unlink()
        log(f"Duplicate skipped: {filepath.name} → {duplicate[0].name}")
        return True

    enabled = PII_ENABLED if pii_enabled is None else pii_enabled
    redact = PII_REDACT if pii_redact is None else pii_redact
    skip_sensitive = (
        PII_SKIP_SENSITIVE if pii_skip_sensitive is None else pii_skip_sensitive
    )
    enabled = enabled or redact or skip_sensitive
    policy = apply_pii_policy(
        qa,
        enabled=enabled,
        redact=redact,
        skip_sensitive=skip_sensitive,
    )
    qa = policy["qa"]
    if policy["action"] == "skip":
        qa["processed_at"] = datetime.now().isoformat()
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out_file = PROCESSED_DIR / filepath.name
        out_file.write_text(json.dumps(qa, ensure_ascii=False, indent=2))
        filepath.unlink()
        log(f"PII-sensitive entry skipped: {filepath.name}")
        return True

    tool_summary = {}
    transcript_path = qa.get("transcript_path", "")
    if transcript_path:
        tool_summary = extract_tool_summary(transcript_path)

    prompt = build_tagging_prompt(qa, tool_summary)
    result = call_tagging(prompt, provider_name)

    if result is None:
        result = fallback_tagging_result(qa, tool_summary)
        qa["tagging_fallback"] = True
        log(f"Used fallback tagging for {filepath.name}")

    result, quality_filter_reason = apply_quality_filters(result, qa)

    if tool_summary:
        qa["tool_summary"] = tool_summary
    if "source_type" not in qa:
        qa["source_type"] = source_type
    if "source_metadata" not in qa:
        qa["source_metadata"] = {}
    qa["tagging_result"] = result

    if not result.get("should_store", True):
        qa["status"] = "filtered"
        qa["filter_reason"] = quality_filter_reason or "should_store=false"
        qa["processed_at"] = datetime.now().isoformat()
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out_file = PROCESSED_DIR / filepath.name
        out_file.write_text(json.dumps(qa, ensure_ascii=False, indent=2))
        filepath.unlink()
        log(f"Filtered (not worth storing): {filepath.name}")
        return True

    qa["status"] = "processed"
    qa["processed_at"] = datetime.now().isoformat()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_file = PROCESSED_DIR / filepath.name
    out_file.write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    filepath.unlink()
    log(f"Processed: {filepath.name} → {out_file.name}")

    try:
        from obsitocin.topic_writer import write_notes_for_qa

        result = write_notes_for_qa(qa)
        log(f"Topic writer: {result.get('topics_written', 0)} topic(s) written")
    except Exception as e:
        log(f"Topic writer failed (non-fatal): {e}")

    return True


ORPHAN_MAX_AGE_SECONDS = 3600


def cleanup_orphan_prompts() -> None:
    now = time.time()
    removed = 0
    for f in QUEUE_DIR.glob("*_prompt.json"):
        age = now - f.stat().st_mtime
        if age > ORPHAN_MAX_AGE_SECONDS:
            f.unlink()
            removed += 1
            log(f"Removed orphan prompt ({age / 3600:.1f}h old): {f.name}")
    if removed:
        log(f"Cleaned up {removed} orphan prompt file(s)")


def main(
    provider_name: str | None = None,
    *,
    pii_enabled: bool | None = None,
    pii_redact: bool | None = None,
    pii_skip_sensitive: bool | None = None,
) -> None:
    import fcntl

    from obsitocin.config import LLM_PROVIDER

    active_provider = provider_name or LLM_PROVIDER

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    lock_file = DATA_DIR / "processor.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("Another processor instance is running, exiting")
        lock_fd.close()
        return

    try:
        log("=" * 50)
        log(f"Processor started (provider: {active_provider})")

        if active_provider == "qwen":
            from obsitocin.qwen_client import start_qwen_server

            start_qwen_server()

        cleanup_orphan_prompts()

        pending = get_pending_files()
        if not pending:
            log("No pending files in queue, exiting")
            return

        log(f"Found {len(pending)} pending file(s)")
        success = 0
        fail = 0
        for f in pending:
            log(f"Processing: {f.name}")
            if process_file(
                f,
                active_provider,
                pii_enabled=pii_enabled,
                pii_redact=pii_redact,
                pii_skip_sensitive=pii_skip_sensitive,
            ):
                success += 1
            else:
                fail += 1

        log(f"Processing complete: {success} success, {fail} failed")

        if success > 0:
            try:
                from obsitocin.embeddings import (
                    build_embeddings_for_qas,
                    is_configured as embed_configured,
                    start_embed_server,
                    stop_embed_server,
                )

                if embed_configured():
                    log("Starting embedding generation...")
                    qa_files = []
                    for filepath in PROCESSED_DIR.glob("*.json"):
                        try:
                            qa = json.loads(filepath.read_text())
                            if qa.get("status") == "processed":
                                qa_files.append((filepath.stem, qa))
                        except Exception:
                            continue

                    if qa_files:
                        try:
                            start_embed_server()
                            count = build_embeddings_for_qas(qa_files)
                            log(f"Generated {count} new embeddings")
                        finally:
                            stop_embed_server()
                else:
                    log(
                        "Embedding subsystem is not configured, skipping embedding generation"
                    )
            except Exception as e:
                log(f"Embedding generation failed (non-fatal): {e}")

        if success > 0:
            log("Topic notes written inline during processing")
    finally:
        if active_provider == "qwen":
            from obsitocin.qwen_client import stop_qwen_server

            stop_qwen_server()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
