# Obsidian-First Research Agent

이 저장소는 IT 분야의 빠르게 바뀌는 프레임워크, 공식 문서, 표준, 논문을 조사해서 Obsidian vault에 구조화된 Markdown으로 남기는 개인 Research Agent 설계 공간입니다.

## 결론

빠른 MVP는 OpenAI API 중심으로 시작하되, 키가 없을 때 Gemini API로 넘어갈 수 있게 provider 경계를 분리하는 것이 좋습니다. 기본값은 `auto`이며 `OPENAI_API_KEY`가 있으면 OpenAI를 먼저 쓰고, 없으면 `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`로 Gemini를 선택합니다. 두 키를 모두 둔 경우에는 `--provider openai` 또는 `--provider gemini`로 실행마다 선택할 수 있습니다.

## 설계 원칙

1. Obsidian vault가 최종 저장소입니다.
2. agent는 검증 가능한 초안, 근거 장부, 출처 노트를 씁니다.
3. 공식 문서, 표준, 논문을 우선합니다.
4. 웹 검색 결과는 보조 근거로만 사용합니다.
5. 최종 노트는 출처 URL, 확인일, 신뢰도, 남은 의문을 포함합니다.
6. 사람이 리뷰한 노트는 덮어쓰지 않고 새 버전이나 append note로 관리합니다.

## 추천 MVP

```text
research question
-> query planner
-> source router
-> collectors
-> evidence extractor
-> taxonomy builder
-> service blueprint writer
-> Obsidian publisher
```

## 빠른 실행

아직 패키지 설치를 하지 않은 상태에서는 `PYTHONPATH=src`로 실행합니다.

먼저 상태를 점검합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault doctor
```

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault init-vault
```

API와 네트워크 없이 구조가 잘 동작하는지 먼저 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "agentic RAG 구조 분류와 실서비스 기본형" --offline
```

실제 파일을 쓰기 전에 생성 예정 경로만 확인하려면 `--dry-run`을 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "agentic RAG 구조 분류와 실서비스 기본형" --dry-run
```

LLM API를 사용할 때는 `.env`에 키를 둡니다. 하나만 있어도 되고, 둘 다 두면 실행 시 provider를 선택할 수 있습니다.

```bash
OPENAI_API_KEY=...
GEMINI_API_KEY=...
# 또는 GOOGLE_API_KEY=...
```

Gemini 쪽은 `GEMINI_API_KEY`와 `GOOGLE_API_KEY`를 모두 지원하며, 둘 다 있으면 `GOOGLE_API_KEY`를 우선 사용합니다.

그 다음 offline 플래그 없이 실행합니다. 기본 `auto`는 OpenAI key가 있으면 OpenAI를, OpenAI key가 없고 Gemini key가 있으면 Gemini를 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "OpenAI Agents SDK와 LangGraph 비교"
```

두 키가 모두 있을 때 provider를 강제로 고르려면 다음처럼 실행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run "OpenAI Agents SDK와 LangGraph 비교" --provider gemini
```

웹포탈이나 `AI Agent Archtecture` PM Portal에서 실행하려면 Research Agent Portal API를 띄웁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault serve-portal-api --host 127.0.0.1 --port 8780
```

브라우저에서 `http://127.0.0.1:8780/`을 열면 한글 기본 웹포탈 화면에서 주제, 제공자, 드라이런/오프라인 옵션을 선택해 실행할 수 있습니다.
처음 사용하는 경우 `http://127.0.0.1:8780/guide` 또는 포털 상단의 `가이드`를 열면 실행 순서, 옵션 의미, Obsidian 산출물, Research Agent Portal과 PM Portal의 차이를 볼 수 있습니다.
웹포탈은 `/job-store-health`를 읽어 job store 총량과 cleanup preview도 함께 표시합니다. `AI Agent Archtecture` PM Portal은 `/api/job-store-health`로 이 상태를 프록시합니다.

`AI Agent Archtecture` PM Portal과의 로컬 연동을 한번에 검증하려면 dry-run smoke 스크립트를 실행합니다.

