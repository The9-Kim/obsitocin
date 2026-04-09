---
name: vault-search
description: Search the Claude knowledge graph vault for past Q&A sessions, concepts, and developer knowledge. Use when you need to find information from previous conversations, recall how something was done before, or look up stored technical knowledge. Supports Korean and English queries.
argument-hint: [검색어]
allowed-tools: Bash(obsitocin query *)
user-invocable: true
---

# 저장소 검색

시맨틱 임베딩으로 지식 그래프 저장소를 검색합니다.

## 검색어: $ARGUMENTS

!`obsitocin query "$ARGUMENTS"`

## 안내

위의 검색 결과를 바탕으로:

1. **가장 관련성 높은 결과 요약** — 상위 매치를 강조하고 쿼리와의 연관성을 설명
2. **핵심 개념** — 결과에 등장하는 위키링크 개념 나열
3. **실행 가능한 인사이트** — 결과에 해결책, 패턴, 결정 사항이 있다면 명확히 제시
4. **추가 탐색 제안** — 결과와 관련된 후속 탐색 주제가 있다면 언급

검색 결과가 없으면 대체 검색어나 더 넓은 쿼리를 제안합니다.

검색 모드:
- `hybrid` (기본): BM25 키워드 + 벡터 시맨틱 검색을 RRF로 결합
- `bm25`: 키워드 매칭만 (임베딩 불필요)
- `vector`: 벡터 유사도만 (임베딩 필요)

## MCP Server Integration

When the obsitocin MCP server is running (`obsitocin serve`), use native MCP tools instead of CLI:

| Task | MCP Tool | CLI Fallback |
|------|----------|-------------|
| Search knowledge | `search_knowledge(query)` | `obsitocin query "query"` |
| List topics | `list_topics(project)` | `obsitocin concepts "query"` |
| Read a topic | `read_topic(project, topic)` | Check vault directly |
| Get project context | `get_project_context(project)` | Read `_MOC.md` |
| Save insight | `save_insight(project, topic, knowledge)` | Manual edit |
| Ask wiki | `ask_wiki(question, project)` | `obsitocin ask "question"` |

### Session Start Pattern

At the start of a new session, call:
```
get_project_context(project="<current-project-name>")
```
This returns a summary of what you already know about the project, including topics and recent work.
