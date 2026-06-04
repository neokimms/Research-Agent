from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SectionTemplate:
    original: str
    korean: str = ""


@dataclass(frozen=True)
class ReportProfile:
    key: str
    label: str
    report_title: str
    summary: str
    required_sections: tuple[str, ...]
    section_guidance: Mapping[str, str]
    focus_rules: tuple[str, ...]
    section_templates: Mapping[str, SectionTemplate]

    def template_for(self, section: str) -> SectionTemplate:
        return self.section_templates.get(section) or SectionTemplate(
            f"- Review the evidence ledger and complete the `{section}` section.",
            f"- 근거 장부를 검토한 뒤 `{section}` 섹션을 보완하세요.",
        )


ARCHITECTURE_SECTIONS = (
    "One-Line Conclusion",
    "When To Use",
    "Structure Classification",
    "Recommended Baseline",
    "Implementation Order",
    "Operational Risks",
    "Verification",
    "Evidence",
    "Still Uncertain",
    "Related Notes",
)

PAPER_SECTIONS = (
    "One-Line Conclusion",
    "Research Scope",
    "Paper Corpus",
    "Research Lineage",
    "Methodology Comparison",
    "Dataset And Benchmark Signals",
    "Evidence Strength",
    "Reproducibility Notes",
    "Practical Applicability",
    "Open Problems",
    "Recommended Baseline",
    "Evidence",
    "Still Uncertain",
    "Related Notes",
)

MARKET_SECTIONS = (
    "One-Line Conclusion",
    "Research Scope",
    "Market Landscape",
    "Segment And Persona",
    "Vendor And Product Map",
    "Pricing And Packaging Signals",
    "Adoption Drivers",
    "Competitive Differentiators",
    "Market Risks",
    "Opportunity Hypotheses",
    "Recommended Baseline",
    "Evidence",
    "Still Uncertain",
    "Related Notes",
)


COMMON_TEMPLATES: dict[str, SectionTemplate] = {
    "One-Line Conclusion": SectionTemplate(
        "TBD after reviewing the evidence ledger.",
        "근거 장부를 검토한 뒤 한 문장 결론을 작성하세요.",
    ),
    "Research Scope": SectionTemplate(
        "- Define the question, decision context, audience, and exclusions before promoting this note.",
        "- 이 노트를 승격하기 전에 질문, 의사결정 맥락, 독자, 제외 범위를 정의하세요.",
    ),
    "Recommended Baseline": SectionTemplate(
        "```text\nTBD\n```",
        "```text\n검토 후 권장 기본안을 작성하세요.\n```",
    ),
    "Evidence": SectionTemplate(
        "- See the generated evidence ledger.",
        "- 생성된 근거 장부를 확인하세요.",
    ),
    "Still Uncertain": SectionTemplate(
        "- Add unresolved questions during review.",
        "- 검토 중 남은 미해결 질문을 추가하세요.",
    ),
    "Related Notes": SectionTemplate(
        "- TBD",
        "- 추후 연결할 Obsidian 노트를 추가하세요.",
    ),
}

ARCHITECTURE_TEMPLATES: dict[str, SectionTemplate] = {
    **COMMON_TEMPLATES,
    "When To Use": SectionTemplate(
        "- TBD after reviewing the evidence ledger.",
        "- 근거 장부를 검토한 뒤 적용 조건을 작성하세요.",
    ),
    "Structure Classification": SectionTemplate(
        "- TBD after reviewing the evidence ledger.",
        "- 근거 장부를 검토한 뒤 구조 분류를 작성하세요.",
    ),
    "Implementation Order": SectionTemplate(
        "1. Review the evidence ledger.\n2. Confirm source quality.\n3. Promote the note after human review.",
        "1. 근거 장부를 검토합니다.\n2. 출처 품질을 확인합니다.\n3. 사람이 검토한 뒤 노트를 승격합니다.",
    ),
    "Operational Risks": SectionTemplate(
        "- Stale or weakly verified sources.",
        "- 오래되었거나 검증이 약한 출처.",
    ),
    "Verification": SectionTemplate(
        "- Check every important claim against the evidence ledger.",
        "- 모든 중요 주장을 근거 장부와 대조해 확인하세요.",
    ),
}