```bash
python3 scripts/portal_e2e_smoke.py \
  --vault "/Users/minsungkim/Documents/Obsidian Vault" \
  --ai-portal-root "/Users/minsungkim/Documents/AI Agent Archtecture"
```

인증 경로까지 확인하려면 `--auth bearer`를 붙입니다. 토큰은 `RESEARCH_AGENT_PORTAL_E2E_TOKEN`이 있으면 사용하고, 없으면 임시 생성합니다.

일상 검증은 로컬 check 스크립트로 묶어서 실행합니다.

```bash
python3 scripts/check.py
```

개발 중 빠른 확인만 필요하면 핵심 단위 테스트 subset을 실행합니다.

```bash
python3 scripts/check.py --quick
```

CI처럼 포탈 서버와 외부 Vault를 띄우지 않는 안정적인 전체 단위 테스트만 필요하면 `--ci`를 사용합니다.

```bash
python3 scripts/check.py --ci
```

포탈 E2E smoke까지 한 번에 확인하려면 다음처럼 실행합니다.

```bash
python3 scripts/check.py --include-portal-smoke --portal-smoke-auth bearer --portal-smoke-auto-port
```

이미 Research Agent Portal API와 PM Portal이 떠 있는 상태에서 UI HTML/JS 표면만 확인하려면 다음을 실행합니다.

```bash
python3 scripts/portal_ui_smoke.py
```

포탈 job store는 기본적으로 dry-run으로 정리 후보를 확인한 뒤 apply합니다. `queued`, `running` job은 prune 대상에서 제외됩니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault \
  portal-job-cleanup --retention-days 90 --retention-limit 200

PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault \
  portal-job-cleanup --retention-days 90 --retention-limit 200 --apply
