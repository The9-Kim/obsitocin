# obsitocin

소스 어댑터 → LLM 태깅 → Obsidian vault 축적 → MCP 서버. 패키지: `obsitocin`, CLI: `obsitocin`

## 아키텍처 핵심

- `processor.py`: `source_type` 분기 태깅 (`claude_code` → Q&A 프롬프트, 기타 → 범용 프롬프트). `compute_content_hash()` 출력 변경 금지 (레거시 호환).
- `source_adapter.py`: `SourceItem` Protocol + `KNOWN_SOURCE_TYPES` = {claude_code, slack, jira, confluence, git, manual}. 새 소스는 여기 등록.
- `mcp_server.py`: FastMCP 6개 도구 — search_knowledge, list_topics, read_topic, get_work_log, save_insight, get_project_context. `fastmcp`는 optional dep.
- `embeddings.py`: Q&A + 주제 노트를 동일 인덱스에 저장. 주제 노트 키: `topic:{project}:{title}`.
- `memory_query.py`: `topic:*` 엔트리를 `source_type: "topic_note"`로 검색 결과에 포함.
- `topic_writer.py`: `update_moc()`에서 핵심 지식 첫 bullet → 한줄 요약. `<!-- OBSITOCIN:BEGIN USER NOTES -->` 블록만 보존.
- `ingest.py`: 수동/외부 소스 수집 진입점. 원문은 `raw/`에 보존하고, 요약은 `projects/<project>/sources/`에 source page로 저장한 뒤 관련 topic note를 갱신.
- `lint.py`: 깨진 위키링크, 고아 주제, 빈약 노트, MOC 불일치 4가지 점검.
- `qa_logger.py`: 훅 핸들러. queue JSON에 `source_type: "claude_code"` + `source_metadata` 포함.
- `identity.py`: `compute_content_hash()` (레거시, 변경 금지) + `compute_source_hash()` (범용).
- `pii.py`: 정규식 PII 감지. `--detect-pii --redact-pii --skip-sensitive`.
- 태깅 프롬프트: "질문의 잘못된 가정을 사실로 추출하지 말 것" 가드 포함.

## 파이프라인

```
소스 → 어댑터(source_type) → queue/ → processor(분기) → LLM 태깅 → processed/
  → embeddings(Q&A+주제노트) → topic_writer → vault/{projects,daily,_MOC.md}

MCP (obsitocin serve): search_knowledge | list/read_topic | get_work_log | save_insight | get_project_context
```

`obsitocin ingest`와 MCP `ingest_source`는 동일한 `obsitocin.ingest.ingest_source()`를 호출한다.

## 설정

환경 변수 → `~/.config/obsitocin/config.json` → 기본값.

| 설정 | 환경 변수 | 기본값 |
|---|---|---|
| LLM 제공자 | `OBS_LLM_PROVIDER` | `claude` |
| 데이터 | `OBS_DATA_DIR` | `~/.local/share/obsitocin` |
| Vault | `OBS_VAULT_DIR` | `init`으로 설정 |
| Claude 모델 | `OBS_CLAUDE_MODEL` | `claude-haiku-4-5` |
| Codex 모델 | `OBS_CODEX_MODEL` | `gpt-5.4-nano` |
| Gemini 모델 | `OBS_GEMINI_MODEL` | `gemini-3-flash-preview` |
| Qwen 모델 | `OBS_QWEN_MODEL_PATH` | `models/*Q4_K_M*.gguf` 스캔 |
| Embed 모델 | `OBS_EMBED_MODEL_PATH` | `models/*embed*.gguf` 스캔 |
| PII | `OBS_PII_ENABLED/REDACT/SKIP_SENSITIVE` | `false` |

## 데이터 모델

Queue: `pending → processed → written`. `should_store=false` → `filtered`.
Queue JSON: `source_type` (기본 "claude_code") + `source_metadata`. 레거시(source_type 없음)도 자동 처리.

`tagging_result`: title, should_store, topics[{name, knowledge}], work_summary, tags, category, importance, memory_type(≥4→static).

## Vault 노트

- **주제**: `projects/<p>/topics/*.md` — 핵심 지식 축적 + User Notes 보존
- **인덱스**: `projects/<p>/_index.md`
- **로그**: `daily/YYYY-MM-DD.md`
- **MOC**: `_MOC.md` — 프로젝트별 주제 + 한줄 요약

## 개발

```bash
pip install -e ".[mcp]"
.venv/bin/python3 -m unittest discover -s tests -v
```

## 설계 원칙

- 정확한 경로 위키링크 (짧은 링크 지양)
- `compute_content_hash()` 출력 절대 변경 금지
- `fastmcp`는 optional — core CLI는 zero dependencies
- 소스 어댑터는 `typing.Protocol` (ABC 없음, duck typing)
- OBS_VAULT_DIR 미설정 시 오류 종료
