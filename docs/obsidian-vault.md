# Obsidian Vault Design

## 권장 폴더

```text
Research/
  00_Inbox/
  10_Sources/
    official-docs/
    standards/
    papers/
    web/
  20_Taxonomy/
  30_Service-Blueprints/
  40_Comparisons/
  50_Evidence-Ledger/
  60_Runs/
  90_Templates/
  99_Archive/
```

## Note contract

모든 agent-generated note는 YAML frontmatter를 가진다.

```yaml
---
type: service-blueprint
topic: agentic-rag
created_at: 2026-05-31
checked_at: 2026-05-31
status: draft
source_priority:
  - official-docs
  - standards
  - papers
confidence: medium
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---
```

agent-generated report note는 원본과 한글 번역을 함께 표기한다. 설명형 섹션은 `**원본**`과 `**한국어 번역**` 블록을 병기하고, evidence ledger는 claim 번역 테이블을 추가한다.

기존 generated report note는 `upgrade-bilingual` 명령으로 안전하게 보강한다. 기본 실행은 dry-run이며, `--apply`를 명시한 경우에만 frontmatter와 `## Korean Translation Draft` 부록을 append한다. API key가 없으면 내장 번역 사전(`translation_mode: dictionary`)을 사용한다. 내장 사전을 보강한 뒤에는 `--refresh-translation`으로 기존 번역 부록만 재생성할 수 있다. 적용 후에는 `bilingual-audit` 명령으로 frontmatter, 번역 블록, 품질 마커, refresh 필요 여부를 읽기 전용으로 점검한다. `--write-note`를 명시하면 audit 결과를 `60_Runs` 아래 `bilingual-audit` note로 저장한다.

## Note types

### source-note

원문 하나에 대한 요약이다. 원문 URL, 발행일, 업데이트일, 핵심 주장, 인용 가능한 근거를 담는다.

각 source note는 evidence ledger와 연결하기 위해 `source_id`를 가진다. 예를 들어 `S001` source note의 claim은 evidence ledger에서 `source_id: S001`로 추적된다.

source note frontmatter에는 collector와 citation identity도 남긴다.

```yaml
source_provider: crossref
canonical_url: https://doi.org/10.xxxx/example
doi: 10.xxxx/example
arxiv_id:
source_score: 0.95
```

`source-audit` 명령은 generated source note의 품질을 읽기 전용으로 점검한다. `source_id`, `source_type`, `source_url`/`canonical_url` 누락은 실패로 보고, official docs가 seed domain에 머문 경우, paper source의 DOI/arXiv 식별자가 없는 경우, structured evidence claim 연결이 없는 경우는 경고로 보고한다. 전체 vault audit에서는 evidence ledger와 service blueprint에 source note 기준으로 동기화되지 않은 stale URL/title/claim 참조가 남아 있는지도 경고한다. `--write-note`를 명시하면 `60_Runs` 아래 `type: source-audit` note를 저장해 refresh 전후 품질 이력을 남긴다.

`official-docs-refresh` 명령은 seed domain에 머문 official docs source note를 찾아 OpenAI/Gemini official docs collector로 exact URL 후보를 만든다. 기본 실행은 원본 source note를 수정하지 않고 후보만 출력한다. `--write-note`를 명시하면 `60_Runs` 아래 `type: official-docs-refresh` proposal note를 저장한다.

`apply-official-docs-refresh` 명령은 사람이 proposal note에서 `- [x]`로 승인한 exact URL 후보만 source note에 반영한다. `--dry-run`으로 먼저 적용 예정 항목을 확인할 수 있다. 적용 시 source note frontmatter의 `source_url`, `canonical_url`, `source_provider`, `source_score`와 본문의 URL 메타데이터를 갱신하고 proposal note를 `proposal_state: applied`로 표시한다.

`standards-refresh` 명령은 seed domain에 머문 standards source note를 찾아 OpenAI/Gemini standards collector로 exact 표준, 거버넌스, 리스크, 컴플라이언스, 보안 프레임워크 URL 후보를 만든다. 기본 실행은 원본 source note를 수정하지 않고 후보만 출력한다. `--write-note`를 명시하면 `60_Runs` 아래 `type: standards-refresh` proposal note를 저장한다.

`apply-standards-refresh` 명령은 사람이 proposal note에서 `- [x]`로 승인한 exact URL 후보만 standards source note에 반영한다. `--dry-run`으로 먼저 적용 예정 항목을 확인할 수 있다. 적용 시 source note frontmatter의 `source_url`, `canonical_url`, `source_provider`, `source_score`와 본문의 URL 메타데이터를 갱신하고 proposal note를 `proposal_state: applied`로 표시한다.