```

서버 시작 시 자동 정리를 켜야 한다면 `serve-portal-api`에 `--job-retention-days` 또는 `--job-retention-limit`를 명시합니다. 기본값은 둘 다 `0`이라 자동 prune은 꺼져 있습니다.

자세한 API 계약과 포탈 연결 방식은 [docs/portal-integration.md](docs/portal-integration.md)를 봅니다. 실행, 검증, 장애 진단 순서는 [docs/portal-runbook.md](docs/portal-runbook.md)를 기준으로 운영합니다.

기본 설정은 [config/research-agent.example.toml](config/research-agent.example.toml)에 있습니다. YAML 예시는 설계 참고용이고, 현재 CLI는 표준 라이브러리만 사용하기 위해 TOML을 읽습니다.

## 현재 구현 범위

- Python 표준 라이브러리 기반 CLI
- `doctor` 상태 점검 명령
- `run --dry-run` 생성 예정 경로 preview
- `serve-portal-api` 웹포탈/AI Agent Architecture 포탈용 JSON Runtime API
- `/job-store-health` 포탈 job store 상태와 cleanup preview API
- `scripts/check.py` quick/CI/full 단위 테스트와 선택적 포탈 E2E smoke 로컬 검증
- `scripts/portal_e2e_smoke.py` PM Portal -> Research Agent API dry-run 연동 검증
- `scripts/portal_ui_smoke.py` 실행 중인 Research/PM Portal UI static asset smoke 검증
- `docs/portal-runbook.md` 포탈 운영/검증/장애 진단 Runbook
- `portal-job-cleanup` 포탈 job store terminal job retention preview/apply 명령
- `index-vault` vault index와 backlink suggestion 생성 명령
- `backlink-proposals` backlink 검토/적용 workflow
- `review-backlinks` backlink checklist 상태 점검 명령
- `apply-reviewed-backlinks` 승인된 backlink checklist 반영 명령
- `backlink-history` backlink proposal note 이력 점검 명령
- `upgrade-bilingual` 기존 generated report note의 한글 번역 부록 preview/apply 명령
- `bilingual-audit` generated report note의 한글 병기 상태 점검 명령
- `source-audit` generated source note의 URL/논문 식별자/claim 연결 품질 점검 명령
- `official-docs-refresh` seed official docs source note의 exact URL 보강 proposal 명령
- `apply-official-docs-refresh` 승인된 exact URL proposal 반영 명령
- `paper-refresh` 논문 메타데이터 후보 proposal 명령
- `apply-paper-refresh` 승인된 논문 source note 생성 명령
- `paper-claim-refresh` metadata-only 논문 claim 보강 proposal 명령
- `apply-paper-claim-refresh` 승인된 논문 claim 보강 반영 명령
- `paper-downstream-proposals` 논문 source를 evidence ledger/service blueprint/topic map에 연결하는 proposal 명령
- `apply-paper-downstream` 승인된 논문 downstream 연결 반영 명령
- `blueprint-refresh` evidence ledger 기반 service blueprint 본문 보강 proposal 명령
- `apply-blueprint-refresh` 승인된 blueprint 본문 보강 반영 명령
- `verification-cleanup` stale 검증 문구 정리 proposal 명령
- `apply-verification-cleanup` 승인된 stale 검증 문구 정리 반영 명령
- `review-promotion-proposals` audit 통과 draft note의 reviewed 승격 proposal 명령
- `apply-review-promotion` 승인된 reviewed 승격 반영 명령
- `run-cleanup-proposals` 완료된 run 이력 archive 후보 proposal 명령
- `apply-run-cleanup` 승인된 run 이력 archive 상태 반영 명령
- `vault-health` audit/review/backlink/cleanup/stale 상태 통합 요약 명령
- Obsidian vault 폴더 생성
- reviewed/evergreen 노트 덮어쓰기 방지
- source note, evidence ledger, service blueprint, topic map, run log 생성
- Obsidian 보고서에 원본/한글 번역 병기
- 신규 run-log에 이번 run 산출물 bilingual audit 요약 기록
- OpenAI Responses API wrapper
- Gemini `generateContent` REST API wrapper
- `auto`, `openai`, `gemini` provider 선택
- OpenAI `web_search` 또는 Gemini Google Search 기반 official docs actual URL collector
- OpenAI Structured Outputs 또는 Gemini structured outputs 기반 JSON evidence extraction
- extracted evidence claim을 source note에 연결
- quality gate report를 evidence ledger와 run-log에 기록
- DOI/arXiv/canonical URL 기반 citation metadata normalization
- source provider와 source score를 source note frontmatter에 기록
- paper collector 실패를 source note 대신 run-log warning으로 기록
- service blueprint 필수 섹션 안정화
- Obsidian topic-map note와 backlink 추천 생성
- Obsidian vault index note와 orphan/stale/backlink 후보 생성
- arXiv, Semantic Scholar, Crossref, OpenAlex paper metadata collector 골격
- API 키가 없거나 `--offline`이면 deterministic fallback evidence/blueprint 생성
- `Common Module`의 `llm_key_manager`, `obsidian_connector`가 있으면 우선 사용

## Common Module 연동

Smoke Test 프로젝트 모듈은 사용하지 않습니다. 대신 검증된 공통 모듈 중 다음만 연결합니다.

- `llm_key_manager`: `.env`와 환경변수에서 OpenAI/Gemini API key를 읽고 placeholder key를 거릅니다.
- `obsidian_connector`: Obsidian vault 경로 검증, Markdown frontmatter 처리, safe path resolve를 맡깁니다.

기본 설정은 sibling 경로인 `../Common Module/src`를 바라봅니다.

```toml
[common_modules]
enabled = true
module_path = "../Common Module/src"
```

다른 위치를 쓰려면 `RESEARCH_AGENT_COMMON_MODULE_PATH` 환경변수나 설정 파일의 `module_path`를 바꿉니다. 공통 모듈이 없으면 기존 fallback 구현으로 동작합니다.

## Doctor 명령

`doctor`는 실제 research run 전에 환경과 안전 정책을 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault doctor
```

확인 항목:

- config 로딩
- Common Module 경로
- `llm_key_manager` 사용 가능 여부
- `obsidian_connector` 사용 가능 여부
- vault 경로와 쓰기 가능 여부
- reviewed/evergreen 노트 덮어쓰기 방지
- OpenAI API key 설정 여부
- Gemini API key 설정 여부
- 선택된 LLM provider
- offline smoke 준비 상태