PAPER_TEMPLATES: dict[str, SectionTemplate] = {
    **COMMON_TEMPLATES,
    "Paper Corpus": SectionTemplate(
        "- List the included papers, venues, years, DOI/arXiv identifiers, and why each paper matters.",
        "- 포함된 논문, 발표처, 연도, DOI/arXiv 식별자, 각 논문의 중요성을 정리하세요.",
    ),
    "Research Lineage": SectionTemplate(
        "- Describe how the papers relate: foundation, extension, benchmark, reproduction, or critique.",
        "- 논문 간 관계를 기반 연구, 확장, 벤치마크, 재현, 비판 관점으로 설명하세요.",
    ),
    "Methodology Comparison": SectionTemplate(
        "- Compare assumptions, model or system design, evaluation method, and threat model.",
        "- 가정, 모델 또는 시스템 설계, 평가 방법, 위협 모델을 비교하세요.",
    ),
    "Dataset And Benchmark Signals": SectionTemplate(
        "- Record datasets, benchmark tasks, metrics, baselines, and known benchmark caveats.",
        "- 데이터셋, 벤치마크 과제, 지표, 기준선, 알려진 벤치마크 주의점을 기록하세요.",
    ),
    "Evidence Strength": SectionTemplate(
        "- Separate replicated results, single-paper claims, negative evidence, and claims needing verification.",
        "- 재현된 결과, 단일 논문 주장, 반대 근거, 추가 검증이 필요한 주장을 구분하세요.",
    ),
    "Reproducibility Notes": SectionTemplate(
        "- Check code availability, data availability, hardware assumptions, evaluation scripts, and license constraints.",
        "- 코드 공개 여부, 데이터 공개 여부, 하드웨어 가정, 평가 스크립트, 라이선스 제약을 확인하세요.",
    ),
    "Practical Applicability": SectionTemplate(
        "- Explain where the research can be applied now, where it is experimental, and what integration work remains.",
        "- 지금 적용 가능한 부분, 실험 단계인 부분, 남은 통합 작업을 설명하세요.",
    ),
    "Open Problems": SectionTemplate(
        "- List unresolved technical questions, missing evaluations, and papers to collect next.",
        "- 미해결 기술 질문, 누락된 평가, 다음에 수집할 논문을 나열하세요.",
    ),
    "Recommended Baseline": SectionTemplate(
        "- Identify the most defensible method or reading path and state why it is safer than alternatives.",
        "- 가장 방어 가능한 방법 또는 읽기 경로를 정하고, 대안보다 안전한 이유를 설명하세요.",
    ),
}