`sync-source-references` 명령은 source note의 최신 URL, title, claim을 기준으로 evidence ledger row와 service blueprint의 Markdown source link를 동기화한다. 기본 실행은 dry-run이며, `--apply`를 명시한 경우에만 evidence ledger와 service blueprint를 수정한다.

`paper-refresh` 명령은 기존 vault topic 또는 명시한 topic 기준으로 arXiv, Semantic Scholar, Crossref, OpenAlex paper metadata 후보를 수집한다. `--write-note`를 명시하면 `60_Runs` 아래 `type: paper-refresh` proposal note와 후보 JSON metadata를 저장한다. `apply-paper-refresh` 명령은 사람이 `- [x]`로 승인한 후보만 `10_Sources/papers` 아래 source note로 생성하고, DOI/arXiv/canonical URL 기준으로 중복 paper source를 건너뛴다.

`paper-claim-refresh` 명령은 `Crossref metadata record.`처럼 metadata-only claim으로 남은 paper source note를 찾아, DOI 기반 Semantic Scholar, OpenAlex, Crossref 세부 메타데이터를 우선 조회하고 실패하면 로컬 DOI/arXiv/저자/연도/제목 메타데이터를 바탕으로 더 구체적인 claim 보강 proposal을 만든다. `--no-network`를 명시하면 로컬 source note 메타데이터만 사용한다. `apply-paper-claim-refresh` 명령은 사람이 `- [x]`로 승인한 후보만 source note의 `## Core Summary`, `## Important Claims`, `## Citable Evidence`를 원본/한글 번역 병기 형식으로 갱신하고 refresh history를 남긴다.

`paper-downstream-proposals` 명령은 paper source note의 claim이 같은 topic의 evidence ledger, service blueprint, topic map에 아직 반영되지 않았는지 확인하고, 사람이 검토할 수 있는 `type: paper-downstream-proposals` note를 만든다. `apply-paper-downstream` 명령은 사람이 `- [x]`로 승인한 후보만 evidence ledger row, service blueprint `## Evidence`, topic map `## Source Notes`와 `## Claim Index`에 추가한다. `--dry-run`으로 먼저 업데이트될 note를 확인할 수 있다.

`blueprint-refresh` 명령은 같은 topic의 evidence ledger를 읽어 service blueprint의 결론, 사용 조건, 구조 분류, baseline, 구현 순서, 위험, 검증, 불확실성 섹션 보강 proposal을 만든다. 적용 여부는 evidence ledger fingerprint로 추적하므로 같은 근거 상태에 대해 반복 proposal을 만들지 않는다. `apply-blueprint-refresh` 명령은 사람이 `- [x]`로 승인한 후보만 service blueprint 본문 섹션을 원본/한글 번역 병기 형태로 갱신한다.

`verification-cleanup` 명령은 generated evidence ledger와 service blueprint에 남은 stale 검증 문구를 찾는다. 예를 들어 논문 source가 downstream에 연결된 뒤에도 "No paper sources were collected"나 "paper metadata still needs review" 문구가 남아 있으면 `type: verification-cleanup` proposal note로 기록한다. `apply-verification-cleanup` 명령은 사람이 `- [x]`로 승인한 후보만 현재 상태에 맞는 원본/한글 검증 문구로 교체하고 `verification_cleaned_at` frontmatter를 남긴다.

`review-promotion-proposals` 명령은 generated draft note 중 source audit, bilingual audit, 구조 체크를 통과한 source note, evidence ledger, service blueprint, topic map을 `reviewed` 승격 후보로 제안한다. audit issue, stale 검증 문구, placeholder가 남은 노트는 skipped 사유로 남긴다. `apply-review-promotion` 명령은 사람이 `- [x]`로 승인한 후보만 `status: reviewed`, `reviewed_by`, `reviewed_at`, `review_basis` frontmatter로 갱신한다.

### evidence-ledger

여러 source에서 추출한 claim 단위 근거 장부다. 종합 노트가 어떤 근거 위에 서 있는지 추적한다.

### taxonomy

개념 구조 분류다. framework, architecture, method, evaluation, security, production readiness 같은 축으로 분류한다.

Research run마다 `topic-map` note도 이 영역에 생성된다. topic-map은 source note, evidence ledger, service blueprint를 Obsidian wikilink로 연결하고 taxonomy 승격 후보 category를 보여준다.

