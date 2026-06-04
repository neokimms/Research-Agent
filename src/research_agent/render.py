from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .models import EvidenceBundle, EvidenceClaim, QualityGateResult, RunWarning, SourceRecord
from .report_profiles import ReportProfile, SectionTemplate, get_report_profile
from .textutil import yaml_scalar


def render_source_note(
    source: SourceRecord,
    *,
    topic: str,
    checked_at: str,
    source_id: str = "",
    claims: list[EvidenceClaim] | None = None,
) -> str:
    authors = "\n".join(f'  - "{author}"' for author in source.authors) or "  []"
    evidence_claims = claims or []
    claim_lines = _claim_lines(evidence_claims)
    implementation_lines = _implementation_lines(evidence_claims)
    verification_lines = _source_verification_lines(source, evidence_claims)
    return f"""---
type: source-note
topic: {yaml_scalar(topic)}
source_id: {yaml_scalar(source_id)}
source_type: {yaml_scalar(source.source_type)}
source_provider: {yaml_scalar(source.source_provider)}
source_url: {yaml_scalar(source.url)}
canonical_url: {yaml_scalar(source.canonical_url)}
doi: {yaml_scalar(source.doi)}
arxiv_id: {yaml_scalar(source.arxiv_id)}
source_score: {yaml_scalar(source.source_score if source.source_score else None)}
title: {yaml_scalar(source.title)}
authors:
{authors}
published_at: {yaml_scalar(source.published_at)}
updated_at: {yaml_scalar(source.updated_at)}
checked_at: {yaml_scalar(checked_at)}
confidence: medium
status: draft
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---

# {source.title}

## Core Summary

{_bilingual_block(source.summary or "No summary captured yet.")}

## Important Claims

{_claim_lines_bilingual(evidence_claims)}

## Implementation Meaning

{_bilingual_block(implementation_lines)}

## Citable Evidence

- Source: {source.url or source.canonical_url or "No URL captured."}
{_citable_evidence_lines_bilingual(evidence_claims)}

## Citation Metadata

- Canonical URL: {source.canonical_url or "Not captured."}
- DOI: {source.doi or "Not captured."}
- arXiv ID: {source.arxiv_id or "Not captured."}
- Provider: {source.source_provider or "Not captured."}
- Source Score: {f"{source.source_score:.2f}" if source.source_score else "Not scored."}

## Limits And Cautions

{_bilingual_block(verification_lines)}

## Related Notes
"""


def render_evidence_ledger(
    topic: str,
    evidence: EvidenceBundle,
    *,
    checked_at: str,
    quality_gates: list[QualityGateResult] | None = None,
) -> str:
    rows = []
    for claim in evidence.claims:
        claim_text = _table_cell(claim.claim)
        evidence_text = _table_cell(claim.evidence)
        source_label = _table_cell(claim.source_url or claim.source_title)
        rows.append(
            f"| {claim.claim_id} | {claim_text} | {source_label} | "
            f"{_table_cell(claim.source_type)} | {checked_at} | {_table_cell(claim.confidence)} | "
            f"{_table_cell(claim.category)}: {evidence_text} |"
        )
    if not rows:
        rows.append(f"| E001 | No sources collected yet. |  | run-log | {checked_at} | low | Add sources. |")

    conflicts = "\n".join(f"- {item}" for item in evidence.conflicts) or "- None captured yet."
    needs = "\n".join(f"- {item}" for item in evidence.needs_verification) or "- None captured yet."

    return f"""---
type: evidence-ledger
topic: {yaml_scalar(topic)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
extraction_mode: {yaml_scalar(evidence.extraction_mode)}
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---

# Evidence Ledger: {topic}

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

## Claim Translations

{_claim_translation_lines(evidence.claims)}

## Conflicting Evidence

{_bilingual_block(conflicts)}

## Needs Verification

{_bilingual_block(needs)}

## Quality Gates

{_quality_gate_table(quality_gates or [])}
"""


def render_evidence_synthesis_context(topic: str, evidence: EvidenceBundle, *, checked_at: str) -> str:
    claim_lines = []
    for claim in evidence.claims:
        source = claim.source_url or claim.source_title or "No source captured."
        claim_lines.append(
            f"- {claim.claim_id} [{claim.confidence}, {claim.category}] {claim.claim}\n"
            f"  - Evidence: {claim.evidence}\n"
            f"  - Source: {source}\n"
            f"  - Source type: {claim.source_type}"
        )
    if not claim_lines:
        claim_lines.append("- No structured claims extracted yet.")

    conflicts = "\n".join(f"- {item}" for item in evidence.conflicts) or "- None captured yet."
    needs = "\n".join(f"- {item}" for item in evidence.needs_verification) or "- None captured yet."

    return f"""# Evidence Context: {topic}

- checked_at: {checked_at}
- extraction_mode: {evidence.extraction_mode}

## Claims

{chr(10).join(claim_lines)}

## Conflicts

{conflicts}

## Needs Verification

{needs}
"""


def _table_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|").strip()