임시 쓰기 테스트를 건너뛰려면 다음을 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault doctor --no-write-test
```

OpenAI Responses API까지 실제로 확인하려면 명시적으로 smoke test를 켭니다. 이 옵션은 작은 유료 API 호출을 1회 수행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault doctor --openai-smoke
```

Gemini API까지 실제로 확인하려면 다음을 사용합니다. 이 옵션도 작은 유료 API 호출을 1회 수행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault doctor --provider gemini --gemini-smoke
```

## Official Docs Collector

기본 `run`은 선택된 provider에 따라 OpenAI Responses API `web_search` tool 또는 Gemini Google Search tool을 사용해 설정된 공식 도메인 안에서 실제 문서 URL을 찾습니다. API key가 없거나 검색이 실패하면 seed domain source로 fallback합니다.

```toml
[sources]
official_doc_domains = [
  "developers.openai.com",
  "docs.langchain.com",
  "developers.llamaindex.ai",
  "modelcontextprotocol.io",
]
```

## Structured Evidence Extraction

기본 `run`은 source 수집 뒤 claim 단위 evidence를 먼저 구조화합니다. OpenAI provider는 Responses API Structured Outputs의 `json_schema`를 사용하고, Gemini provider는 Gemini structured outputs의 JSON schema를 사용합니다. API key가 없거나 추출이 실패하면 deterministic fallback evidence를 생성합니다.

생성되는 evidence ledger frontmatter에는 추출 모드가 남습니다.

```yaml
extraction_mode: structured-json
```

또는:

```yaml
extraction_mode: fallback
```

source note에는 evidence ledger와 연결되는 `source_id`와 claim 목록이 함께 들어갑니다.

```yaml
source_id: S001
```

생성된 source note 품질은 읽기 전용 `source-audit` 명령으로 점검합니다. 이 명령은 seed official docs URL, DOI/arXiv 누락, source URL 누락, structured claim 연결 누락을 확인합니다. 또한 전체 vault audit에서는 evidence ledger와 service blueprint에 source note 기준으로 동기화되지 않은 stale URL/title/claim 참조가 남아 있는지도 경고합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault source-audit
```

점검 결과를 Obsidian 이력으로 남기려면 `--write-note`를 추가합니다. 결과 note는 `60_Runs` 아래에 생성됩니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault source-audit --write-note
```

seed official docs source를 정확한 공식 문서 URL 후보로 보강하려면 `official-docs-refresh`를 먼저 실행합니다. 기본 실행은 source note를 수정하지 않고 후보만 출력합니다. API key가 없으면 수집을 건너뛰고 필요한 상태를 알려줍니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault official-docs-refresh
```

후보를 Obsidian proposal note로 남기려면 `--write-note`를 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault official-docs-refresh --write-note
```

proposal note에서 승인한 항목을 `- [x]`로 체크한 뒤 반영하려면 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-official-docs-refresh --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 source note의 URL/provider/score 메타데이터에 반영합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-official-docs-refresh
```

seed standards source를 정확한 표준/보안 프레임워크 문서 URL 후보로 보강하려면 `standards-refresh`를 사용합니다. 흐름은 official docs와 같습니다. 기본 실행은 source note를 수정하지 않고 후보만 출력하고, API key가 없으면 수집을 건너뜁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault standards-refresh
```

후보를 Obsidian proposal note로 남기려면 `--write-note`를 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault standards-refresh --write-note
```

proposal note에서 승인한 항목을 `- [x]`로 체크한 뒤 반영하려면 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-standards-refresh --dry-run
```

문제가 없으면 dry-run 없이 실행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-standards-refresh
```

source note URL이나 claim을 보강한 뒤 evidence ledger와 service blueprint의 참조를 source note 기준으로 동기화하려면 `sync-source-references`를 사용합니다. 기본 실행은 dry-run입니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault sync-source-references
```

출력 결과가 괜찮으면 `--apply`로 실제 반영합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault sync-source-references --apply
```

