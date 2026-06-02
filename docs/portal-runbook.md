# Portal Operations Runbook

이 문서는 Research Agent Portal API와 `AI Agent Archtecture` PM Portal을 로컬 또는 공유 환경에서 실행, 검증, 진단하는 운영 절차다. API 계약의 상세 설명은 [portal-integration.md](portal-integration.md)를 기준으로 보고, 실제 운영 순서는 이 Runbook을 따른다.

## 운영 모드

| Mode | 목적 | 기본 명령 |
| --- | --- | --- |
| Quick check | 개발 중 핵심 회귀만 빠르게 확인 | `python3 scripts/check.py --quick` |
| CI check | CI에서 포탈/외부 Vault 없이 전체 단위 테스트 확인 | `python3 scripts/check.py --ci` |
| Full local check | 전체 단위 테스트 확인 | `python3 scripts/check.py` |
| UI static smoke | 이미 떠 있는 Research/PM Portal의 HTML/JS 표면 확인 | `python3 scripts/portal_ui_smoke.py` |
| Portal smoke | PM Portal -> Research Agent API dry-run 확인 | `python3 scripts/check.py --include-portal-smoke --portal-smoke-auto-port` |
| Bearer smoke | 인증 헤더와 upstream bearer 전달까지 확인 | `python3 scripts/check.py --include-portal-smoke --portal-smoke-auth bearer --portal-smoke-auto-port` |
| Job store cleanup | 오래된 terminal portal job 정리 preview/apply | `python3 -m research_agent --vault ... portal-job-cleanup` |
| Manual portal run | 브라우저에서 직접 실행/상태 확인 | Research Agent API와 PM Portal을 각각 기동 |

## 사전 점검

1. Python 3.11 이상을 사용한다.
2. Research Agent 경로가 `/Users/minsungkim/Documents/Research Agent`인지 확인한다.
3. 실제 Vault는 `/Users/minsungkim/Documents/Obsidian Vault`를 사용한다.
4. PM Portal 연동 검증에는 `/Users/minsungkim/Documents/AI Agent Archtecture`가 필요하다.
5. 공유 환경에서는 token을 명령행에 쓰지 않고 환경변수로 둔다.

```bash
cd "/Users/minsungkim/Documents/Research Agent"
python3 --version
python3 scripts/check.py --quick
```

## 검증 Profile

목적에 따라 다음 profile을 골라 실행한다.

| Profile | 포함 범위 | 사용 시점 |
| --- | --- | --- |
| `--quick` | 핵심 단위 테스트 subset | 작은 코드 변경 중 빠른 피드백 |
| `--ci` | 전체 단위 테스트, portal smoke 제외 | CI 또는 외부 서비스가 없는 자동 검증 |
| 기본 `scripts/check.py` | 전체 단위 테스트 | 로컬 개발 완료 전 |
| `--include-portal-smoke` | 전체 단위 테스트 + PM Portal dry-run E2E | 포탈 연동 변경 후 |

CI profile은 `--quick`, `--include-portal-smoke`와 함께 쓰지 않는다. 포탈 서버 기동, 로컬 포트 bind, 실제 Vault job store 검증이 필요한 작업은 명시적으로 portal smoke profile에서만 실행한다.

## Research Agent API 기동

개발용 기본 실행:

```bash
cd "/Users/minsungkim/Documents/Research Agent"

PYTHONPATH=src python3 -m research_agent \
  --vault "/Users/minsungkim/Documents/Obsidian Vault" \
  serve-portal-api \
  --host 127.0.0.1 \
  --port 8780
```

공유 환경용 bearer 실행:

```bash
export RESEARCH_AGENT_PORTAL_TOKEN="replace-me"

PYTHONPATH=src python3 -m research_agent \
  --vault "/Users/minsungkim/Documents/Obsidian Vault" \
  serve-portal-api \
  --host 127.0.0.1 \
  --port 8780 \
  --auth bearer
```

