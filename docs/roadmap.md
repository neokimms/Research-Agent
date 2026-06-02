# Roadmap

## Phase 0: 설계 고정

- Obsidian vault path 결정
- source priority 결정
- note templates 확정
- API key와 `.env` 정책 결정

## Phase 1: Local MVP

목표: 한 주제를 입력하면 Obsidian 초안 노트 묶음을 생성한다.

구현:

- CLI command
- doctor command
- optional OpenAI/Gemini API smoke check
- dry-run preview for planned Obsidian artifacts
- config loader
- OpenAI Responses wrapper
- Gemini generateContent wrapper
- `auto`, `openai`, `gemini` provider selection
- source collector interface
- structured JSON evidence extraction
- source note quality audit command
- Obsidian source audit note writer for source quality history
- official docs exact URL refresh proposal workflow
- checked official docs refresh proposal apply workflow
- standards exact URL refresh proposal workflow
- checked standards refresh proposal apply workflow
- source reference sync for evidence ledger and service blueprint
- paper metadata refresh proposal workflow
- checked paper refresh source-note creation workflow
- paper claim refresh proposal/apply workflow for metadata-only paper source notes
- paper downstream proposal/apply workflow for evidence ledger, service blueprint, and topic map
- blueprint refresh proposal/apply workflow from current evidence ledger
- verification cleanup proposal/apply workflow for stale evidence review text
- review promotion proposal/apply workflow for audit-clean generated draft notes
- run history cleanup proposal/apply workflow for completed proposal and superseded audit notes
- vault health summary command for audit, review, backlink, cleanup, and stale-note state
- quality gate report in evidence ledger and run-log
- service blueprint section stabilization
- bilingual original/Korean Obsidian report rendering
- existing generated note bilingual upgrade using dictionary fallback
- bilingual audit command and Obsidian audit note writer for generated report quality checks
- automatic per-run bilingual audit summary in run-log
- Obsidian topic-map and backlink suggestions
- Obsidian Markdown writer
- evidence ledger writer
- service blueprint writer

검증:

- 한 주제에 대해 source note, evidence ledger, service blueprint 생성
- Markdown frontmatter 유효성 검사
- reviewed note 덮어쓰기 방지

## Phase 2: Research Quality

목표: 공식 문서와 논문 중심으로 source quality를 끌어올린다.

구현:

- arXiv connector
- Crossref connector
- Semantic Scholar connector
- OpenAlex connector
- source deduplication by DOI/arXiv/canonical URL
- citation metadata normalization into source note frontmatter
- collector failure warnings in run-log instead of source notes

검증:

- DOI/arXiv ID 중복 제거
- 논문 메타데이터 정확도 샘플링
- source priority scoring

## Phase 3: Agent Runtime

목표: 긴 research run을 중단/재개하고, 위험한 write 전에 승인한다.

후보:

- OpenAI Agents SDK
- LangGraph

선택 기준:

- OpenAI 중심 tool orchestration과 tracing이 우선이면 Agents SDK
- durable execution, checkpoint, human-in-the-loop workflow가 우선이면 LangGraph

## Phase 4: Vault Intelligence

목표: 기존 Obsidian vault를 재검색하고, 새 조사 결과와 연결한다.

구현:

- vault indexer
- backlink suggestion
- backlink proposal/apply workflow
- backlink review queue
- apply reviewed backlink checklist
- backlink proposal history
- explicit backlink proposal state lifecycle
- existing generated report bilingual upgrade workflow
- stale note detector
- topic map generator
- LlamaIndex integration

현재 구현은 `index-vault` CLI로 note type/status 집계, topic cluster, orphan note, stale generated note, backlink suggestion을 생성한다. backlink suggestion은 exact topic, `tags`, `aliases`, title/topic token overlap을 함께 보고, 포털 재실행 결과의 `run-log`와 `topic-map`이 같은 `rerun_of`를 공유하면 실패 원본 job lineage backlink 후보를 score 6으로 만든다. orphan note는 실제 review 대상, status가 있는 수동 review 대상, generated history, 무상태 수동 reference orphan으로 분리하고, 날짜 숫자나 `ai`, `system` 같은 일반 IT 토큰만 겹치는 추천은 제외한다. score 3 이상은 실행 가능한 backlink suggestion으로, score 1-2는 proposal/health queue에서 제외되는 low-priority signal로 분리한다. `next-actions` CLI는 health, backlink, manual orphan, low-priority signal, run cleanup, promotion, stale note 상태를 다음 행동 목록으로 요약하고, 기존 proposal note가 있으면 중복 생성 대신 apply dry-run 경로를 안내한다. run cleanup queue도 읽어 기존 archive proposal note의 review/apply를 우선 추천한다. `run-cleanup-proposals` CLI는 완료된 proposal/audit history와 최신 `next-actions` 또는 `vault-health` snapshot에 의해 대체된 과거 snapshot을 archive 후보로 모은다. `low-priority-backlink-proposals` CLI는 낮은 점수 신호를 `Ignore` checklist로 만들고, `apply-low-priority-backlinks` CLI는 사람이 체크한 ignore만 proposal state로 기록해 이후 index에서 숨긴다. `manual-orphan-proposals` CLI는 수동 orphan review 후보를 `Ignore`, `Archive`, `Link` action checklist로 만들고, `apply-manual-orphan-review` CLI는 사람이 체크한 action만 frontmatter 또는 `## Related Notes`에 반영한다. `backlink-proposals` CLI는 추천을 proposal note로 만들고, 명시적 `--apply`에서만 source note 하단에 체크리스트를 append한다. 새 proposal note는 `proposal_state: proposed` 또는 `proposal_state: applied`를 기록한다. reviewed/evergreen 노트는 기본적으로 skip한다. `--supersede-previous`를 명시한 경우에만 기존 proposed note를 superseded로 갱신한다. `review-backlinks` CLI는 append된 checklist의 pending/completed/resolved 상태를 읽기 전용으로 요약한다. `apply-reviewed-backlinks` CLI는 사람이 체크한 `- [x]` 항목만 `## Related Notes`에 반영한다. `backlink-history` CLI는 proposal note 이력을 읽고 상태를 추론하며, `--write-state`로 추론 상태를 frontmatter에 기록할 수 있다. `upgrade-bilingual` CLI는 기존 generated report note를 dry-run으로 스캔하고, `--apply`에서만 한글 번역 초안 부록을 append한다. API key가 없으면 내장 번역 사전을 사용한다.

## Phase 5: UI

목표: CLI를 넘어 작은 local web UI 또는 Obsidian command integration을 제공한다.

구현:

- run dashboard
- review queue
- source explorer
- Obsidian URI open/search link