논문 후보를 기존 Vault 주제나 명시한 topic 기준으로 수집하려면 `paper-refresh`를 사용합니다. 기본 실행은 후보만 출력하고, `--write-note`를 붙이면 사람이 체크할 수 있는 proposal note와 후보 JSON metadata를 `60_Runs` 아래에 저장합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault paper-refresh "OpenAI Agents SDK와 LangGraph 비교" --write-note
```

proposal note에서 승인한 항목을 `- [x]`로 체크한 뒤 적용하려면 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-refresh --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 논문 후보만 `10_Sources/papers` 아래 source note로 생성합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-refresh
```

Crossref/OpenAlex/Semantic Scholar metadata-only claim처럼 `Crossref metadata record.` 수준으로 생성된 논문 source note는 downstream 반영 전에 `paper-claim-refresh`로 보강 후보를 만듭니다. 기본적으로 DOI 기반 Semantic Scholar, OpenAlex, Crossref 세부 메타데이터를 조회하고, 실패하면 로컬 source note 메타데이터만 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault paper-claim-refresh --write-note
```

네트워크 없이 기존 source note 메타데이터만 쓰려면 `--no-network`를 붙입니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault paper-claim-refresh --write-note --no-network
```

proposal note에서 승인할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-claim-refresh --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 source note의 Core Summary, Important Claims, Citable Evidence를 원본/한글 번역 병기 형태로 갱신합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-claim-refresh
```

새로 생성된 paper source note를 downstream 산출물에 연결하려면 `paper-downstream-proposals`로 evidence ledger, service blueprint, topic map 반영 후보를 만듭니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault paper-downstream-proposals --write-note
```

proposal note에서 승인할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 반영 대상을 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-downstream --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 evidence ledger row, service blueprint Evidence 항목, topic map Source Notes/Claim Index에 추가됩니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-paper-downstream
```

확장된 evidence ledger를 바탕으로 service blueprint의 결론, 사용 조건, 구조 분류, 구현 순서 같은 본문 섹션을 보강하려면 `blueprint-refresh`를 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault blueprint-refresh --write-note
```

proposal note에서 승인할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-blueprint-refresh --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 service blueprint의 본문 섹션을 원본/한글 번역 병기 형태로 갱신합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-blueprint-refresh
```

이전 run에서 남은 검증 문구가 현재 근거 상태와 맞지 않을 때는 `verification-cleanup`으로 정리 후보를 만듭니다. 예를 들어 논문 출처가 뒤늦게 연결되었는데 "No paper sources were collected" 같은 문구가 evidence ledger나 service blueprint에 남아 있으면 proposal note에 후보로 기록합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault verification-cleanup --write-note
```

proposal note에서 승인할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-verification-cleanup --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 stale 문구를 현재 상태에 맞는 원본/한글 문구로 교체하고 `verification_cleaned_at` frontmatter를 남깁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-verification-cleanup
```

audit와 구조 체크를 통과한 draft note를 `reviewed` 승격 후보로 모으려면 `review-promotion-proposals`를 사용합니다. 이 명령은 기본적으로 source note, evidence ledger, service blueprint, topic map만 대상으로 삼고, stale 문구나 audit issue가 남은 노트는 skipped 사유로 남깁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault review-promotion-proposals --write-note
```

proposal note에서 사람이 승격할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-review-promotion --dry-run
```

문제가 없으면 dry-run 없이 실행합니다. 체크된 항목만 `status: reviewed`와 `reviewed_at` frontmatter를 받습니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-review-promotion
```

`60_Runs` 아래에 완료된 proposal note, 최신 clean audit에 의해 대체된 과거 audit note, 최신 snapshot에 의해 대체된 과거 `next-actions`/`vault-health` note가 쌓이면 `run-cleanup-proposals`로 archive 후보를 만들 수 있습니다. 이 workflow는 파일을 삭제하거나 이동하지 않고, 승인된 노트의 frontmatter에만 `status: archived`를 남깁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault run-cleanup-proposals --write-note
```

proposal note에서 승인할 항목을 `- [x]`로 체크한 뒤, 먼저 dry-run으로 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-run-cleanup --dry-run
```

