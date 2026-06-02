# Architecture

## 목표

IT 분야의 특정 주제에 대해 공식 문서, 국제 표준, 주요 논문을 우선 수집하고, 구조 분류와 실서비스 기본형을 Obsidian Markdown으로 저장한다.

## 핵심 판단

OpenAI API 중심으로 빠르게 시작한다. 다만 provider, source connector, Obsidian writer를 분리해서 특정 프레임워크에 잠기지 않게 한다. 현재 provider 정책은 `auto`가 기본이며 OpenAI key가 있으면 OpenAI, 없으면 Gemini key를 선택한다. 두 key가 모두 있으면 CLI의 `--provider openai|gemini` 또는 설정의 `[llm].provider`로 고정한다.

공통 모듈은 Smoke Test 프로젝트 전체가 아니라 `Common Module`의 재사용 가능한 일부만 붙인다. 현재 재사용 대상은 `llm_key_manager`와 `obsidian_connector`이며, Research Agent의 reviewed/evergreen 보호 정책은 별도 wrapper에서 유지한다.

## 컴포넌트

```text
CLI or local web UI
  -> Research Orchestrator
    -> Query Planner
    -> Source Router
    -> Source Collectors
    -> Evidence Extractor
    -> Classifier
    -> Blueprint Writer
    -> Obsidian Publisher
```

## 역할

### Research Orchestrator

하나의 research run을 관리한다. 입력 주제, 실행 상태, 비용, 수집 출처, 생성 파일 목록을 기록한다.

MVP에서는 단순 Python 또는 TypeScript script로 시작한다. 장기 실행, 재시작, human-in-the-loop가 필요해지면 LangGraph 또는 OpenAI Agents SDK로 확장한다.

### Query Planner

사용자 질문을 다음 쿼리 묶음으로 나눈다.

- 공식 문서 쿼리
- 표준/보안 기준 쿼리
- 논문 쿼리
- 구현 사례 쿼리
- 반례/한계 쿼리

### Source Router

출처 우선순위를 적용한다.

1. Vendor official docs
2. Standards and security frameworks
3. Peer-reviewed papers and major preprints
4. API metadata sources
5. High-signal engineering articles
6. General web search

### Source Collectors

초기 connector는 다음으로 충분하다.

- OpenAI web search tool
- Gemini Google Search tool
- arXiv API
- Crossref REST API
- Semantic Scholar Academic Graph API
- OpenAlex API
- Direct URL fetcher

collector 실패는 출처 노트가 아니라 run-log warning으로 기록한다.

### Evidence Extractor

각 source에서 다음을 추출한다.

- 주장
- 근거 문장 요약
- source URL
- 발행일 또는 업데이트일
- source type
- confidence
- 관련 taxonomy 후보

### Classifier

주제를 구조 분류한다.

예시:

- architecture pattern
- retrieval strategy
- orchestration model
- evaluation method
- security risk
- production readiness
- integration surface

### Blueprint Writer

"실서비스에 가장 유용한 기본형"을 작성한다.

항상 포함할 항목:

- 기본 구조
- 언제 쓰는가
- 피해야 하는 경우
- MVP 구현 순서
- 운영 리스크
- 검증 방법
- 출처

### Obsidian Publisher

Obsidian vault에 Markdown 파일을 쓴다. Obsidian은 vault를 로컬 파일 폴더로 저장하고 외부 변경을 자동 반영하므로, agent는 파일 writer만 안정적으로 구현하면 된다.

## 저장 정책

초안은 `00_Inbox` 또는 `90_Drafts`에 저장한다. 사람이 확인한 노트는 `20_Taxonomy`, `30_Service-Blueprints`, `40_Comparisons`로 이동한다.

agent는 reviewed note를 직접 덮어쓰지 않는다.

## 확장 경로

### Phase 1

OpenAI Responses API + Gemini fallback + local tools + Obsidian writer.

### Phase 2

OpenAI Agents SDK로 tool, state, approval, tracing을 코드 중심으로 관리.

### Phase 3

LangGraph로 durable execution과 재시작 가능한 workflow 추가.

### Phase 4

LlamaIndex로 vault와 PDF/HTML corpus 인덱싱, 재검색, evaluation 추가.
