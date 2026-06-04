from __future__ import annotations


def render_portal_guide_html() -> str:
    return """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>리서치 에이전트 포털 가이드</title>
  <link rel="stylesheet" href="/assets/portal.css">
</head>
<body>
  <header class="topbar">
    <div>
      <p class="eyebrow">사용 가이드</p>
      <h1>리서치 에이전트 포털 가이드</h1>
    </div>
    <div class="top-actions">
      <a class="nav-button" href="/">포털로 돌아가기</a>
      <a class="nav-button" href="#quick-start">빠른 시작</a>
      <a class="nav-button" href="#portal-map">화면 이해</a>
    </div>
  </header>

  <main class="guide-page">
    <section class="guide-hero">
      <p class="eyebrow">무엇을 하는 도구인가요?</p>
      <h2>IT 리서치를 실행하고, 결과를 읽을 수 있는 리뷰 화면으로 확인한 뒤 Obsidian Vault에 저장하는 포털입니다.</h2>
      <p>
        이 포털은 공식 문서, 표준, 논문 중심의 Research Agent를 브라우저에서 실행하기 위한 화면입니다.
        리서치 유형 preset으로 출처 우선순위를 정하고, 완료 후에는 Service Blueprint 미리보기, Quality Gate,
        리서치 리뷰 작업, Obsidian 산출물 링크를 한 화면에서 확인합니다.
      </p>
    </section>

    <section id="quick-start" class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">처음 5분</p>
        <h2>가장 안전한 실행 순서</h2>
      </div>
      <ol class="step-list">
        <li>
          <strong>리서치 유형을 먼저 고릅니다.</strong>
          <span>IT 아키텍처, 논문 합성, 표준·보안, 시장 조사, 공식 문서 중 하나를 선택하면 출처 우선순위와 기본 깊이가 맞춰집니다.</span>
        </li>
        <li>
          <strong>주제를 한 문장으로 입력합니다.</strong>
          <span>예: OpenAI Agents SDK와 LangGraph 비교, Agentic RAG 구조 분류와 실서비스 기본형.</span>
        </li>
        <li>
          <strong>드라이런을 켠 채로 실행합니다.</strong>
          <span>드라이런은 Vault에 파일을 쓰지 않고 생성 예정 경로와 실행 계획만 보여줍니다.</span>
        </li>
        <li>
          <strong>결과 패널에서 계획 또는 산출물을 확인합니다.</strong>
          <span>드라이런은 planned artifacts를, live run은 Service Blueprint preview, Quality Gate, Obsidian 링크를 보여줍니다.</span>
        </li>
        <li>
          <strong>실제 저장이 필요할 때만 드라이런을 끕니다.</strong>
          <span>live run은 Obsidian Vault에 Markdown 산출물을 씁니다. 처음 live run은 오프라인을 켜면 더 안전합니다.</span>
        </li>
        <li>
          <strong>리서치 리뷰와 Vault 정비를 나눠서 봅니다.</strong>
          <span>리서치 리뷰는 근거/품질 검토용이고, Vault 정비는 backlink, cleanup, audit 같은 운영 작업입니다.</span>
        </li>
      </ol>
    </section>

    <section id="portal-map" class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">화면 이해</p>
        <h2>각 영역이 의미하는 것</h2>
      </div>
      <div class="term-grid">
        <article>
          <h3>실행</h3>
          <p>리서치 유형, 주제, 깊이, provider, bilingual 여부를 정하는 영역입니다. 입력한 주제는 topic 또는 objective로 처리됩니다.</p>
        </article>
        <article>
          <h3>리서치 유형</h3>
          <p>preset 버튼입니다. 논문 합성은 papers를 먼저 보고, IT 아키텍처와 공식 문서는 official-docs를 먼저 봅니다.</p>
        </article>
        <article>
          <h3>리서치 깊이</h3>
          <p>빠른 스캔, 표준 분석, 심층 분석을 고릅니다. 깊이에 따라 기본 논문 수가 달라지고, 필요하면 직접 조정할 수 있습니다.</p>
        </article>
        <article>
          <h3>제공자</h3>
          <p><code>auto</code>는 OpenAI key가 있으면 OpenAI, 없으면 Gemini를 선택합니다. 특정 provider를 쓰려면 <code>openai</code> 또는 <code>gemini</code>를 고릅니다.</p>
        </article>
        <article>
          <h3>드라이런</h3>
          <p>Vault에 쓰지 않는 preview 모드입니다. 운영 전 기본값으로 권장합니다.</p>
        </article>
        <article>
          <h3>오프라인</h3>
          <p>LLM/API 수집을 건너뛰고 내장 fallback으로 산출물을 만듭니다. UI와 Obsidian writer 검증에 좋습니다.</p>
        </article>
        <article>
          <h3>원본/한글 병기</h3>
          <p>켜면 Service Blueprint와 Source Note에 원문과 한국어 번역을 함께 남깁니다. 끄면 영어 중심 산출물로 토큰과 노트 길이를 줄입니다.</p>
        </article>
        <article>
          <h3>결과</h3>
          <p>raw JSON 대신 사람이 읽을 수 있는 리뷰 화면입니다. Quality Gate, Obsidian 링크, Service Blueprint preview를 확인합니다.</p>
        </article>
        <article>
          <h3>리서치 리뷰</h3>
          <p>근거 부족, 품질 gate 경고, reviewed 승격처럼 전문가가 판단해야 할 다음 행동을 보여줍니다.</p>
        </article>
        <article>
          <h3>Vault 정비</h3>
          <p><code>/next-actions</code> 결과입니다. backlink, source audit, bilingual audit, cleanup 같은 Vault 운영 작업을 보여줍니다.</p>
        </article>
        <article>
          <h3>작업 저장소</h3>
          <p>포털 job 이력 상태입니다. 오래된 completed/failed job이 많으면 cleanup preview에 정리 후보가 표시됩니다.</p>
        </article>
      </div>
    </section>

    <section class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">추천 시나리오</p>
        <h2>상황별 권장 옵션</h2>
      </div>
      <div class="scenario-table">
        <div class="scenario-row scenario-head">
          <span>상황</span>
          <span>권장 설정</span>
          <span>확인할 결과</span>
        </div>
        <div class="scenario-row">
          <span>처음 UI 확인</span>
          <span>IT 아키텍처, 드라이런 ON, 오프라인 ON, provider auto</span>
          <span>결과 패널의 planned artifacts, job 상태 completed</span>
        </div>
        <div class="scenario-row">
          <span>논문 중심 구조 분류</span>
          <span>논문 합성, 심층 분석, 원본/한글 병기 ON</span>
          <span>papers 우선 source note, Evidence Ledger의 Needs Verification</span>
        </div>
        <div class="scenario-row">
          <span>표준·보안 검토</span>
          <span>표준·보안, 표준 분석, provider auto</span>
          <span>Quality Gate PASS/WARN, 표준 출처와 공식 문서 근거</span>
        </div>
        <div class="scenario-row">
          <span>실제 Vault 저장 리허설</span>
          <span>드라이런 OFF, 오프라인 ON</span>
          <span>Obsidian에 bilingual run log/topic map 생성</span>
        </div>
        <div class="scenario-row">
          <span>실사용 리서치</span>
          <span>주제에 맞는 preset, 드라이런 OFF, 오프라인 OFF, provider auto</span>
          <span>Service Blueprint preview, Obsidian 산출물 링크, 리서치 리뷰 작업</span>
        </div>
        <div class="scenario-row">
          <span>실패 job 재실행</span>
          <span>PM Portal의 재실행 버튼 사용</span>
          <span><code>rerun_of</code>, Run Lineage, backlink proposal 후보</span>
        </div>
      </div>
    </section>

    <section class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">산출물</p>
        <h2>Obsidian에 무엇이 만들어지나요?</h2>
      </div>
      <div class="term-grid">
        <article>
          <h3>Source Note</h3>
          <p>공식 문서, 표준, 논문 등 개별 출처 단위의 요약과 claim을 저장합니다.</p>
        </article>
        <article>
          <h3>Evidence Ledger</h3>
          <p>출처별 claim을 구조화해 근거 표로 모읍니다. 이후 service blueprint의 근거가 됩니다.</p>
        </article>
        <article>
          <h3>Service Blueprint</h3>
          <p>실서비스에 유용한 기본형, 적용 조건, 위험, 검증 방법을 정리합니다.</p>
        </article>
        <article>
          <h3>Topic Map</h3>
          <p>source note, evidence ledger, service blueprint를 Obsidian wikilink로 연결합니다.</p>
        </article>
        <article>
          <h3>Run Log</h3>
          <p>실행 옵션, 품질 gate, bilingual audit, 생성 artifact 목록을 남기는 실행 이력입니다.</p>
        </article>
        <article>
          <h3>Proposal Notes</h3>
          <p>backlink, cleanup, refresh 같은 후속 변경 후보를 사람이 검토할 수 있게 분리해 저장합니다.</p>
        </article>
      </div>
    </section>

    <section class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">두 포털</p>
        <h2>Research Agent Portal과 PM Portal의 차이</h2>
      </div>
      <div class="term-grid">
        <article>
          <h3>Research Agent Portal</h3>
          <p>Research Agent runtime을 직접 다룹니다. 단일 주제 실행, job 확인, Vault health와 next-actions 확인에 적합합니다.</p>
        </article>
        <article>
          <h3>AI Agent Architecture PM Portal</h3>
          <p>업무 시나리오 관점의 포털입니다. Research Agent runtime에 연결하면 preset 실행, 최근 job 필터, 실패 job 재실행을 사용할 수 있습니다.</p>
        </article>
      </div>
    </section>

    <section class="guide-section">
      <div class="guide-section-head">
        <p class="eyebrow">주의</p>
        <h2>안전하게 쓰는 기준</h2>
      </div>
      <ul class="check-list">
        <li>처음 실행은 드라이런으로 시작합니다.</li>
        <li>실제 Vault에 쓰기 전 `/doctor` 또는 health 상태를 확인합니다.</li>
        <li>API key는 포털 화면이나 Git에 남기지 않고 환경변수 또는 `.env`에 둡니다.</li>
        <li>proposal note는 자동 적용 대상이 아니라 사람이 체크한 뒤 적용하는 검토 큐입니다.</li>
        <li>공유 환경에서는 bearer 인증을 켜고 토큰은 명령행이 아니라 환경변수로 둡니다.</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""