문제가 없으면 dry-run 없이 실행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-run-cleanup
```

Vault의 현재 운영 상태를 한 화면으로 확인하려면 `vault-health`를 사용합니다. 이 명령은 source audit, bilingual audit, reviewed 승격 상태, backlink 후보와 checklist, run cleanup 후보, stale generated note를 함께 요약합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault vault-health
```

상태 스냅샷을 `60_Runs`에 남기려면 `--write-note`를 붙입니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault vault-health --write-note
```

## Quality Gates

`run`은 생성된 source, evidence, service blueprint를 기준으로 quality gate를 평가하고, 결과를 evidence ledger와 run-log의 `## Quality Gates` 섹션에 남깁니다.

또한 run이 끝나면 이번 run에서 생성된 산출물 묶음만 대상으로 bilingual audit을 실행하고, run-log의 `## Bilingual Audit` 섹션에 요약을 남깁니다.

현재 gate:

- 최소 official docs source 수
- source URL 누락 여부
- run `checked_at` 존재 여부
- evidence ledger claim 존재 여부
- service blueprint의 `Still Uncertain` 섹션 존재 여부

결과는 `PASS`, `WARN`, `FAIL` 중 하나로 기록됩니다. 기준값은 설정 파일의 `[quality_gates]`에서 조정합니다.

## Bilingual Obsidian Reports

`run`이 생성하는 source note, evidence ledger, service blueprint, topic map, run log는 원본과 한글 번역을 함께 남깁니다. frontmatter에는 다음 필드가 들어갑니다.

```yaml
language: bilingual
original_language: en
translation_language: ko
```

본문의 주요 설명 섹션은 다음 형식을 사용합니다.

```markdown
**원본**

Original text

**한국어 번역**

한글 번역
```

오프라인 fallback은 내장 번역 사전을 사용하고, 모델 기반 service blueprint 생성 시에는 프롬프트에서 같은 병기 형식을 요구합니다.

기존 generated report note에 같은 형식을 붙이려면 먼저 읽기 전용 preview를 실행합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault upgrade-bilingual
```

검토 후 적용하려면 `--apply`를 명시합니다. 이 명령은 기존 본문을 바꾸지 않고 `language`, `original_language`, `translation_language`, `translation_mode` frontmatter와 `## Korean Translation Draft` 부록만 추가합니다. `status: reviewed` 또는 `status: evergreen` 노트는 기본적으로 건너뜁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault upgrade-bilingual --apply
```

OpenAI/Gemini key가 없어도 실패하지 않고 내장 번역 사전(`translation_mode: dictionary`)을 사용합니다.

내장 번역 사전이 보강된 뒤 이미 붙은 번역 부록만 다시 생성하려면 `--refresh-translation`을 사용합니다. 이 옵션은 기존 `## Korean Translation Draft`가 있는 note만 대상으로 삼습니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault upgrade-bilingual --refresh-translation --apply
```

적용 후 품질 점검은 읽기 전용 `bilingual-audit` 명령으로 수행합니다. 이 명령은 bilingual frontmatter, 한글 번역 블록, 번역 검토 마커, 현재 내장 사전과의 refresh 필요 여부를 확인합니다. 단, `Translation mode:`가 `dictionary`가 아닌 수동 보정 모드인 부록은 내장 사전 refresh 경고 대상에서 제외합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault bilingual-audit
```

점검 결과를 Obsidian에 이력으로 남기려면 `--write-note`를 추가합니다. 결과 note는 `60_Runs` 아래에 생성됩니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault bilingual-audit --write-note
```

## Citation Metadata Normalization

paper collector와 source writer는 citation metadata를 정규화합니다.

- DOI는 `https://doi.org/{doi}` 형태의 canonical URL로 통일합니다.
- arXiv URL은 `https://arxiv.org/abs/{id}` 형태로 통일합니다.
- source deduplication은 URL/title보다 DOI, arXiv ID를 우선합니다.
- source note frontmatter에는 `source_provider`, `canonical_url`, `doi`, `arxiv_id`, `source_score`가 기록됩니다.
- paper collector 실패나 알 수 없는 paper source 설정은 `run-log`의 `## Warnings`에 기록하고 source note를 만들지 않습니다.