MARKET_TEMPLATES: dict[str, SectionTemplate] = {
    **COMMON_TEMPLATES,
    "Market Landscape": SectionTemplate(
        "- Map the market category, adjacent categories, maturity, and current adoption signals.",
        "- 시장 카테고리, 인접 카테고리, 성숙도, 현재 도입 신호를 정리하세요.",
    ),
    "Segment And Persona": SectionTemplate(
        "- Identify buyer segments, user personas, use cases, pains, and buying triggers.",
        "- 구매자 세그먼트, 사용자 페르소나, 사용 사례, 고충, 구매 촉발 요인을 식별하세요.",
    ),
    "Vendor And Product Map": SectionTemplate(
        "- Compare vendors, products, positioning, integrations, and publicly visible traction.",
        "- 공급사, 제품, 포지셔닝, 연동, 공개적으로 확인 가능한 성과를 비교하세요.",
    ),
    "Pricing And Packaging Signals": SectionTemplate(
        "- Record observable pricing, packaging, licensing, deployment, and procurement signals.",
        "- 관찰 가능한 가격, 패키징, 라이선스, 배포, 조달 신호를 기록하세요.",
    ),
    "Adoption Drivers": SectionTemplate(
        "- Summarize demand drivers, regulation, platform shifts, ecosystem momentum, and timing.",
        "- 수요 동인, 규제, 플랫폼 변화, 생태계 흐름, 시점을 요약하세요.",
    ),
    "Competitive Differentiators": SectionTemplate(
        "- State durable differentiation claims only when evidence supports them.",
        "- 근거가 뒷받침되는 경우에만 지속 가능한 차별화 주장을 작성하세요.",
    ),
    "Market Risks": SectionTemplate(
        "- Capture substitution risk, commoditization, budget friction, compliance barriers, and weak signals.",
        "- 대체 위험, 상품화, 예산 마찰, 컴플라이언스 장벽, 약한 신호를 기록하세요.",
    ),
    "Opportunity Hypotheses": SectionTemplate(
        "- Propose testable opportunity hypotheses and the evidence needed to validate each one.",
        "- 검증 가능한 기회 가설과 각 가설을 확인하는 데 필요한 근거를 제안하세요.",
    ),
    "Recommended Baseline": SectionTemplate(
        "- Recommend the most practical market entry, tracking, or partnership stance based on current evidence.",
        "- 현재 근거를 바탕으로 가장 실용적인 시장 진입, 추적, 파트너십 관점을 권장하세요.",
    ),
}


REPORT_PROFILES: dict[str, ReportProfile] = {
    "architecture": ReportProfile(
        key="architecture",
        label="IT Architecture",
        report_title="Service Blueprint",
        summary="Use for implementation-oriented IT architecture decisions.",
        required_sections=ARCHITECTURE_SECTIONS,
        section_guidance={
            "One-Line Conclusion": "State the architecture recommendation in one evidence-backed sentence.",
            "When To Use": "Explain the fit conditions and non-fit conditions.",
            "Structure Classification": "Classify the architectural pattern, control plane, data plane, and integration style.",
            "Recommended Baseline": "Give the most useful default implementation shape.",
            "Implementation Order": "List a practical build or adoption sequence.",
            "Operational Risks": "Identify reliability, security, cost, governance, and lock-in risks.",
            "Verification": "List checks that prove the architecture is usable in production.",
            "Evidence": "Reference claim IDs and source URLs from the evidence ledger.",
            "Still Uncertain": "Keep unverified claims and follow-up questions visible.",
            "Related Notes": "Suggest Obsidian backlinks and topic-map links.",
        },
        focus_rules=(
            "Prefer official documentation and standards for capability and constraint claims.",
            "Turn findings into a practical baseline architecture, not a market memo.",
            "Make implementation order, risks, and verification criteria actionable.",
        ),
        section_templates=ARCHITECTURE_TEMPLATES,
    ),
    "paper": ReportProfile(
        key="paper",
        label="Paper Analysis",
        report_title="Paper Analysis Report",
        summary="Use for literature review, methodology comparison, and research-to-practice assessment.",
        required_sections=PAPER_SECTIONS,
        section_guidance={
            "One-Line Conclusion": "State the strongest research takeaway and its confidence level.",
            "Research Scope": "Define research question, included subfields, and exclusions.",
            "Paper Corpus": "List papers, venues, years, identifiers, and selection rationale.",
            "Research Lineage": "Connect papers by foundation, extension, benchmark, reproduction, or critique.",
            "Methodology Comparison": "Compare assumptions, methods, model/system design, and evaluation setup.",
            "Dataset And Benchmark Signals": "Capture datasets, metrics, baselines, benchmark caveats, and comparability.",
            "Evidence Strength": "Separate replicated findings, single-paper claims, negative evidence, and weak claims.",
            "Reproducibility Notes": "Check code, data, hardware, license, and evaluation reproducibility.",
            "Practical Applicability": "Translate research findings into realistic engineering use or caution.",
            "Open Problems": "List unresolved questions and next papers to collect.",
            "Recommended Baseline": "Recommend a reading path or defensible method baseline.",
            "Evidence": "Reference claim IDs and source URLs from the evidence ledger.",
            "Still Uncertain": "Keep missing papers, metadata gaps, and unresolved claims visible.",
            "Related Notes": "Suggest Obsidian backlinks and topic-map links.",
        },
        focus_rules=(
            "Do not flatten papers into a product recommendation; preserve methodology and evidence strength.",
            "Call out DOI/arXiv identifiers, datasets, metrics, and reproducibility constraints when available.",
            "Distinguish proven findings from single-paper or benchmark-limited claims.",
        ),
        section_templates=PAPER_TEMPLATES,
    ),
    "market": ReportProfile(
        key="market",
        label="Market Research",
        report_title="Market Research Report",
        summary="Use for market landscape, vendor/product comparison, and opportunity assessment.",
        required_sections=MARKET_SECTIONS,
        section_guidance={
            "One-Line Conclusion": "State the market implication in one evidence-backed sentence.",
            "Research Scope": "Define category, geography, buyer, time horizon, and exclusions.",
            "Market Landscape": "Summarize category shape, maturity, adjacent categories, and adoption signals.",
            "Segment And Persona": "Identify buyer segments, users, jobs-to-be-done, pains, and triggers.",
            "Vendor And Product Map": "Compare products, vendors, positioning, integrations, and traction signals.",
            "Pricing And Packaging Signals": "Capture observable pricing, packaging, license, deployment, and procurement data.",
            "Adoption Drivers": "Explain demand drivers, regulation, platform shifts, ecosystem changes, and timing.",
            "Competitive Differentiators": "State defensible differentiation with evidence and caveats.",
            "Market Risks": "Identify substitution, commoditization, budget, compliance, and weak-signal risks.",
            "Opportunity Hypotheses": "Propose testable hypotheses and validation evidence.",
            "Recommended Baseline": "Recommend a market entry, monitoring, partnership, or buying stance.",
            "Evidence": "Reference claim IDs and source URLs from the evidence ledger.",
            "Still Uncertain": "Keep missing market data, unverifiable claims, and follow-up questions visible.",
            "Related Notes": "Suggest Obsidian backlinks and topic-map links.",
        },
        focus_rules=(
            "Treat pricing, market size, and traction as signals unless directly evidenced.",
            "Separate vendor claims from independently verifiable evidence.",
            "Make recommendations testable through follow-up customer, competitor, or source validation.",
        ),
        section_templates=MARKET_TEMPLATES,
    ),
}