상태 확인:

```bash
curl -sS http://127.0.0.1:8780/health
curl -sS http://127.0.0.1:8780/doctor
curl -sS http://127.0.0.1:8780/vault-health
curl -sS "http://127.0.0.1:8780/job-store-health?retention_days=90&retention_limit=200"
```

Bearer 모드에서 `/health`는 공개 probe로 남고, 나머지 route에는 header가 필요하다.

```bash
curl -sS http://127.0.0.1:8780/jobs \
  -H "Authorization: Bearer $RESEARCH_AGENT_PORTAL_TOKEN"
```

## PM Portal 연결

PM Portal을 Research Agent runtime으로 연결한다.

```bash
cd "/Users/minsungkim/Documents/AI Agent Archtecture"

PYTHONPATH=src python3 -m supervisor_graph_hybrid \
  --serve-pm-portal \
  --pm-portal-host 127.0.0.1 \
  --pm-portal-port 8770 \
  --pm-portal-runtime-url http://127.0.0.1:8780
```

Research Agent API가 bearer 모드라면 PM Portal upstream token도 같은 값으로 둔다.

```bash
export PM_PORTAL_TOKEN="replace-me"
export RUNTIME_API_TOKEN="$RESEARCH_AGENT_PORTAL_TOKEN"

PYTHONPATH=src python3 -m supervisor_graph_hybrid \
  --serve-pm-portal \
  --pm-portal-host 127.0.0.1 \
  --pm-portal-port 8770 \
  --pm-portal-auth bearer \
  --pm-portal-runtime-url http://127.0.0.1:8780 \
  --pm-portal-runtime-token-env RUNTIME_API_TOKEN
```

PM Portal 자체가 bearer 모드이면 브라우저 UI의 token 입력 또는 API 요청 header가 필요하다.

```bash
curl -sS http://127.0.0.1:8770/api/runs \
  -H "Authorization: Bearer $PM_PORTAL_TOKEN"
```

## Smoke 검증

가장 안전한 운영 검증은 dry-run smoke다. 이 검증은 실제 Vault 산출물을 쓰지 않고 planned artifact만 확인한다.

```bash
python3 scripts/portal_e2e_smoke.py --auto-port
```

인증 경로까지 확인한다.

```bash
python3 scripts/portal_e2e_smoke.py --auth bearer --auto-port
```

전체 단위 테스트와 함께 확인한다.

```bash
python3 scripts/check.py --include-portal-smoke --portal-smoke-auth bearer --portal-smoke-auto-port
```

성공 기준:

- `OK portal E2E smoke completed`
- `planned_artifacts`가 1개 이상
- `vault_writes: none`
- `pm_presets: ok`
- bearer smoke에서는 `auth: bearer`
- 실행 후 8770/8780 또는 자동 선택 포트에 listener가 남지 않음

## UI 검증

서버가 이미 떠 있다면 HTML/JS 표면만 빠르게 확인한다.

```bash
python3 scripts/portal_ui_smoke.py \
  --research-url http://127.0.0.1:8780 \
  --pm-url http://127.0.0.1:8770
```

Bearer 모드에서는 토큰을 환경변수로 둔다.

```bash
export RESEARCH_AGENT_PORTAL_TOKEN="replace-me"
export PM_PORTAL_TOKEN="replace-me"

python3 scripts/portal_ui_smoke.py
```

성공 기준:

- `Research Portal UI: ok`
- `PM Portal UI: ok`
- Research Portal 기본 UI가 한글이며 run form, provider/dry-run/offline 입력, job/result 영역이 있음
- Research Portal에 job store cleanup preview 상태가 있음
- Research Portal에 `/next-actions` 기반 `후속 작업` 리스트가 있음
- PM Portal에 리서치 드라이런/실사용 preset과 runtime option 입력이 있음
- PM Portal의 `리서치 실행` 섹션에 최근 job 필터, 실패 job 재실행, `rerun_of` 이력, 작업 저장소 상태가 표시됨
- live 재실행의 Obsidian run log와 topic map에 `Run Lineage` 섹션과 `rerun_of` frontmatter가 있음
- 같은 `rerun_of`를 가진 run log/topic map이 score 6 lineage backlink 후보로 잡힘
- `/assets/portal.js`에 submit/polling/preset handler가 있음