## Service Blueprint Stabilization

모델이 생성한 blueprint Markdown은 저장 전에 필수 섹션을 검사합니다. 빠진 섹션은 검토용 placeholder와 함께 자동 보강됩니다.

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

## Obsidian Topic Map

각 research run은 `20_Taxonomy` 아래에 topic-map note를 생성합니다. 이 노트는 source notes, evidence ledger, service blueprint를 Obsidian wikilink로 연결하고, 추출된 evidence category를 기반으로 taxonomy 승격 후보를 보여줍니다.

## Vault Index

기존 vault를 스캔해서 note type, status, topic cluster, orphan note, 오래된 generated note, backlink suggestion을 `20_Taxonomy` 아래 vault-index note로 남길 수 있습니다. 추천은 exact topic 일치뿐 아니라 frontmatter의 `tags`, `aliases`, 제목/토픽의 핵심 토큰 겹침도 사용합니다. `ai`, `system`처럼 너무 일반적인 IT 토큰과 날짜 숫자만 겹치는 추천은 제외합니다. score 3 이상은 실행 가능한 backlink suggestion으로, score 1-2는 proposal/health queue에서 제외되는 low-priority signal로 분리합니다. orphan은 실제 review 대상, status가 있는 수동 review 대상, generated history, 무상태 수동 reference orphan으로 나뉩니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault index-vault
```

쓰기 전에 결과를 확인하려면 `--dry-run`을 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault index-vault --dry-run --max-suggestions 20
```

## Backlink Proposal Workflow

`backlink-proposals`는 vault index의 추천을 사람이 검토 가능한 작업 큐로 바꿉니다. 기본 실행은 `60_Runs` 아래 proposal note만 만들고 기존 노트는 수정하지 않습니다. 새 proposal note에는 기본적으로 `proposal_state: proposed`가 기록됩니다. 포털 재실행 결과처럼 `run-log`와 `topic-map`에 같은 `rerun_of`가 기록된 경우에는 실패 원본 job 이력을 따라 두 노트를 연결하는 고신뢰 lineage backlink 후보도 함께 제안합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-proposals
```

쓰기 전 preview는 다음처럼 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-proposals --dry-run --min-score 3
```

원본 노트에 검토용 체크리스트를 붙이려면 명시적으로 `--apply`를 사용합니다. `status: reviewed` 또는 `status: evergreen`인 노트는 기본적으로 건드리지 않고 proposal note의 skipped section에 남깁니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-proposals --apply
```

`--apply`로 생성된 결과 note에는 `proposal_state: applied`와 `applied_at`이 기록됩니다.

리뷰 완료 노트에도 체크리스트를 붙여야 하는 경우에만 `--include-reviewed`를 함께 사용합니다.

이전 `proposal_state: proposed` note를 새 note 생성 후 supersede하려면 명시적으로 `--supersede-previous`를 사용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-proposals --supersede-previous
```

붙인 체크리스트의 처리 상태는 `review-backlinks`로 다시 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault review-backlinks
```

이 명령은 기존 노트를 수정하지 않고, `- [ ]`, `- [x]`, 그리고 proposal section 바깥에 이미 wikilink가 들어가 해결된 항목을 분리해서 보여줍니다.

사람이 승인한 항목은 체크박스를 `- [x]`로 바꾼 뒤 `apply-reviewed-backlinks`로 `## Related Notes`에 반영합니다. 기본 동작은 체크된 항목만 처리하며, pending 항목은 그대로 둡니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-reviewed-backlinks --dry-run
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-reviewed-backlinks
```

proposal note가 여러 개 쌓이면 `backlink-history`로 최신 note와 추론된 상태를 확인합니다. 기존 note에 `proposal_state`가 없으면 요약값과 최신 파일 기준으로 `proposed`, `applied`, `superseded`, `empty` 상태를 추론합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-history
```

추론된 상태를 frontmatter에 명시하려면 먼저 dry-run으로 변경 예정 파일을 확인합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-history --write-state --dry-run
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault backlink-history --write-state
```

## Low-Priority Backlink Review Workflow

`low-priority-backlink-proposals`는 score가 actionable threshold보다 낮은 backlink signal을 검토 큐로 모읍니다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 proposal note를 저장합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault low-priority-backlink-proposals
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault low-priority-backlink-proposals --write-note
```

