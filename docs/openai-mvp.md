# OpenAI API MVP

## 결론

빠르게 만들어야 한다면 OpenAI API 중심으로 시작한다. 단, OpenAI가 "진실 저장소"가 되면 안 된다. 진실 저장소는 Obsidian Markdown이고, LLM provider는 planning, extraction, classification, synthesis를 수행하는 engine이다. 현재 MVP는 OpenAI key가 없을 때 Gemini key로 fallback할 수 있다.

## 공식 문서 기준

OpenAI 문서 기준으로 Agents SDK는 코드에서 orchestration, tool execution, approval, state를 직접 소유할 때 적합하다.

OpenAI tools 문서 기준으로 API 요청에는 function calling, web search, remote MCP, file search 같은 도구를 붙일 수 있다. 최신 정보가 필요한 prompt에서는 web search tool을 호출할 수 있다.

OpenAI models 문서 기준으로 복잡한 reasoning/coding은 `gpt-5.5`, 비용과 지연을 줄이는 작업은 `gpt-5.4-mini` 또는 `gpt-5.4-nano`가 출발점이다.

## MVP 모델 정책

```yaml
models:
  planner: gpt-5.4-mini
  extractor: gpt-5.4-mini
  classifier: gpt-5.4-mini
  synthesis: gpt-5.5
  cheap_triage: gpt-5.4-nano
```

운영 중 비용이 부담되면 `synthesis`만 상위 모델로 유지하고 나머지는 mini/nano로 낮춘다.

## API 사용 방식

### 1단계: Responses API 직접 사용

가장 빠르다. 각 단계는 명시적인 JSON schema를 출력하게 한다.

```text
plan_research()
collect_sources()
extract_evidence()
classify_structure()
write_markdown()
```

### 2단계: function tools 추가

다음 local function을 모델에 제공한다.

- `search_official_docs(query)`
- `search_papers(query)`
- `fetch_url(url)`
- `write_obsidian_note(path, markdown)`
- `append_evidence_ledger(entry)`

현재 MVP는 선택된 provider에 따라 OpenAI Responses API의 `web_search` tool 또는 Gemini Google Search tool을 사용해 공식 도메인 안에서 실제 문서 URL을 찾는다. API key가 없거나 검색이 실패하면 seed domain source로 fallback한다.

### 2.5단계: structured evidence extraction

source 수집 뒤에는 evidence claim을 JSON schema로 구조화한다.

```text
sources
-> EvidenceBundle
  -> claims[]
  -> conflicts[]
  -> needs_verification[]
-> evidence-ledger.md
```

OpenAI provider는 Responses API `text.format`의 `json_schema`를 사용하고, Gemini provider는 Gemini structured outputs의 JSON schema를 사용한다. API key가 없거나 schema 추출이 실패하면 source summary 기반 fallback evidence를 생성한다.

### 2.6단계: service blueprint stabilization

모델이 생성한 Markdown은 저장 전 필수 섹션을 검사한다. 누락된 섹션은 검토용 placeholder로 보강한다.

필수 섹션:

- One-Line Conclusion
- When To Use
- Structure Classification
- Recommended Baseline
- Implementation Order
- Operational Risks
- Verification
- Evidence
- Still Uncertain
- Related Notes

### 3단계: Agents SDK 도입

다음 조건이 생기면 Agents SDK로 옮긴다.

- agent가 여러 도구를 자율적으로 조합해야 한다.
- run state와 재개가 중요하다.
- 위험 작업 전 human review가 필요하다.
- trace와 evaluation으로 품질을 관리해야 한다.

## 최소 실행 단위

첫 MVP는 하나의 명령으로 충분하다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "agentic RAG 프레임워크 구조 분류와 실서비스 기본형"
```

생성 결과:

```text
vault/00_Inbox/YYYY-MM-DD_agentic-rag-research-run.md
vault/10_Sources/...
vault/50_Evidence-Ledger/...
vault/30_Service-Blueprints/...
```

API 키나 네트워크 없이 파일 쓰기 흐름만 검증하려면 `--offline`을 붙인다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "agentic RAG 프레임워크 구조 분류와 실서비스 기본형" --offline
```

파일을 쓰지 않고 생성 예정 경로와 safety 상태만 보려면 `--dry-run`을 붙인다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "agentic RAG 프레임워크 구조 분류와 실서비스 기본형" --dry-run
```

## 품질 게이트

최종 note를 만들기 전에 아래 조건을 검사한다.

- 공식 문서가 최소 2개 이상 있는가?
- 표준 또는 보안 기준이 관련되면 최소 1개 이상 포함했는가?
- 논문 주장이 있으면 arXiv, DOI, Semantic Scholar, Crossref 중 하나로 확인했는가?
- source마다 URL과 checked_at이 있는가?
- "확실하지 않음"을 숨기지 않았는가?

현재 CLI는 이 결과를 evidence ledger와 run-log의 `## Quality Gates` 섹션에 `PASS`, `WARN`, `FAIL`로 기록한다.

## Citation Metadata

paper source는 DOI, arXiv ID, canonical URL을 정규화해서 source note frontmatter에 남긴다. deduplication은 DOI, arXiv ID, canonical URL, title 순서로 identity key를 만든다.

paper collector 자체의 실패는 source note로 만들지 않고 run-log의 `## Warnings`에 남긴다.

## 피해야 할 것

- 처음부터 multi-agent crew로 만들기
- Obsidian plugin부터 만들기
- 모든 웹 페이지를 무차별 저장하기
- citation 없는 종합 노트 만들기
- 사람이 리뷰한 노트를 agent가 덮어쓰기