`index-vault` 명령은 이 영역에 `vault-index` note를 생성한다. vault-index는 note type/status 집계, topic cluster, orphan note, stale generated note, backlink suggestion을 담는다. backlink suggestion은 topic, tags, aliases, 제목 token overlap을 함께 사용하되, `ai`, `system`처럼 너무 일반적인 IT 토큰만 겹치는 후보는 제외한다. score 3 이상은 실행 가능한 backlink suggestion으로, score 1-2는 proposal/health queue에서 제외되는 low-priority signal로 분리한다. orphan note는 실제 review 대상, status가 있는 수동 review 대상, generated history, 무상태 수동 reference orphan으로 분리한다.

`backlink-proposals` 명령은 vault index의 backlink suggestion을 `60_Runs` 아래 proposal note로 변환한다. 기본 모드는 기존 노트를 수정하지 않는다. 새 proposal note에는 `proposal_state: proposed`를 기록한다. 포털 재실행 결과처럼 `run-log`와 `topic-map`에 같은 `rerun_of`가 있으면 실패 원본 job lineage를 따라 두 노트를 연결하는 score 6 backlink 후보를 만든다. `--apply`를 명시하면 각 source note 하단에 `## Backlink Proposals` 체크리스트를 append하고 결과 note에는 `proposal_state: applied`와 `applied_at`을 기록한다. `status: reviewed` 또는 `status: evergreen`인 노트는 기본적으로 skip하며, 사용자가 `--include-reviewed`를 명시한 경우에만 append한다. `--supersede-previous`를 명시한 경우에만 기존 `proposal_state: proposed` note를 `superseded`로 갱신한다.

`review-backlinks` 명령은 append된 backlink checklist를 읽기 전용으로 스캔한다. 아직 남은 `- [ ]` 항목, 체크된 `- [x]` 항목, proposal section 바깥에 이미 wikilink가 들어가 해결된 항목을 분리해서 보여준다.

`apply-reviewed-backlinks` 명령은 사람이 `- [x]`로 승인한 checklist 항목만 실제 `## Related Notes` wikilink로 반영한다. `- [ ]` 항목은 수정하지 않는다. `--dry-run`으로 먼저 반영 예정 항목과 업데이트될 note를 확인할 수 있다.

`backlink-history` 명령은 `backlink-proposals` note 이력을 읽기 전용으로 보여준다. 기존 note에 `proposal_state` frontmatter가 없으면 summary count와 최신 파일 기준으로 `proposed`, `applied`, `superseded`, `empty` 상태를 추론한다. `--write-state --dry-run`은 추론 상태를 frontmatter에 쓰기 전 변경 예정 파일만 보여주고, `--write-state`를 명시한 경우에만 `proposal_state`를 갱신한다.

`low-priority-backlink-proposals` 명령은 actionable threshold보다 낮은 backlink signal을 `Ignore` checklist로 제안한다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 `type: low-priority-backlink-review` proposal note를 저장한다. `apply-low-priority-backlinks` 명령은 사람이 체크한 ignore만 적용하고 proposal note를 `proposal_state: applied`로 표시한다. 적용된 low-priority pair는 이후 `index-vault`의 low-priority signal 목록에서 제외된다.

`run-cleanup-proposals` 명령은 `60_Runs` 아래 completed history를 archive 후보로 모은다. `proposal_state: applied` 또는 `superseded`인 proposal note, 최신 clean audit으로 대체된 과거 PASS audit note, 최신 snapshot으로 대체된 과거 `next-actions`/`vault-health` note가 기본 후보가 된다. `apply-run-cleanup` 명령은 사람이 `- [x]`로 승인한 후보만 `status: archived`, `archived_at`, `archive_reason` frontmatter로 갱신한다. 파일은 삭제하거나 이동하지 않는다.

`portal-job-cleanup` 명령은 Portal API JSON job store인 `60_Runs/research_portal_jobs.json`을 대상으로 오래된 terminal job을 prune한다. 기본 실행은 dry-run preview이며, `--apply`를 붙였을 때만 JSON store를 갱신한다. `queued`, `running` job은 정리 대상에서 제외하고, `serve-portal-api --job-retention-days ... --job-retention-limit ...` 옵션을 명시한 경우에만 서버 시작/저장 시 자동 retention을 적용한다. 실행 중인 포탈에서는 `/job-store-health` endpoint가 같은 retention 기준의 읽기 전용 preview를 제공한다.