REPORT_PROFILE_ALIASES = {
    "": "architecture",
    "it": "architecture",
    "it-architecture": "architecture",
    "architecture-review": "architecture",
    "service-blueprint": "architecture",
    "papers": "paper",
    "paper-analysis": "paper",
    "literature": "paper",
    "literature-review": "paper",
    "research": "paper",
    "market-research": "market",
    "market_analysis": "market",
    "market-analysis": "market",
    "official-docs": "architecture",
    "standards": "architecture",
}


def normalize_research_type(value: str | None) -> str:
    key = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    key = REPORT_PROFILE_ALIASES.get(key, key)
    if key not in REPORT_PROFILES:
        return "architecture"
    return key


def get_report_profile(value: str | None = None) -> ReportProfile:
    return REPORT_PROFILES[normalize_research_type(value)]


def report_profile_keys() -> tuple[str, ...]:
    return tuple(REPORT_PROFILES.keys())


def section_default_text(section: str, *, research_type: str | None = None) -> str:
    return get_report_profile(research_type).template_for(section).original


def render_required_sections_for_prompt(profile: ReportProfile) -> str:
    return "\n".join(
        f"- {section}: {profile.section_guidance.get(section, 'Use evidence-backed concise prose.')}"
        for section in profile.required_sections
    )


def render_focus_rules_for_prompt(profile: ReportProfile) -> str:
    return "\n".join(f"- {rule}" for rule in profile.focus_rules)
