# obsitocin

LLM이 점진적으로 구축·유지관리하는 **영속적인 지식 베이스**.

AI 코딩 어시스턴트(Claude Code)와의 대화에서 자동으로 지식을 수집하고, 주제별로 축적합니다.
MCP 서버로 세션 중에도 과거 지식에 접근할 수 있습니다. 범용 소스 어댑터 아키텍처로 향후 Slack, Jira, Confluence, Git 등 다양한 소스 확장이 가능합니다.

## 핵심 아이디어

대부분의 AI 도구는 세션이 끝나면 대화 내용을 잊습니다. obsitocin은 다릅니다:

- **세션 종료 시 자동 수집** — Claude Code 훅이 대화를 캡처
- **LLM이 주제별 지식을 추출** — "Docker", "결제 취소 정책" 같은 재사용 가능한 주제로 분류
- **하나의 주제 노트에 지식이 축적** — 같은 주제를 5번 대화하면 1개 노트에 합쳐짐
- **MCP 서버로 네이티브 접근** — 새 세션에서 "이 프로젝트에서 뭘 알고 있지?"에 바로 답변
- **Obsidian vault로 시각화** — 그래프 뷰에서 지식의 연결 관계를 탐색

```
Claude Code 세션
  └─ Stop 훅 → Q&A 수집 → LLM 태깅
       ↓
  topics: [
    { name: "결제 취소 정책",
      knowledge: ["7일 이내 환불 가능", "PG 수수료는 회사 부담"] }
  ]
       ↓
  기존 "결제 취소 정책" 노트에 지식 추가 (없으면 생성)
  + 작업 로그 + MOC(한줄 요약 포함) 갱신
```

### 로드맵

| 단계        | 범위                                             | 상태               |
| ----------- | ------------------------------------------------ | ------------------ |
| **Phase 1** | 개인 / Claude Code (자동 수집 + MCP 서버 + 하이브리드 검색 + Git 동기화) | ✅ 현재            |
| Phase 2     | 멀티플랫폼 (Slack, Jira, Confluence, Git 어댑터) | 아키텍처 준비 완료 |
| Phase 3     | 팀 지식 베이스 (팀원별 지식 그래프 병합)         | 계획               |

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/The9-Kim/obsitocin
cd obsitocin

# macOS는 시스템 Python 보호 정책 때문에 venv 필요
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[mcp]"           # MCP 서버 포함 설치 (권장)
pip install -e ".[mcp,korean]"    # + 한국어 형태소 분석기 (선택)
```

**alias 등록** (매번 activate 없이 사용하려면):

```bash
echo 'alias obsitocin="~/work/obsitocin/.venv/bin/obsitocin"' >> ~/.zshrc
source ~/.zshrc
```

> 경로는 clone한 위치에 맞게 수정하세요.

### 2. LLM 준비

태깅에 사용할 LLM을 하나 이상 준비하세요:

| 제공자          | 필요한 것                                                                                                 |
| --------------- | --------------------------------------------------------------------------------------------------------- |
| `claude` (기본) | Claude CLI (`claude --version`)                                                                           |
| `codex`         | Codex CLI (`codex --version`)                                                                             |
| `gemini`        | Gemini CLI (`gemini --version`)                                                                           |
| `qwen`          | llama-server + Qwen3.5 GGUF (로컬, 외부 의존성 없음) — [상세 가이드](docs/install-macos-apple-silicon.md) |

### 3. 초기화

```bash
obsitocin init --vault-dir ~/my-obsidian-vault --llm-provider claude
```

이 명령어가 하는 일:

- config 생성 (`~/.config/obsitocin/config.json`)
- data 디렉토리 생성 (`~/.local/share/obsitocin/`)
- Claude Code 훅 등록 (자동 수집 시작)
- vault-search 스킬 설치

### 4. (선택) 시맨틱 검색 + MCP 서버

```bash
# 임베딩 모델 다운로드
pip install huggingface-hub
hf download unsloth/Qwen3.5-4B-GGUF --include '*Q4_K_M*'
hf download Qwen/Qwen3-Embedding-0.6B-GGUF --include "*Q8_0*"