`manual-orphan-proposals` 명령은 status metadata가 있지만 incoming/outgoing wikilink가 없는 수동 노트를 `Ignore`, `Archive`, `Link to [[TARGET_NOTE]]` action checklist로 제안한다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 `type: manual-orphan-review` proposal note를 저장한다. `apply-manual-orphan-review` 명령은 사람이 체크한 action만 반영한다. ignore는 `orphan_review: ignored`, archive는 `status: archived`, link는 `## Related Notes` wikilink와 `orphan_review: linked`를 남긴다.

`next-actions` 명령은 vault health, backlink checklist, manual orphan review, low-priority backlink review, run cleanup, promotion, stale note 상태를 합쳐 다음에 할 일만 우선순위와 명령어로 보여준다. 이미 proposal note가 있으면 새 note 생성을 다시 권하지 않고, 기존 note를 체크한 뒤 apply dry-run을 실행하도록 안내한다. run cleanup proposal note도 queue로 인식해 중복 생성 대신 기존 checklist 검토를 추천한다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 `type: next-actions` snapshot note를 저장한다.

`vault-health` 명령은 source audit, bilingual audit, reviewed core note, backlink 후보와 checklist, run cleanup 후보, stale generated note, review promotion 후보, verification cleanup 후보를 한 화면에 요약한다. 기본 실행은 읽기 전용이며, `--write-note`를 붙이면 `60_Runs` 아래 `type: vault-health` snapshot note를 저장한다.

`upgrade-bilingual` 명령은 과거에 생성된 source note, evidence ledger, service blueprint, topic map, run log 중 아직 bilingual 메타데이터나 한글 번역 부록이 없는 노트를 찾는다. 기존 본문은 보존하고 번역 초안 부록만 추가한다. `--refresh-translation`은 이미 `## Korean Translation Draft`가 있는 노트만 대상으로 기존 부록을 현재 내장 사전 기준으로 재생성한다. `status: reviewed` 또는 `status: evergreen` 노트는 기본적으로 skip하며, `--include-reviewed`를 명시한 경우에만 후보에 포함한다.

`bilingual-audit` 명령은 generated report note를 대상으로 bilingual 계약을 점검한다. `language: bilingual`, `translation_language: ko`, `**한국어 번역**` 블록 누락은 실패로 보고, `한국어 번역 검토 필요` 같은 품질 마커나 현재 내장 사전과 다른 `dictionary` 번역 부록은 경고로 보고한다. `Translation mode:`가 수동 보정 모드인 부록은 내장 사전 refresh 경고 대상에서 제외한다. `--write-note` 결과 note는 `type: bilingual-audit`로 저장되며, 감사 대상 report type에는 포함되지 않는다.

`run` 명령은 완료 직후 이번 run에서 생성한 source note, evidence ledger, service blueprint, topic map을 대상으로 bilingual audit을 실행하고, run-log의 `## Bilingual Audit` 섹션에 요약을 기록한다. run-log는 감사 요약을 포함해 한 번만 저장된다. 이 자동 요약은 기존 vault의 오래된 노트 상태와 분리해서 새 산출물의 한글 병기 계약을 확인하기 위한 것이다.

### service-blueprint

실서비스에 가장 유용한 기본형이다. "무엇을 만들 것인가"에 바로 연결되는 note다.

### comparison

프레임워크나 접근법 비교 노트다.

### run-log

한 번의 research run에 대한 실행 기록이다.

## 파일명 규칙

```text
YYYY-MM-DD_topic-note-type.md
```

예시:

```text
2026-05-31_agentic-rag-service-blueprint.md
2026-05-31_openai-agents-sdk-source.md
2026-05-31_agent-framework-comparison.md
```

## 덮어쓰기 정책

`status: reviewed` 또는 `status: evergreen`인 노트는 덮어쓰지 않는다.

`[quality_gates].block_vault_write_on_fail = true`로 설정하면 quality gate 실패 시 vault note 쓰기를 시작하지 않는다. 쓰기 도중 예외가 발생하면 `[pipeline].cleanup_partial_artifacts = true` 기본값에 따라 이번 run에서 생성된 partial artifact를 삭제한다.

새로운 조사 결과는 다음 중 하나로 처리한다.

- 기존 노트 하단에 append proposal 생성
- `YYYY-MM-DD_topic-update.md` 생성
- 원본 노트의 `related`에 update note 링크 추가

backlink workflow는 이 정책을 따른다. 기본 실행은 proposal note만 만들고, 명시적 적용 시에도 reviewed/evergreen 노트는 별도 승인 없이는 수정하지 않는다.

## Obsidian URI

Obsidian은 `obsidian://open`과 `obsidian://search` URI를 지원한다. MVP에서는 파일을 직접 쓰는 것으로 충분하고, UI에서 바로 열기 기능은 나중에 붙인다.