def _claim_lines(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return "- No structured claims extracted yet."
    return "\n".join(
        f"- {claim.claim_id} ({claim.confidence}, {claim.category}): {claim.claim}"
        for claim in claims
    )


def _implementation_lines(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return "- Review this source before using it in a service blueprint."
    categories = sorted({claim.category for claim in claims if claim.category})
    if not categories:
        return "- Use the extracted claims as supporting evidence for the service blueprint."
    return "\n".join(f"- Supports `{category}` decisions." for category in categories)


def _citable_evidence_lines(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return ""
    return "\n".join(f"- {claim.claim_id}: {claim.evidence}" for claim in claims if claim.evidence)


def _source_verification_lines(source: SourceRecord, claims: list[EvidenceClaim]) -> str:
    lines = ["- This source note is agent-generated and needs review."]
    if not source.url and not source.canonical_url:
        lines.append("- Source URL is missing.")
    if source.source_type == "official-docs" and source.url and source.url.endswith("/"):
        lines.append("- This may be a seed domain rather than an exact documentation page.")
    if source.source_type == "papers" and not source.doi and not source.arxiv_id:
        lines.append("- Paper identity is missing DOI/arXiv metadata.")
    if not claims:
        lines.append("- No structured claim is linked to this source yet.")
    return "\n".join(lines)


def render_fallback_blueprint(
    topic: str,
    sources: list[SourceRecord],
    *,
    checked_at: str,
    bilingual: bool = True,
    source_priority: list[str] | None = None,
    research_type: str | None = None,
) -> str:
    profile = get_report_profile(research_type)
    source_lines = "\n".join(
        f"- [{source.title}]({source.url or source.canonical_url}) ({source.source_type})"
        if source.url or source.canonical_url
        else f"- {source.title} ({source.source_type})"
        for source in sources
    ) or "- No sources collected yet."
    sections = _fallback_profile_sections(profile, source_lines=source_lines, bilingual=bilingual)

    return f"""---
type: service-blueprint
topic: {yaml_scalar(topic)}
research_type: {yaml_scalar(profile.key)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
confidence: low
source_priority:
{_source_priority_frontmatter(source_priority)}
generated_by: research-agent
{_language_frontmatter(bilingual)}
---

# {topic} {profile.report_title}

{sections}
"""


def _fallback_profile_sections(profile: ReportProfile, *, source_lines: str, bilingual: bool) -> str:
    rendered: list[str] = []
    for section in profile.required_sections:
        if section == "Evidence":
            content = source_lines
        elif profile.key == "architecture" and section == "One-Line Conclusion":
            content = _localized_block(
                "Use an Obsidian-first workflow where the agent gathers evidence, writes source notes, creates an evidence ledger, and drafts a service blueprint for human review.",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "When To Use":
            content = _localized_block(
                "- When source freshness and traceability matter.\n- When official documentation, standards, and papers should outrank general web summaries.\n- When the final artifact must remain readable and editable in Obsidian.",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "Structure Classification":
            content = _localized_block(
                "- Source-first research workflow\n- Evidence-led synthesis\n- Human-reviewed knowledge base",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "Recommended Baseline":
            content = _fallback_baseline_block(bilingual=bilingual)
        elif profile.key == "architecture" and section == "Implementation Order":
            content = _localized_block(
                "1. Configure the Obsidian vault path.\n2. Collect official docs, standards, and paper metadata.\n3. Generate source notes and an evidence ledger.\n4. Use OpenAI synthesis only after local evidence is assembled.\n5. Review and promote draft notes inside Obsidian.",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "Operational Risks":
            content = _localized_block(
                "- Stale documentation\n- Weak source metadata\n- Overwriting reviewed notes\n- Treating generated synthesis as verified fact",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "Verification":
            content = _localized_block(
                "- Check that every claim has a source URL.\n- Check publication and update dates.\n- Keep uncertain claims in the uncertainty section.",
                bilingual=bilingual,
            )
        elif profile.key == "architecture" and section == "Still Uncertain":
            content = _localized_block(
                "- Exact source pages and paper metadata need human review.",
                bilingual=bilingual,
            )
        else:
            content = _section_template_block(profile.template_for(section), bilingual=bilingual)
        rendered.append(f"## {section}\n\n{content}")
    return "\n\n".join(rendered)


def _section_template_block(template: SectionTemplate, *, bilingual: bool) -> str:
    if bilingual and template.korean.strip():
        return f"""**원본**

{template.original.strip()}

**한국어 번역**

{template.korean.strip()}"""
    return _localized_block(template.original, bilingual=bilingual)


def _source_priority_frontmatter(source_priority: list[str] | None) -> str:
    priority = [item.strip() for item in source_priority or [] if item.strip()]
    if not priority:
        priority = ["official-docs", "standards", "papers"]
    return "\n".join(f"  - {yaml_scalar(item)}" for item in priority)


def _fallback_baseline_block(*, bilingual: bool) -> str:
    original = """```text
question
-> source collection
-> evidence ledger
-> structure classification
-> service blueprint draft
-> Obsidian review
```"""
    if not bilingual:
        return original
    return """**원본**

```text
question
-> source collection
-> evidence ledger
-> structure classification
-> service blueprint draft
-> Obsidian review
```

**한국어 번역**

```text
질문
-> 출처 수집
-> 근거 장부
-> 구조 분류
-> 실서비스 기본형 초안
-> Obsidian 검토
```"""


def _language_frontmatter(bilingual: bool) -> str:
    if bilingual:
        return "language: bilingual\noriginal_language: en\ntranslation_language: ko"
    return "language: en"


def _localized_block(text: str, *, bilingual: bool) -> str:
    if bilingual:
        return _bilingual_block(text)
    return text.strip() or "No content."


def render_run_note(
    topic: str,
    artifacts: list[str],
    *,
    checked_at: str,
    mode: str,
    quality_gates: list[QualityGateResult] | None = None,
    warnings: list[RunWarning] | None = None,
    bilingual_audit: str | None = None,
    rerun_of: str | None = None,
) -> str:
    artifact_lines = "\n".join(f"- {path}" for path in artifacts)
    bilingual_audit_section = f"\n## Bilingual Audit\n\n{bilingual_audit.strip()}\n" if bilingual_audit else ""
    rerun_frontmatter = f"rerun_of: {yaml_scalar(rerun_of)}\n" if rerun_of else ""
    lineage_section = _run_lineage_section(rerun_of)
    return f"""---
type: run-log
topic: {yaml_scalar(topic)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
mode: {yaml_scalar(mode)}
{rerun_frontmatter}\
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---

# Research Run: {topic}

## Mode

{_bilingual_block(mode)}
{lineage_section}

## Quality Gates

{_quality_gate_table(quality_gates or [])}

## Warnings

{_warning_table(warnings or [])}
{bilingual_audit_section}

## Artifacts

{artifact_lines or "- No artifacts generated."}

## Next Review Steps

{_bilingual_block("- Open the service blueprint in Obsidian.\n- Promote useful notes from draft to reviewed.\n- Add exact citations where seed sources need deeper fetching.")}
"""


def render_topic_map(
    topic: str,
    *,
    source_paths: list[str],
    evidence_path: str,
    blueprint_path: str,
    evidence: EvidenceBundle,
    checked_at: str,
    vault_path: str,
    rerun_of: str | None = None,
) -> str:
    source_links = "\n".join(f"- {_wikilink(path, vault_path)}" for path in source_paths) or "- No source notes generated."
    categories = sorted({claim.category for claim in evidence.claims if claim.category})
    category_lines = "\n".join(f"- `{category}`" for category in categories) or "- No categories extracted yet."
    claim_links = "\n".join(
        f"- {claim.claim_id}: {claim.claim} ({_wikilink(evidence_path, vault_path)})"
        for claim in evidence.claims
    ) or "- No claims extracted yet."
    rerun_frontmatter = f"rerun_of: {yaml_scalar(rerun_of)}\n" if rerun_of else ""
    lineage_section = _run_lineage_section(rerun_of)

    return f"""---
type: topic-map
topic: {yaml_scalar(topic)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
{rerun_frontmatter}\
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---

# Topic Map: {topic}
{lineage_section}

## Core Notes

- Blueprint: {_wikilink(blueprint_path, vault_path)}
- Evidence Ledger: {_wikilink(evidence_path, vault_path)}

## Source Notes

{source_links}

## Claim Index

{_claim_index_lines_bilingual(evidence.claims, evidence_path=evidence_path, vault_path=vault_path)}

## Suggested Backlinks

{_bilingual_block("- Link the service blueprint back to this topic map after review.\n- Link reviewed source notes to the evidence ledger.\n- Promote durable categories into taxonomy notes under `20_Taxonomy`.")}

## Extracted Categories

{category_lines}
"""


def render_final_report(
    topic: str,
    *,
    blueprint_markdown: str,
    evidence: EvidenceBundle,
    sources: list[SourceRecord],
    source_paths: list[str],
    evidence_path: str,
    blueprint_path: str,
    topic_map_path: str,
    run_path: str,
    checked_at: str,
    vault_path: str,
    quality_gates: list[QualityGateResult] | None = None,
    research_type: str | None = None,
    bilingual: bool = True,
) -> str:
    profile = get_report_profile(research_type)
    profile_label_korean = _profile_label_korean(profile)
    source_links = "\n".join(f"- {_wikilink(path, vault_path)}" for path in source_paths) or "- No source notes generated."
    source_count = len(sources)
    claim_count = len(evidence.claims)
    gate_summary = _quality_gate_summary(quality_gates or [])
    blueprint_summary = _blueprint_review_summary(blueprint_markdown)
    confidence_summary = _confidence_summary(evidence)
    executive_original = (
        f"This final report summarizes `{topic}` as a {profile.label} review. "
        f"The run collected {source_count} source record(s), extracted {claim_count} evidence claim(s), "
        f"and produced quality gate status `{gate_summary}`. "
        f"Treat the Service Blueprint as the implementation draft and this note as the reviewable final report."
    )
    executive_korean = (
        f"이 최종 보고서는 `{topic}`을(를) {profile_label_korean} 관점에서 정리한 검토 보고서입니다. "
        f"이번 실행에서는 출처 {source_count}개, 근거 주장 {claim_count}개를 수집했고, "
        f"품질 게이트 상태는 `{gate_summary}`입니다. "
        f"Service Blueprint는 구현 초안으로, 이 노트는 사람이 검토할 대표 최종 보고서로 사용하세요."
    )
    return f"""---
type: final-report
topic: {yaml_scalar(topic)}
research_type: {yaml_scalar(profile.key)}
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
generated_by: research-agent
{_language_frontmatter(bilingual)}
---

# Final Report: {topic}

## Executive Summary

{_paired_block(executive_original, executive_korean, bilingual=bilingual)}

## Key Findings

{_final_report_key_findings(evidence, bilingual=bilingual)}

## Evidence Table

{_final_report_evidence_table(evidence)}

## Source Coverage

{_source_coverage_summary(sources)}

## Profile-Specific Interpretation

{_final_report_profile_interpretation(profile, evidence, sources, bilingual=bilingual)}

## Blueprint Cross-Check

{_paired_block(blueprint_summary[0], blueprint_summary[1], bilingual=bilingual)}

## Quality And Limits

{_final_report_quality_section(
        evidence,
        quality_gates=quality_gates or [],
        confidence_summary=confidence_summary,
        bilingual=bilingual,
    )}

## Recommended Review Actions

{_final_report_review_actions(
        evidence,
        sources=sources,
        quality_gates=quality_gates or [],
        profile_label=profile.label,
        profile_label_korean=profile_label_korean,
        bilingual=bilingual,
    )}

## Obsidian Links

- Service Blueprint: {_wikilink(blueprint_path, vault_path)}
- Evidence Ledger: {_wikilink(evidence_path, vault_path)}
- Topic Map: {_wikilink(topic_map_path, vault_path)}
- Run Note: {_wikilink(run_path, vault_path)}

## Source Notes

{source_links}
"""


def _paired_block(original: str, korean: str, *, bilingual: bool) -> str:
    if not bilingual:
        return original.strip() or "No content."
    return f"""**원본**

{original.strip() or "No content."}

**한국어 번역**

{korean.strip() or "내용이 없습니다."}"""


def _profile_label_korean(profile: ReportProfile) -> str:
    labels = {
        "architecture": "IT 아키텍처",
        "paper": "논문 분석",
        "market": "시장조사",
        "official-docs": "공식 문서 검토",
        "standards": "표준·보안 검토",
    }
    return labels.get(profile.key, profile.label)


def _quality_gate_summary(gates: list[QualityGateResult]) -> str:
    if not gates:
        return "NOT_EVALUATED"
    counts = Counter(gate.status.upper() for gate in gates if gate.status)
    if counts.get("FAIL"):
        return f"FAIL ({counts['FAIL']} fail, {counts.get('PASS', 0)} pass)"
    if counts.get("WARN"):
        return f"WARN ({counts['WARN']} warn, {counts.get('PASS', 0)} pass)"
    if counts.get("PASS"):
        return f"PASS ({counts['PASS']} pass)"
    return ", ".join(f"{status}:{count}" for status, count in sorted(counts.items())) or "UNKNOWN"


def _confidence_summary(evidence: EvidenceBundle) -> str:
    if not evidence.claims:
        return "no claims"
    counts = Counter(claim.confidence for claim in evidence.claims)
    ordered = [f"{name}:{counts[name]}" for name in ("high", "medium", "low") if counts.get(name)]
    return ", ".join(ordered) or "no confidence labels"


def _final_report_key_findings(evidence: EvidenceBundle, *, bilingual: bool) -> str:
    if not evidence.claims:
        return _paired_block(
            "- No evidence claims were extracted, so no final findings can be stated yet.",
            "- 추출된 근거 주장이 없어 아직 최종 발견사항을 작성할 수 없습니다.",
            bilingual=bilingual,
        )

    lines: list[str] = []
    if evidence.extraction_mode != "structured-json":
        lines.append(
            _paired_block(
                f"- Evidence extraction mode is `{evidence.extraction_mode}`. Findings are derived from source summaries and require human verification.",
                f"- 근거 추출 모드는 `{evidence.extraction_mode}`입니다. 발견사항은 출처 요약에서 파생되었으므로 사람의 검증이 필요합니다.",
                bilingual=bilingual,
            )
        )

    for claim in evidence.claims[:12]:
        source = claim.source_title or claim.source_url or "No source captured."
        original = (
            f"- {claim.claim_id} (`{claim.confidence}`, `{claim.category}`): {claim.claim}\n"
            f"  - Evidence: {claim.evidence}\n"
            f"  - Source: {source}"
        )
        korean = (
            f"- {claim.claim_id} (`{_translate_to_korean(claim.confidence)}`, `{_translate_to_korean(claim.category)}`): "
            f"{_translate_to_korean(claim.claim)}\n"
            f"  - 근거: {_translate_to_korean(claim.evidence)}\n"
            f"  - 출처: {source}"
        )
        lines.append(_paired_block(original, korean, bilingual=bilingual))
    remaining = len(evidence.claims) - 12
    if remaining > 0:
        lines.append(
            _paired_block(
                f"- {remaining} additional claim(s) are available in the Evidence Ledger.",
                f"- 추가 근거 주장 {remaining}개는 Evidence Ledger에서 확인할 수 있습니다.",
                bilingual=bilingual,
            )
        )
    return "\n\n".join(lines)


def _final_report_evidence_table(evidence: EvidenceBundle) -> str:
    if not evidence.claims:
        return "- No evidence claims available."
    rows = [
        "| claim_id | confidence | category | claim | source |",
        "|---|---|---|---|---|",
    ]
    for claim in evidence.claims:
        source = claim.source_url or claim.source_title
        rows.append(
            f"| {_table_cell(claim.claim_id)} | {_table_cell(claim.confidence)} | {_table_cell(claim.category)} | "
            f"{_table_cell(claim.claim)} | {_table_cell(source)} |"
        )
    return "\n".join(rows)


def _source_coverage_summary(sources: list[SourceRecord]) -> str:
    if not sources:
        return "- No source records were collected."

    counts = Counter(source.source_type or "unknown" for source in sources)
    coverage_rows = [
        "| source_type | count |",
        "|---|---:|",
        *[f"| {_table_cell(source_type)} | {count} |" for source_type, count in sorted(counts.items())],
    ]
    source_rows = [
        "| source | type | URL |",
        "|---|---|---|",
    ]
    for source in sources:
        url = source.url or source.canonical_url or "Not captured"
        source_rows.append(f"| {_table_cell(source.title)} | {_table_cell(source.source_type)} | {_table_cell(url)} |")
    return "\n".join([*coverage_rows, "", *source_rows])


def _final_report_profile_interpretation(
    profile: ReportProfile,
    evidence: EvidenceBundle,
    sources: list[SourceRecord],
    *,
    bilingual: bool,
) -> str:
    counts = Counter(source.source_type for source in sources)
    low_count = sum(1 for claim in evidence.claims if claim.confidence == "low")
    missing_url_count = sum(1 for source in sources if not (source.url or source.canonical_url))
    if profile.key == "paper":
        original = (
            f"The current corpus contains {counts.get('papers', 0)} paper source(s). "
            "Use this report to compare methods, datasets, benchmarks, reproducibility signals, and practical applicability. "
            f"{low_count} low-confidence claim(s) and {missing_url_count} source(s) without URLs must be resolved before promoting the paper analysis."
        )
        korean = (
            f"현재 코퍼스에는 논문 출처 {counts.get('papers', 0)}개가 포함되어 있습니다. "
            "이 보고서는 방법론, 데이터셋, 벤치마크, 재현성 신호, 실무 적용 가능성을 비교하는 용도로 사용하세요. "
            f"논문 분석 노트를 승격하기 전 낮은 신뢰도 주장 {low_count}개와 URL이 없는 출처 {missing_url_count}개를 해소해야 합니다."
        )
    elif profile.key == "market":
        original = (
            "Use the current evidence as a market-research draft: separate observable public signals from opportunity hypotheses. "
            f"The run captured {counts.get('official-docs', 0)} official-docs source(s), {counts.get('standards', 0)} standards source(s), "
            f"and {counts.get('papers', 0)} paper source(s). Pricing, adoption, vendor traction, and competitive positioning still need explicit public sources."
        )
        korean = (
            "현재 근거는 시장조사 초안으로 사용하세요. 공개적으로 관찰 가능한 신호와 기회 가설을 구분해야 합니다. "
            f"이번 실행은 공식 문서 {counts.get('official-docs', 0)}개, 표준 {counts.get('standards', 0)}개, "
            f"논문 {counts.get('papers', 0)}개를 수집했습니다. 가격, 도입 사례, 공급사 성과, 경쟁 포지셔닝은 명시적인 공개 출처 보강이 필요합니다."
        )
    else:
        original = (
            "Use the current evidence as an architecture decision draft. "
            "Official documentation should anchor implementation details, standards should anchor governance and risk, "
            "and papers should be treated as supporting context unless their metadata is verified."
        )
        korean = (
            "현재 근거는 아키텍처 의사결정 초안으로 사용하세요. "
            "구현 세부사항은 공식 문서가 기준이 되어야 하고, 거버넌스와 위험은 표준 문서가 기준이 되어야 하며, "
            "논문은 메타데이터가 확인되기 전까지 보조 맥락으로 다루는 편이 안전합니다."
        )
    return _paired_block(original, korean, bilingual=bilingual)


def _blueprint_review_summary(blueprint_markdown: str) -> tuple[str, str]:
    body = _strip_markdown_frontmatter(blueprint_markdown)
    if not body.strip():
        return (
            "No Service Blueprint body was captured. Review the Run Note and re-run synthesis after evidence coverage improves.",
            "Service Blueprint 본문이 수집되지 않았습니다. Run Note를 확인하고 근거 커버리지를 보강한 뒤 합성을 다시 실행하세요.",
        )
    headings = [
        line.lstrip("#").strip()
        for line in body.splitlines()
        if line.startswith("## ") and line.lstrip("#").strip()
    ]
    placeholder_markers = ("TBD", "검토 후", "근거 장부를 검토", "No sources collected yet")
    placeholder_count = sum(body.count(marker) for marker in placeholder_markers)
    fallback_hint = "Use an Obsidian-first workflow" in body or "source collection" in body
    section_preview = ", ".join(headings[:8]) if headings else "no explicit sections"
    fallback_sentence = (
        " The blueprint appears to include fallback/template content, so do not treat it as a confirmed recommendation."
        if fallback_hint or placeholder_count
        else " The blueprint does not show obvious fallback markers, but key claims should still be checked against the Evidence Ledger."
    )
    fallback_sentence_ko = (
        " Blueprint에 fallback/template 성격의 내용이 포함된 것으로 보이므로 확정 권고로 취급하지 마세요."
        if fallback_hint or placeholder_count
        else " Blueprint에서 뚜렷한 fallback 표지는 보이지 않지만, 핵심 주장은 여전히 Evidence Ledger와 대조해야 합니다."
    )
    return (
        f"The generated Service Blueprint contains {len(headings)} section(s): {section_preview}. "
        f"Placeholder-like markers found: {placeholder_count}.{fallback_sentence}",
        f"생성된 Service Blueprint에는 {len(headings)}개 섹션이 있습니다: {section_preview}. "
        f"플레이스홀더로 보이는 표지는 {placeholder_count}개입니다.{fallback_sentence_ko}",
    )


def _final_report_quality_section(
    evidence: EvidenceBundle,
    *,
    quality_gates: list[QualityGateResult],
    confidence_summary: str,
    bilingual: bool,
) -> str:
    mode_block = _paired_block(
        f"- Evidence extraction mode: `{evidence.extraction_mode}`\n- Claim confidence distribution: {confidence_summary}",
        f"- 근거 추출 모드: `{evidence.extraction_mode}`\n- 주장 신뢰도 분포: {confidence_summary}",
        bilingual=bilingual,
    )
    conflicts = "\n".join(f"- {item}" for item in evidence.conflicts) or "- None captured yet."
    needs = "\n".join(f"- {item}" for item in evidence.needs_verification) or "- None captured yet."
    return f"""{mode_block}

### Quality Gates

{_quality_gate_table(quality_gates)}

### Conflicts

{_localized_block(conflicts, bilingual=bilingual)}

### Needs Verification

{_localized_block(needs, bilingual=bilingual)}"""


def _final_report_review_actions(
    evidence: EvidenceBundle,
    *,
    sources: list[SourceRecord],
    quality_gates: list[QualityGateResult],
    profile_label: str,
    profile_label_korean: str,
    bilingual: bool,
) -> str:
    low_count = sum(1 for claim in evidence.claims if claim.confidence == "low")
    missing_url_count = sum(1 for source in sources if not (source.url or source.canonical_url))
    failed_gates = [gate for gate in quality_gates if gate.status.upper() == "FAIL"]
    original_lines = [
        f"Open the linked Evidence Ledger and verify each high-impact claim before using the {profile_label} recommendation.",
        "Open the Service Blueprint only after reading this report summary and the quality gate table.",
    ]
    korean_lines = [
        f"연결된 Evidence Ledger를 열고 {profile_label_korean} 권고에 사용하기 전 영향도가 큰 주장을 하나씩 검증하세요.",
        "이 보고서 요약과 품질 게이트 표를 읽은 뒤 Service Blueprint를 검토하세요.",
    ]
    if evidence.extraction_mode != "structured-json":
        original_lines.append("Re-run with a live LLM provider or stronger source collection if the report needs production-grade confidence.")
        korean_lines.append("운영 수준의 신뢰도가 필요하면 live LLM provider 또는 더 강한 출처 수집 설정으로 다시 실행하세요.")
    if low_count:
        original_lines.append(f"Resolve {low_count} low-confidence claim(s) or keep them explicitly marked as uncertain.")
        korean_lines.append(f"낮은 신뢰도 주장 {low_count}개를 해소하거나 불확실 항목으로 명확히 표시하세요.")
    if missing_url_count:
        original_lines.append(f"Add exact URLs for {missing_url_count} source record(s) before promotion.")
        korean_lines.append(f"노트를 승격하기 전 출처 {missing_url_count}개에 정확한 URL을 추가하세요.")
    if failed_gates:
        original_lines.append(f"Address {len(failed_gates)} failed quality gate(s) before marking the report reviewed.")
        korean_lines.append(f"보고서를 reviewed로 표시하기 전 실패한 품질 게이트 {len(failed_gates)}개를 해결하세요.")

    original = "\n".join(f"- {line}" for line in original_lines)
    korean = "\n".join(f"- {line}" for line in korean_lines)
    return _paired_block(original, korean, bilingual=bilingual)


def _strip_markdown_frontmatter(markdown: str) -> str:
    text = markdown.strip()
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    return text[end + len("\n---") :].strip()


def _demote_markdown_headings(markdown: str, *, levels: int) -> str:
    demoted: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            count = len(line) - len(line.lstrip("#"))
            if count > 0 and count < 6 and line[count : count + 1] == " ":
                demoted.append("#" * min(6, count + levels) + line[count:])
                continue
        demoted.append(line)
    return "\n".join(demoted)


def _wikilink(path: str, vault_path: str) -> str:
    path_obj = Path(path)
    vault = Path(vault_path)
    try:
        relative = path_obj.relative_to(vault).as_posix()
    except ValueError:
        relative = path_obj.as_posix()
    target = relative[:-3] if relative.endswith(".md") else relative
    label = Path(target).stem
    return f"[[{target}|{label}]]"


def _quality_gate_table(gates: list[QualityGateResult]) -> str:
    if not gates:
        return "- No quality gates evaluated."
    rows = [
        "| status | gate | detail |",
        "|---|---|---|",
    ]
    for gate in gates:
        rows.append(f"| {_table_cell(gate.status)} | {_table_cell(gate.name)} | {_table_cell(gate.detail)} |")
    return "\n".join(rows)


def _warning_table(warnings: list[RunWarning]) -> str:
    if not warnings:
        return "- No warnings."
    rows = [
        "| category | source | detail |",
        "|---|---|---|",
    ]
    for warning in warnings:
        rows.append(f"| {_table_cell(warning.category)} | {_table_cell(warning.source)} | {_table_cell(warning.detail)} |")
    return "\n".join(rows)


def _bilingual_block(text: str) -> str:
    original = text.strip() or "No content."
    return f"""**원본**

{original}

**한국어 번역**

{_translate_to_korean(original)}"""


def _claim_lines_bilingual(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return _bilingual_block("- No structured claims extracted yet.")
    lines: list[str] = []
    for claim in claims:
        original = f"{claim.claim_id} ({claim.confidence}, {claim.category}): {claim.claim}"
        lines.append(
            f"- **원본:** {original}\n"
            f"  - **한국어 번역:** {claim.claim_id} ({_translate_to_korean(claim.confidence)}, {_translate_to_korean(claim.category)}): {_translate_to_korean(claim.claim)}"
        )
    return "\n".join(lines)


def _citable_evidence_lines_bilingual(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return ""
    lines: list[str] = []
    for claim in claims:
        if not claim.evidence:
            continue
        lines.append(f"- {claim.claim_id} 원본: {claim.evidence}")
        lines.append(f"  - {claim.claim_id} 한국어 번역: {_translate_to_korean(claim.evidence)}")
    return "\n".join(lines)


def _claim_translation_lines(claims: list[EvidenceClaim]) -> str:
    if not claims:
        return "- None."
    rows = [
        "| claim_id | original claim | Korean translation | original evidence | Korean evidence translation |",
        "|---|---|---|---|---|",
    ]
    for claim in claims:
        rows.append(
            f"| {_table_cell(claim.claim_id)} | {_table_cell(claim.claim)} | {_table_cell(_translate_to_korean(claim.claim))} | "
            f"{_table_cell(claim.evidence)} | {_table_cell(_translate_to_korean(claim.evidence))} |"
        )
    return "\n".join(rows)


def _claim_index_lines_bilingual(
    claims: list[EvidenceClaim],
    *,
    evidence_path: str,
    vault_path: str,
) -> str:
    if not claims:
        return _bilingual_block("- No claims extracted yet.")
    evidence_link = _wikilink(evidence_path, vault_path)
    return "\n".join(
        f"- **원본:** {claim.claim_id}: {claim.claim} ({evidence_link})\n"
        f"  - **한국어 번역:** {claim.claim_id}: {_translate_to_korean(claim.claim)} ({evidence_link})"
        for claim in claims
    )


def _translate_to_korean(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if _has_hangul(stripped):
        return stripped
    if stripped in KO_TRANSLATIONS:
        return KO_TRANSLATIONS[stripped]
    translated_lines = [_translate_line_to_korean(line) for line in stripped.splitlines()]
    return "\n".join(translated_lines)


def _run_lineage_section(rerun_of: str | None) -> str:
    if not rerun_of:
        return ""
    return f"""

## Run Lineage

{_bilingual_block(f"- Re-run of portal job `{rerun_of}`.")}
"""


def _translate_line_to_korean(line: str) -> str:
    if not line.strip():
        return line
    prefix = ""
    body = line.strip()
    if body.startswith("- "):
        prefix = "- "
        body = body[2:].strip()
    elif body.startswith("-> "):
        prefix = "-> "
        body = body[3:].strip()
    else:
        number_prefix = _numbered_prefix(body)
        if number_prefix:
            prefix = number_prefix
            body = body[len(number_prefix) :].strip()
    if body.startswith("```"):
        return prefix + body
    if body in KO_TRANSLATIONS:
        return prefix + KO_TRANSLATIONS[body]
    for pattern, template in KO_PATTERNS:
        match = pattern.match(body)
        if match:
            return prefix + template(**match.groupdict())
    replaced = body
    for source, target in KO_REPLACEMENTS:
        replaced = replaced.replace(source, target)
    if replaced != body:
        return prefix + replaced
    return prefix + f"{body} (한국어 번역 검토 필요)"


def _numbered_prefix(text: str) -> str:
    index = 0
    while index < len(text) and text[index].isdigit():
        index += 1
    if index and index < len(text) and text[index] == ".":
        return text[: index + 1] + " "
    return ""


def _has_hangul(text: str) -> bool:
    return any("가" <= char <= "힣" for char in text)


KO_TRANSLATIONS = {
    "No content.": "내용이 없습니다.",
    "No summary captured yet.": "아직 요약이 수집되지 않았습니다.",
    "No structured claims extracted yet.": "아직 구조화된 주장이 추출되지 않았습니다.",
    "Review this source before using it in a service blueprint.": "이 출처를 실서비스 기본형에 사용하기 전에 검토하세요.",
    "Use the extracted claims as supporting evidence for the service blueprint.": "추출된 주장을 실서비스 기본형의 근거로 사용하세요.",
    "This source note is agent-generated and needs review.": "이 출처 노트는 에이전트가 생성했으며 검토가 필요합니다.",
    "Source URL is missing.": "출처 URL이 누락되었습니다.",
    "This may be a seed domain rather than an exact documentation page.": "정확한 문서 페이지가 아니라 seed 도메인일 수 있습니다.",
    "Paper identity is missing DOI/arXiv metadata.": "논문 식별 정보에 DOI/arXiv 메타데이터가 누락되었습니다.",
    "No structured claim is linked to this source yet.": "아직 이 출처와 연결된 구조화 주장이 없습니다.",
    "Seed official documentation source.": "Seed 공식 문서 출처입니다.",
    "Seed official documentation source. Fetch or search this domain for exact evidence.": "Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
    "Fetch or search this domain for exact evidence.": "정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
    "Seed standards source.": "Seed 표준 출처입니다.",
    "Seed standards source. Use when the topic touches governance, security, risk, or compliance.": "Seed 표준 출처입니다. 주제가 거버넌스, 보안, 위험, 컴플라이언스와 관련될 때 사용하세요.",
    "Use when the topic touches governance, security, risk, or compliance.": "주제가 거버넌스, 보안, 위험, 컴플라이언스와 관련될 때 사용하세요.",
    "No sources collected yet.": "아직 수집된 출처가 없습니다.",
    "Add sources.": "출처를 추가하세요.",
    "None captured yet.": "아직 수집된 항목이 없습니다.",
    "Evidence extraction used fallback source summaries; review each claim before treating it as verified.": "근거 추출에 fallback 출처 요약을 사용했습니다. 각 주장을 검증하기 전에는 확인된 사실로 취급하지 마세요.",
    "Confirm exact official documentation pages instead of relying only on seed domains.": "seed 도메인에만 의존하지 말고 정확한 공식 문서 페이지를 확인하세요.",
    "Confirm paper metadata and DOI/arXiv IDs for all paper claims.": "모든 논문 주장에 대해 논문 메타데이터와 DOI/arXiv ID를 확인하세요.",
    "No quality gates evaluated.": "평가된 품질 게이트가 없습니다.",
    "No warnings.": "경고가 없습니다.",
    "No artifacts generated.": "생성된 산출물이 없습니다.",
    "No claims extracted yet.": "아직 추출된 주장이 없습니다.",
    "No categories extracted yet.": "아직 추출된 분류가 없습니다.",
    "offline": "오프라인",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "question": "질문",
    "medium": "중간",
    "high": "높음",
    "low": "낮음",
    "official-docs": "공식 문서",
    "standards": "표준",
    "papers": "논문",
    "source collection": "출처 수집",
    "structure classification": "구조 분류",
    "service blueprint draft": "실서비스 기본형 초안",
    "Obsidian review": "Obsidian 검토",
    "Use an Obsidian-first workflow where the agent gathers evidence, writes source notes, creates an evidence ledger, and drafts a service blueprint for human review.": "에이전트가 근거를 수집하고, 출처 노트를 작성하고, 근거 장부를 만들고, 사람이 검토할 실서비스 기본형 초안을 작성하는 Obsidian 우선 워크플로를 사용하세요.",
    "When source freshness and traceability matter.": "출처의 최신성과 추적 가능성이 중요할 때.",
    "When official documentation, standards, and papers should outrank general web summaries.": "공식 문서, 표준, 논문이 일반 웹 요약보다 우선되어야 할 때.",
    "When the final artifact must remain readable and editable in Obsidian.": "최종 산출물이 Obsidian에서 읽고 편집 가능한 상태로 남아야 할 때.",
    "Source-first research workflow": "출처 우선 조사 워크플로",
    "Evidence-led synthesis": "근거 기반 종합",
    "Human-reviewed knowledge base": "사람이 검토하는 지식 베이스",
    "Configure the Obsidian vault path.": "Obsidian vault 경로를 설정합니다.",
    "Collect official docs, standards, and paper metadata.": "공식 문서, 표준, 논문 메타데이터를 수집합니다.",
    "Generate source notes and an evidence ledger.": "출처 노트와 근거 장부를 생성합니다.",
    "Use OpenAI synthesis only after local evidence is assembled.": "로컬 근거가 모인 뒤에만 OpenAI 종합을 사용합니다.",
    "Review and promote draft notes inside Obsidian.": "Obsidian 안에서 초안 노트를 검토하고 승격합니다.",
    "Stale documentation": "오래된 문서",
    "Weak source metadata": "부실한 출처 메타데이터",
    "Overwriting reviewed notes": "검토 완료 노트 덮어쓰기",
    "Treating generated synthesis as verified fact": "생성된 종합을 검증된 사실처럼 취급하는 것",
    "Check that every claim has a source URL.": "모든 주장에 출처 URL이 있는지 확인합니다.",
    "Check publication and update dates.": "게시일과 업데이트일을 확인합니다.",
    "Keep uncertain claims in the uncertainty section.": "불확실한 주장은 불확실성 섹션에 남깁니다.",
    "Exact source pages and paper metadata need human review.": "정확한 출처 페이지와 논문 메타데이터는 사람의 검토가 필요합니다.",
    "Open the service blueprint in Obsidian.": "Obsidian에서 실서비스 기본형을 엽니다.",
    "Promote useful notes from draft to reviewed.": "유용한 노트를 draft에서 reviewed 상태로 승격합니다.",
    "Add exact citations where seed sources need deeper fetching.": "seed 출처에 더 깊은 수집이 필요한 곳에는 정확한 인용을 추가합니다.",
    "Link the service blueprint back to this topic map after review.": "검토 후 실서비스 기본형을 이 topic map에 다시 연결합니다.",
    "Link reviewed source notes to the evidence ledger.": "검토된 출처 노트를 근거 장부에 연결합니다.",
    "Promote durable categories into taxonomy notes under `20_Taxonomy`.": "오래 유지될 분류를 `20_Taxonomy` 아래 taxonomy note로 승격합니다.",
    "- Start here as the human-readable final report.\n- Use the Evidence Ledger for claim-level verification.\n- Use the Run Note for quality gates, warnings, and generated artifact history.\n- Promote this note from draft to reviewed after checking weak sources.": "- 사람이 읽는 최종 보고서는 여기에서 시작하세요.\n- 주장 단위 검증은 Evidence Ledger를 사용하세요.\n- 품질 게이트, 경고, 생성 산출물 이력은 Run Note를 사용하세요.\n- 약한 출처를 확인한 뒤 이 노트를 draft에서 reviewed 상태로 승격하세요.",
    "- Review linked source notes.\n- Confirm citations for low-confidence or fallback claims.\n- Keep unresolved issues in the source notes and Evidence Ledger before promoting this report.": "- 연결된 출처 노트를 검토하세요.\n- 신뢰도가 낮거나 fallback으로 생성된 주장에 대한 인용을 확인하세요.\n- 이 보고서를 승격하기 전에 미해결 이슈를 출처 노트와 Evidence Ledger에 남기세요.",
    "By creating an OpenAI account and securing an API key, users can begin building customized AI assistants tailored to their unique goals—whether for personal productivity, lifestyle tasks, or business use.": "OpenAI 계정을 만들고 API 키를 확보하면, 사용자는 개인 생산성, 생활 업무, 비즈니스 활용 등 고유한 목표에 맞춘 맞춤형 AI 어시스턴트 구축을 시작할 수 있습니다.",
    "LangGraph is a popular open source framework—created by LangChain—that helps developers use large language models (LLMs) to build sophisticated, stateful, and multi-actor applications.": "LangGraph는 LangChain이 만든 인기 있는 오픈소스 프레임워크로, 개발자가 대규모 언어 모델(LLM)을 사용해 정교하고 상태를 유지하는 다중 행위자 애플리케이션을 구축하도록 돕습니다.",
}


KO_PATTERNS = [
    (
        re.compile(r"^Re-run of portal job `(?P<job_id>[^`]+)`\.$"),
        lambda job_id: f"포털 작업 `{job_id}`의 재실행입니다.",
    ),
    (
        re.compile(r"^(?P<claim_id>[A-Z]\d{3,}) \((?P<confidence>[^,]+), (?P<category>[^)]+)\): (?P<claim>.+)$"),
        lambda claim_id, confidence, category, claim: (
            f"{claim_id} ({_translate_to_korean(confidence)}, {_translate_to_korean(category)}): {_translate_to_korean(claim)}"
        ),
    ),
    (
        re.compile(r"^(?P<claim_id>[A-Z]\d{3,}): (?P<evidence>.+)$"),
        lambda claim_id, evidence: f"{claim_id}: {_translate_to_korean(evidence)}",
    ),
    (
        re.compile(r"^Source: (?P<url>https?://\S+)$"),
        lambda url: f"출처: {url}",
    ),
    (
        re.compile(r"^Source URL: (?P<url>https?://\S+)$"),
        lambda url: f"출처 URL: {url}",
    ),
    (
        re.compile(r"^Official documentation candidate for (?P<topic>.+)$"),
        lambda topic: f"{topic} 공식 문서 후보",
    ),
    (
        re.compile(r"^Standards or security framework candidate for (?P<topic>.+)$"),
        lambda topic: f"{topic} 표준 또는 보안 프레임워크 후보",
    ),
    (
        re.compile(r"^Review (?P<title>.+) for topic relevance\.$"),
        lambda title: f"{title}의 주제 관련성을 검토하세요.",
    ),
    (
        re.compile(r"^Supports `(?P<category>.+)` decisions\.$"),
        lambda category: f"`{_translate_to_korean(category)}` 관련 결정을 뒷받침합니다.",
    ),
    (
        re.compile(r"^Add sources for (?P<topic>.+)\.$"),
        lambda topic: f"{topic}에 대한 출처를 추가하세요.",
    ),
]


KO_REPLACEMENTS = [
    ("open source", "오픈소스"),
    ("official documentation", "공식 문서"),
    ("standards", "표준"),
    ("papers", "논문"),
    ("collection", "수집"),
    ("classification", "분류"),
    ("exact evidence", "정확한 근거"),
    ("Fetch or search this domain", "이 도메인에서 문서를 가져오거나 검색"),
    ("evidence ledger", "근거 장부"),
    ("service blueprint", "실서비스 기본형"),
    ("source notes", "출처 노트"),
    ("source note", "출처 노트"),
    ("source", "출처"),
    ("evidence", "근거"),
    ("review", "검토"),
    ("draft", "초안"),
    ("claims", "주장"),
    ("claim", "주장"),
    ("metadata", "메타데이터"),
    ("official-docs", "공식 문서"),
    ("standards", "표준"),
    ("papers", "논문"),
]