# llama-server 설치 (macOS)
brew install llama.cpp
```

---

## 사용법

### 자동 수집 (기본 — 아무것도 안 해도 됨)

설치 후 Claude Code를 사용하면 **자동으로 동작**합니다:

1. 세션 종료 시 Stop 훅 발동
2. LLM이 주제 + 지식 추출
3. 프로젝트별 주제 노트에 지식 축적
4. 작업 로그 + MOC 갱신

### MCP 서버 (Claude Code에서 지식 직접 접근)

```bash
obsitocin serve   # MCP 서버 시작 (stdio)
```

MCP 서버가 제공하는 도구:

| 도구                  | 설명                          | 예시                                                 |
| --------------------- | ----------------------------- | ---------------------------------------------------- |
| `search_knowledge`    | 시맨틱 검색 (Q&A + 주제 노트) | `search_knowledge("Docker 네트워크")`                |
| `list_topics`         | 프로젝트별 주제 목록          | `list_topics(project="my-api")`                      |
| `read_topic`          | 주제 노트 전체 내용           | `read_topic("my-api", "Docker")`                     |
| `get_work_log`        | 작업 로그                     | `get_work_log("2026-04-05")`                         |
| `save_insight`        | 지식 직접 저장                | `save_insight("my-api", "Redis", ["인메모리 캐시"])` |
| `get_project_context` | 프로젝트 컨텍스트 요약        | `get_project_context("my-api")`                      |

**Claude Code에서 MCP 연동:**

`.mcp.json` (프로젝트 루트) 또는 `~/.claude/mcp.json` (전역):

```json
{
  "mcpServers": {
    "obsitocin": {
      "command": "obsitocin",
      "args": ["serve"]
    }
  }
}
```

이제 Claude Code 세션에서 과거 지식을 자연어로 질문할 수 있습니다:

```
"이 프로젝트에서 Docker 관련해서 뭘 알고 있어?"
→ search_knowledge("Docker") 호출 → 축적된 지식 반환
```

**세션 시작 패턴 (권장):**

```
get_project_context("my-api-server")
→ 프로젝트 주제 목록 + 최근 작업 로그 + 중요 주제 핵심 지식 요약
```

### CLI 명령어

```bash
# 수집 파이프라인
obsitocin run                          # 수동 파이프라인 실행
obsitocin run --dry-run                # 미리보기
obsitocin run --llm-provider claude    # 제공자 오버라이드

# 검색 (업데이트)
obsitocin query "환불 정책"              # 하이브리드 검색 (BM25 + 벡터)
obsitocin query "환불 정책" --mode bm25  # 키워드 검색만
obsitocin query "환불 정책" --mode vector # 벡터 검색만
obsitocin concepts "venv"              # 주제 단위 집계 검색

# Git 동기화
obsitocin sync                           # vault Git 동기화 (pull → commit → push)
obsitocin sync --local-only              # 로컬 커밋만 (push 안 함)
obsitocin sync --dry-run                 # 미리보기

# 데이터베이스
obsitocin migrate                        # embeddings.json → SQLite 마이그레이션

# Vault 품질 관리
obsitocin lint                         # 콘텐츠 점검 (4가지)
obsitocin lint --json                  # JSON 출력
obsitocin organize --dry-run           # 정리 미리보기
obsitocin organize --min-importance 4  # 중요도 기반 정리

# 관리
obsitocin status                       # 상태 확인
obsitocin verify                       # 데이터 무결성 검사
obsitocin cleanup                      # 고아 파일 정리
obsitocin embed                        # 임베딩 재생성 (Q&A + 주제 노트)
obsitocin serve                        # MCP 서버 시작
obsitocin uninstall                    # 훅 제거
```

### Lint (vault 품질 점검)

```bash
obsitocin lint
```

4가지 점검을 수행합니다:

- **깨진 위키링크** — MOC/인덱스의 링크가 실제 파일과 일치하는지
- **고아 주제** — 다른 파일에서 참조되지 않는 주제 노트
- **빈약한 노트** — 핵심 지식이 2개 미만인 주제 (임계치 조정 가능)
- **MOC 불일치** — 파일은 있는데 MOC에 없거나, 반대 경우

---

## Vault 구조

```
your-vault/obsitocin/
├── _MOC.md                              # 전체 색인 (한줄 요약 포함)
├── projects/
│   ├── my-api-server/
│   │   ├── _index.md                    # 프로젝트 색인
│   │   └── topics/
│   │       ├── 결제 취소 정책.md         # 주제 노트 (지식 축적)
│   │       └── 환불 프로세스.md
│   └── claude-knowledge-graph/
│       ├── _index.md
│       └── topics/
│           ├── CLI 명령어 설계.md
│           └── LLM 태깅 프롬프트.md
├── raw/
│   └── sessions/
│       └── 2026-04-05/
│           └── 17-30-00_a1b2c3d4.md   # 원문 Q&A 보존
└── daily/
    └── 2026-04-05.md                    # 작업 로그