브라우저 수동 확인이 필요할 때는 다음을 본다.

- Research Agent UI: `http://127.0.0.1:8780/`
- PM Portal UI: `http://127.0.0.1:8770/`
- 개발자 콘솔에 `warn`/`error`가 없는지 확인
- dry-run 실행 후 Vault에 planned artifact가 실제 생성되지 않았는지 확인

## Dry-run 수동 실행

Research Agent API에 직접 dry-run job을 등록한다.

```bash
curl -sS -X POST http://127.0.0.1:8780/runs \
  -H "Content-Type: application/json" \
  -d '{"topic":"agentic RAG 구조 분류","provider":"gemini","offline":true,"dry_run":true,"max_papers_per_source":1}'
```

응답의 `status_url`로 polling한다.

```bash
curl -sS http://127.0.0.1:8780/jobs/<job_id>
```

PM Portal 경유 dry-run은 `/api/runs`로 보낸다.

```bash
curl -sS -X POST http://127.0.0.1:8770/api/runs \
  -H "Content-Type: application/json" \
  -d '{"objective":"Research Agent PM Portal dry-run","provider":"gemini","offline":true,"dry_run":true,"max_papers_per_source":1}'
```

## Live 실행 전 체크리스트

실제 Vault 쓰기 실행 전에는 다음을 확인한다.

- `python3 scripts/check.py --quick` 통과
- `python3 scripts/rerun_lineage_smoke.py` 통과
- `python3 scripts/portal_e2e_smoke.py --auth bearer --auto-port` 통과
- `/doctor`에서 provider key 상태 확인
- `/vault-health`가 `OK` 또는 의도한 `WARN` 상태
- `dry_run: true` 결과의 planned artifact 경로가 기대와 일치
- reviewed/evergreen note 보호 정책을 우회하지 않음
- 공유 환경에서는 bearer token을 켬
- `--max-workers 1` 유지

Live run은 `dry_run`을 빼거나 `false`로 둔다.

```bash
curl -sS -X POST http://127.0.0.1:8780/runs \
  -H "Content-Type: application/json" \
  -d '{"topic":"OpenAI Agents SDK와 LangGraph 비교","provider":"gemini","offline":false,"dry_run":false,"max_papers_per_source":2}'
```

## 장애 진단