proposal note에서 숨겨도 되는 신호를 `Ignore`로 체크한 뒤 적용하면, 이후 `index-vault`의 low-priority signal 목록에서 제외됩니다. 원본 노트의 wikilink는 수정하지 않고 proposal note의 적용 상태만 기록합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-low-priority-backlinks --dry-run
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-low-priority-backlinks
```

## Next Actions

`next-actions`는 vault health, backlink checklist, manual orphan review, low-priority backlink review, run cleanup, promotion, stale note 상태를 모아 다음에 할 일만 우선순위와 명령어로 보여줍니다. 이미 proposal note가 있으면 새 note 생성을 다시 권하지 않고, 기존 note를 체크한 뒤 apply dry-run을 실행하도록 안내합니다. run cleanup proposal note도 queue로 인식해 중복 생성 대신 기존 checklist 검토를 추천합니다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 snapshot note를 저장합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault next-actions
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault next-actions --write-note
```

Research Agent 웹포털은 `/next-actions`의 상위 항목을 `후속 작업` 패널에 한글로 보여줍니다. 재실행 live run에서 생성된 `rerun_of` lineage backlink 후보도 같은 패널과 `backlink-proposals` workflow에 나타납니다.

임시 Vault에서 포털 API live offline run, `Run Lineage` 산출물, backlink proposal, checked apply까지 한 번에 검증하려면 다음 smoke를 사용합니다. 기본값은 임시 Vault를 정리하므로 실제 Vault를 건드리지 않습니다.

```bash
python3 scripts/rerun_lineage_smoke.py
python3 scripts/check.py --include-rerun-lineage-smoke
```

## Manual Orphan Review Workflow

`manual-orphan-proposals`는 status metadata가 있지만 incoming/outgoing wikilink가 없는 수동 노트를 검토 대상으로 모읍니다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 proposal note를 저장합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault manual-orphan-proposals
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault manual-orphan-proposals --write-note
```

proposal note에서 각 항목마다 `Ignore`, `Archive`, `Link to [[TARGET_NOTE]]` 중 하나만 체크합니다. link action은 `TARGET_NOTE`를 실제 Obsidian wikilink로 바꾼 뒤 적용합니다.

```bash
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-manual-orphan-review --dry-run
PYTHONPATH=src python3 -m research_agent --vault /path/to/ObsidianVault apply-manual-orphan-review
```

## 테스트

```bash
python3 -m unittest discover -s tests
```

## 파일 안내

- [docs/architecture.md](docs/architecture.md): 전체 아키텍처와 컴포넌트 경계
- [docs/openai-mvp.md](docs/openai-mvp.md): OpenAI API 중심 MVP 설계
- [docs/obsidian-vault.md](docs/obsidian-vault.md): vault 구조와 note contract
- [docs/roadmap.md](docs/roadmap.md): 구현 단계
- [config/research-agent.example.yaml](config/research-agent.example.yaml): 설정 예시
- [templates/source-note.md](templates/source-note.md): 출처 노트 템플릿
- [templates/evidence-ledger.md](templates/evidence-ledger.md): 근거 장부 템플릿
- [templates/service-blueprint.md](templates/service-blueprint.md): 실서비스 기본형 템플릿

## 공식 문서 기준 참고

- OpenAI Agents SDK: https://developers.openai.com/api/docs/guides/agents
- OpenAI tools: https://developers.openai.com/api/docs/guides/tools
- OpenAI models: https://developers.openai.com/api/docs/models
- Gemini API keys: https://ai.google.dev/gemini-api/docs/api-key
- Gemini API reference: https://ai.google.dev/api
- Gemini structured outputs: https://ai.google.dev/gemini-api/docs/structured-output
- Gemini Google Search grounding: https://ai.google.dev/gemini-api/docs/google-search
- Obsidian data storage: https://obsidian.md/help/data-storage
- Obsidian URI: https://obsidian.md/help/uri