```

### 주제 노트 예시

```markdown
# 결제 취소 정책

## 핵심 지식

- 결제 후 7일 이내만 환불 가능
- 부분 환불은 관리자 승인 필요
- 환불 시 PG사 수수료는 회사 부담
- 정기결제 환불은 다음 결제일 전까지만 가능

## 히스토리

- 2026-04-05 17:30: 정기결제 환불 로직 추가
- 2026-04-05 17:00: 환불 정책 API 구현

## User Notes

<!-- OBSITOCIN:BEGIN USER NOTES -->

여기에 직접 정리한 내용을 작성하세요.

<!-- OBSITOCIN:END USER NOTES -->
```

새 세션에서 환불 관련 대화를 하면 → 이 노트에 지식이 추가됩니다.

### MOC 예시 (한줄 요약 포함)

```markdown
### my-api-server

- [[.../결제 취소 정책|결제 취소 정책]] (3) — 결제 후 7일 이내만 환불 가능
- [[.../Docker|Docker]] (2) — Docker는 컨테이너 기반 가상화 기술
```

---

## 설정

설정 파일: **`~/.config/obsitocin/config.json`**

```json
{
  "vault_dir": "~/Documents/Obsitocin",
  "llm_provider": "claude",
  "embed_model_path": "/path/to/Qwen3-Embedding-0.6B-Q8_0.gguf",
  "tokenizer": "unicode",
  "git_auto_sync": false,
  "git_remote": "origin"
}
```

- `vault_dir` 기본값: `~/Documents/Obsitocin` (`obsitocin init` 시 자동 생성)
- Qwen GGUF 모델은 `~/.cache/huggingface/hub/`에서 자동 스캔 (`OBS_QWEN_MODEL_PATH` 미설정 시)

우선순위: 환경 변수 → config.json → 기본값.

자세한 설정 항목은 [CLAUDE.md](CLAUDE.md)를 참조하세요.

---

## 아키텍처

### 소스 어댑터

obsitocin은 범용 소스 어댑터 아키텍처를 사용합니다. 현재는 Claude Code 훅이 유일한 어댑터이지만, `SourceItem` Protocol을 구현하면 어떤 소스든 추가할 수 있습니다:

```python
# 지원 소스 타입
KNOWN_SOURCE_TYPES = {"claude_code", "slack", "jira", "confluence", "git", "manual"}
```

각 소스는 동일한 파이프라인을 거칩니다:

```
소스 어댑터 → queue/ → processor (LLM 태깅) → processed/ → topic_writer → vault/
```

### 파이프라인 흐름

```
Claude Code 세션 (source_type: claude_code)
  ├─ UserPromptSubmit 훅 → qa_logger → queue/{session}_prompt.json
  └─ Stop 훅 → qa_logger → queue/{timestamp}_{session}.json
       → processor (source_type 분기: Q&A / 범용)
         → LLM 태깅 → topic_writer → vault
                     → raw/sessions/ (원문 Q&A 보존)
         → embeddings (Q&A + 주제 노트) → search.db (SQLite + FTS5)
         → hybrid_search (BM25 + 벡터 + RRF 결합)

obsitocin sync: git pull → process → commit → push (multi-device vault 동기화)

MCP 서버 (obsitocin serve)
  ├─ search_knowledge → 하이브리드 검색 (BM25 + 벡터)
  ├─ list_topics / read_topic → vault 파일시스템 직접 스캔
  ├─ save_insight → topic_writer 직접 호출 (LLM 태깅 스킵)
  └─ get_project_context → 프로젝트 지식 요약 생성
```

---

## 요구 사항

- Python 3.10+
- Claude Code CLI
- LLM 중 하나: Claude CLI / Codex CLI / Gemini CLI / llama-server + Qwen GGUF
- (선택) llama-server + Qwen3-Embedding GGUF (시맨틱 검색용)
- (선택) `fastmcp` (`pip install obsitocin[mcp]`) — MCP 서버용

## 라이선스

MIT
