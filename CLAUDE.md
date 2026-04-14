# obsitocin

소스 어댑터 → LLM 태깅 → Obsidian vault 축적 → MCP 서버. 패키지: `obsitocin`, CLI: `obsitocin`

## 아키텍처 핵심

- `processor.py`: `source_type` 분기 태깅 (`claude_code` → Q&A 프롬프트, 기타 → 범용 프롬프트). `compute_content_hash()` 출력 변경 금지 (레거시 호환).
- `source_adapter.py`: `SourceItem` Protocol + `KNOWN_SOURCE_TYPES` = {claude_code, codex, gemini, claude_ai, slack, jira, confluence, git, manual}. 새 소스는 여기 등록.
- `mcp_server.py`: FastMCP 8개 도구 — list_topics, read_topic, get_work_log, save_insight, get_project_context, ingest_source, ask_wiki, recall. `fastmcp`는 optional dep.
- `embeddings.py`: Q&A + 주제 노트를 동일 인덱스에 저장. 주제 노트 키: `topic:{project}:{title}`. JSON + SQLite 듀얼 라이트.
- `search_db.py`: SQLite + FTS5 검색 DB. BM25 키워드 검색 + 벡터 검색. `embeddings.json` 대체/보완. 마이그레이션: `obsitocin migrate`.
- `hybrid_search.py`: BM25 + 벡터를 RRF(k=60)로 결합. mode: hybrid/bm25/vector.
- `chunker.py`: 긴 Q&A 텍스트를 3000자 단위 + 15% 오버랩으로 청크 분할. 임베딩 정밀도 향상.
- `tokenizer.py`: FTS5용 토크나이저. `UnicodeTokenizer`(기본) / `KiwiTokenizer`(한국어 형태소, optional `kiwipiepy`).
- `memory_query.py`: 듀얼 패스 — `search.db` 있으면 hybrid search, 없으면 기존 JSON brute-force 폴백. `topic:*` 엔트리를 `source_type: "topic_note"`로 포함.
- `git_sync.py`: Git vault 동기화. pull → process → commit → push. 충돌 자동 해결(생성 파일은 ours, topic은 USER NOTES 병합).
- `topic_writer.py`: `update_moc()`에서 핵심 지식 첫 bullet → 한줄 요약. `<!-- OBSITOCIN:BEGIN USER NOTES -->` 블록만 보존. 원문 보존: `raw/sessions/YYYY-MM-DD/` 에 immutable session note 저장.
- `ingest.py`: 수동/외부 소스 수집 진입점. 원문은 `raw/`에 보존하고, 요약은 `projects/<project>/sources/`에 source page로 저장한 뒤 관련 topic note를 갱신.
- `lint.py`: 7가지 점검 — 깨진 위키링크, 고아 주제, 빈약 노트, MOC 불일치, DB↔vault 정합성, FTS 무결성, 고아 임베딩.
- `reindex.py`: vault MD에서 search.db를 재구축. `reindex_from_vault()` (주제 노트) + `reindex_from_processed()` (QA).
- `session_scanner.py`: 멀티 에이전트 세션 로그 스캔. claude_code/codex/gemini 디렉토리 탐색 → queue 변환.
- `qa_logger.py`: 훅 핸들러. queue JSON에 `source_type: "claude_code"` + `source_metadata` 포함.
- 훅 등록은 `~/.claude/settings.json`에 절대 경로(`runtime` python + repo `src`)를 저장하므로, 저장소 이동/이름 변경 시 재등록이 필요하다.
- `cli.main()`은 일반 명령 실행 전에 `register_hooks()`를 한 번 더 호출해 stale hook 경로를 자동 복구한다.
- `identity.py`: `compute_content_hash()` (레거시, 변경 금지) + `compute_source_hash()` (범용).
- `pii.py`: 정규식 PII 감지. `--detect-pii --redact-pii --skip-sensitive`.
- 태깅 프롬프트: "질문의 잘못된 가정을 사실로 추출하지 말 것" 가드 포함.

## 파이프라인

```
소스 → 어댑터(source_type) → queue/ → processor(분기) → LLM 태깅 → processed/
  → embeddings(Q&A+주제노트) → topic_writer → vault/{projects,daily,raw/sessions,_MOC.md}
  → search.db(SQLite+FTS5) → hybrid_search(BM25+Vector+RRF)

MCP (obsitocin serve): search_knowledge | recall | list/read_topic | get_work_log | save_insight | get_project_context | ingest_source | ask_wiki

obsitocin scan: claude_code/codex/gemini 세션 로그 → queue (멀티 에이전트)
obsitocin sync: git pull → process → commit → push (multi-device vault sync)
obsitocin reindex: vault MD → search.db 재구축 (MD = source of truth)
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
| Tokenizer | `OBS_TOKENIZER` | `unicode` (`kiwi` for Korean) |
| Git 자동 동기화 | `OBS_GIT_AUTO_SYNC` | `false` |
| Git Remote | `OBS_GIT_REMOTE` | `origin` |

## 데이터 모델

Queue: `pending → processed → written`. `should_store=false` → `filtered`.
Queue JSON: `source_type` (기본 "claude_code") + `source_metadata`. 레거시(source_type 없음)도 자동 처리.

`tagging_result`: title, should_store, topics[{name, knowledge}], work_summary, tags, category, importance, memory_type(≥4→static).

## Vault 노트

- **주제**: `projects/<p>/topics/*.md` — 핵심 지식 축적 + User Notes 보존
- **인덱스**: `projects/<p>/_index.md`
- **로그**: `daily/YYYY-MM-DD.md`
- **MOC**: `_MOC.md` — 프로젝트별 주제 + 한줄 요약
- **원문**: `raw/sessions/YYYY-MM-DD/HH-MM-SS_{sid}.md` — immutable Q&A 보존 (type: session-raw, content_hash로 멱등)

## 개발

```bash
pip install -e ".[mcp,korean]"
.venv/bin/python3 -m unittest discover -s tests -v
obsitocin migrate  # embeddings.json → search.db 마이그레이션
```

## 설계 원칙

- 정확한 경로 위키링크 (짧은 링크 지양)
- `compute_content_hash()` 출력 절대 변경 금지
- `fastmcp`, `kiwipiepy`는 optional — core CLI는 zero dependencies (sqlite3, struct = stdlib)
- 소스 어댑터는 `typing.Protocol` (ABC 없음, duck typing)
- OBS_VAULT_DIR 미설정 시 오류 종료
- MD가 source of truth — search.db는 vault에서 재구축 가능한 파생 캐시
- search.db 없으면 기존 JSON 경로 그대로 폴백 (하위 호환)