| 증상 | 확인할 것 | 조치 |
| --- | --- | --- |
| `Address already in use` | 8770/8780 listener | smoke는 `--auto-port`, 수동 실행은 다른 `--port` 사용 |
| `PermissionError: Operation not permitted` | 로컬 포트 bind 권한 | Codex/샌드박스 환경이면 권한 승인 경로로 실행 |
| `401 unauthorized` | bearer header/token env | `Authorization: Bearer ...`, `RESEARCH_AGENT_PORTAL_TOKEN`, `PM_PORTAL_TOKEN`, `RUNTIME_API_TOKEN` 확인 |
| `/doctor` provider unavailable | API key env | `.env`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` 확인 |
| job이 `failed` | `/jobs/{job_id}` error | error message와 run summary 확인 후 `dry_run`으로 재현 |
| smoke가 timeout | 서버 조기 종료 출력 | smoke script가 `[research-agent-api output]` 또는 `[pm-portal output]`을 출력하는지 확인 |
| dry-run인데 파일이 생김 | planned artifact 경로 | smoke는 실패해야 정상이다. 해당 파일을 만든 별도 live run 여부 확인 |
| PM Portal에서 job 조회 실패 | runtime URL/token | `--pm-portal-runtime-url`, `RUNTIME_API_TOKEN` 확인 |
| Python import error | Python 버전/PYTHONPATH | Python 3.11 이상, `PYTHONPATH=src` 확인 |

포트 확인:

```bash
lsof -nP -iTCP:8770 -sTCP:LISTEN
lsof -nP -iTCP:8780 -sTCP:LISTEN
```

## 종료 절차

수동으로 띄운 서버는 터미널에서 `Ctrl-C`로 종료한다. 남은 listener가 있으면 PID를 확인하고 종료한다.

```bash
lsof -nP -iTCP:8770 -sTCP:LISTEN
lsof -nP -iTCP:8780 -sTCP:LISTEN
```

Smoke script와 `scripts/check.py --include-portal-smoke`는 정상 종료 시 서버를 자동으로 내린다.

## Job Store 보존 정책

Research Agent API의 기본 job store는 Vault의 `60_Runs/research_portal_jobs.json`이다. 이 파일은 포탈 운영 이력과 최근 job 상태를 담는 감사 로그 성격이 있으므로 API가 실행 중일 때 직접 편집하지 않는다.

Smoke script는 기본적으로 `/private/tmp/research-agent-e2e-*.json` job store를 만들고, 성공/실패와 관계없이 종료 시 삭제한다. 장애 재현을 위해 보존해야 할 때만 `scripts/portal_e2e_smoke.py --keep-job-store --job-store-path /private/tmp/...`를 사용한다.

수동 UI 검증용 임시 job store를 쓰는 경우에도 `/private/tmp` 아래에 두고 검증 후 삭제한다. 실제 Vault job store 정리는 Obsidian run log나 별도 archive로 필요한 이력이 남아 있는지 확인한 뒤 진행한다.

정리 대상은 `completed`, `failed`, `cancelled`, `interrupted` 같은 terminal job만이다. `queued`, `running` job은 retention cleanup에서 삭제하지 않는다.

기본 cleanup 명령은 dry-run preview다.

```bash
PYTHONPATH=src python3 -m research_agent \
  --vault "/Users/minsungkim/Documents/Obsidian Vault" \
  portal-job-cleanup \
  --retention-days 90 \
  --retention-limit 200
```

preview 결과가 맞으면 `--apply`를 붙인다.

```bash
PYTHONPATH=src python3 -m research_agent \
  --vault "/Users/minsungkim/Documents/Obsidian Vault" \
  portal-job-cleanup \
  --retention-days 90 \
  --retention-limit 200 \
  --apply
```

API 서버에서 자동 정리를 켜려면 `serve-portal-api`에 `--job-retention-days` 또는 `--job-retention-limit`를 명시한다. 둘 다 기본값은 `0`이라 기존 운영 이력을 자동 삭제하지 않는다.

웹포탈과 API에서 현재 cleanup preview를 확인하려면 `/job-store-health`를 호출한다. 이 endpoint는 읽기 전용이며 JSON store를 갱신하지 않는다.

```bash
curl -sS "http://127.0.0.1:8780/job-store-health?retention_days=90&retention_limit=200&max_removed=10"
```

PM Portal에서는 같은 정보를 BFF 프록시 경로로 확인한다.

```bash
curl -sS "http://127.0.0.1:8770/api/job-store-health?retention_days=90&retention_limit=200&max_removed=10"
```

## 운영 메모

- job store 기본값은 Vault의 `60_Runs/research_portal_jobs.json`이다.
- smoke script는 `/private/tmp/research-agent-e2e-*.json` job store를 사용하고 기본적으로 삭제한다.
- portal job cleanup은 terminal job만 prune하며, 기본 실행은 dry-run이다.
- `offline: true`는 network collector와 LLM 호출 없이 deterministic fallback을 사용한다.
- `dry_run: true`는 Obsidian 산출물을 쓰지 않는다.
- PM Portal은 `objective`, Research Agent web portal은 `topic`을 보내지만 Research Agent API는 둘 다 받는다.
