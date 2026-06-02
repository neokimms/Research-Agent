from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .obsidian import ObsidianWriter, REVIEWED_STATUSES
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


BLUEPRINT_REFRESH_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>B\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)
LEDGER_ROW_RE = re.compile(r"^\|\s*(?P<claim_id>E\d{3,})\s*\|")
H2_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")

REFRESH_SECTIONS = [
    "One-Line Conclusion",
    "When To Use",
    "Structure Classification",
    "Recommended Baseline",
    "Implementation Order",
    "Operational Risks",
    "Verification",
    "Still Uncertain",
]


@dataclass(frozen=True)
class LedgerClaim:
    claim_id: str
    claim: str
    source: str
    source_type: str
    checked_at: str
    confidence: str
    note: str


@dataclass(frozen=True)
class BlueprintRefreshCandidate:
    proposal_id: str
    topic: str
    blueprint_path: Path
    blueprint_relative_path: str
    ledger_path: Path
    ledger_relative_path: str
    fingerprint: str
    evidence_counts: dict[str, int]
    section_updates: dict[str, str]


@dataclass(frozen=True)
class BlueprintRefreshResult:
    vault_path: Path
    blueprints_scanned: int
    evidence_ledgers_scanned: int
    candidates: list[BlueprintRefreshCandidate]
    warnings: list[str]


@dataclass(frozen=True)
class BlueprintRefreshWriteResult:
    result: BlueprintRefreshResult
    note_path: Path


@dataclass(frozen=True)
class BlueprintRefreshApplyItem:
    proposal_path: Path
    proposal_id: str
    topic: str
    blueprint_path: Path
    sections: list[str]


@dataclass(frozen=True)
class BlueprintRefreshSkippedItem:
    proposal_path: Path
    proposal_id: str
    topic: str
    reason: str


@dataclass(frozen=True)
class BlueprintRefreshApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[BlueprintRefreshApplyItem]
    updated_paths: list[Path]
    skipped_items: list[BlueprintRefreshSkippedItem]


def build_blueprint_refresh(settings: Settings, *, max_proposals: int = 50) -> BlueprintRefreshResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    ledgers = _ledger_snapshots(vault)
    candidates: list[BlueprintRefreshCandidate] = []
    warnings: list[str] = []
    blueprints_scanned = 0

    for blueprint_path in _markdown_files(vault):
        text = blueprint_path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "service-blueprint":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        blueprints_scanned += 1
        status = _frontmatter_scalar(frontmatter, "status").lower()
        if status in REVIEWED_STATUSES:
            warnings.append(f"Skipped protected service blueprint: {blueprint_path.relative_to(vault).as_posix()}")
            continue
        topic = _frontmatter_scalar(frontmatter, "topic") or _heading_topic(body)
        claims = ledgers.get(topic)
        if not claims:
            warnings.append(f"No evidence ledger found for service blueprint topic: {topic}")
            continue
        fingerprint = _fingerprint(topic, claims)
        if _frontmatter_scalar(frontmatter, "blueprint_refresh_fingerprint") == fingerprint:
            continue
        section_updates = _section_updates(topic, claims)
        differing = {
            section: content
            for section, content in section_updates.items()
            if _section_content(body, section).strip() != content.strip()
        }
        if not differing:
            continue
        proposal_id = f"B{len(candidates) + 1:03d}"
        ledger_path = _ledger_path_for_topic(vault, topic)
        if ledger_path is None:
            warnings.append(f"No evidence ledger path found for service blueprint topic: {topic}")
            continue
        candidates.append(
            BlueprintRefreshCandidate(
                proposal_id=proposal_id,
                topic=topic,
                blueprint_path=blueprint_path,
                blueprint_relative_path=blueprint_path.relative_to(vault).as_posix(),
                ledger_path=ledger_path,
                ledger_relative_path=ledger_path.relative_to(vault).as_posix(),
                fingerprint=fingerprint,
                evidence_counts=_evidence_counts(claims),
                section_updates=differing,
            )
        )
        if len(candidates) >= max_proposals:
            break

    return BlueprintRefreshResult(
        vault_path=vault,
        blueprints_scanned=blueprints_scanned,
        evidence_ledgers_scanned=len(ledgers),
        candidates=candidates,
        warnings=warnings,
    )


def render_blueprint_refresh(result: BlueprintRefreshResult, *, max_proposals: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Blueprint Refresh

Vault: {result.vault_path}
Service blueprints scanned: {result.blueprints_scanned}
Evidence ledgers scanned: {result.evidence_ledgers_scanned}
Blueprint refresh candidates: {len(result.candidates)}
Warnings: {len(result.warnings)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Warnings:
{_warning_lines(result.warnings)}
"""


def write_blueprint_refresh_note(settings: Settings, *, max_proposals: int = 50) -> BlueprintRefreshWriteResult:
    result = build_blueprint_refresh(settings, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_blueprint-refresh.md",
        render_blueprint_refresh_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return BlueprintRefreshWriteResult(result=result, note_path=path)


def apply_blueprint_refresh(settings: Settings, *, dry_run: bool = True, applied_at: str = "") -> BlueprintRefreshApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[BlueprintRefreshApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[BlueprintRefreshSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _blueprint_refresh_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(BlueprintRefreshSkippedItem(proposal_path, proposal_id, "", "candidate record not found"))
                continue
            if not candidate.blueprint_path.exists():
                skipped_items.append(BlueprintRefreshSkippedItem(proposal_path, proposal_id, candidate.topic, "service blueprint not found"))
                continue
            blueprint_text = candidate.blueprint_path.read_text(encoding="utf-8", errors="replace")
            frontmatter, _body = _split_frontmatter(blueprint_text)
            status = _frontmatter_scalar(frontmatter, "status").lower()
            if status in REVIEWED_STATUSES:
                skipped_items.append(BlueprintRefreshSkippedItem(proposal_path, proposal_id, candidate.topic, "protected reviewed/evergreen service blueprint"))
                continue
            if _frontmatter_scalar(frontmatter, "blueprint_refresh_fingerprint") == candidate.fingerprint:
                skipped_items.append(BlueprintRefreshSkippedItem(proposal_path, proposal_id, candidate.topic, "blueprint already refreshed"))
                continue
            updated = _apply_sections(blueprint_text, candidate.section_updates)
            updated = _set_frontmatter_scalars(
                updated,
                {
                    "blueprint_refresh_provider": "deterministic-evidence-ledger",
                    "blueprint_refresh_fingerprint": candidate.fingerprint,
                    "blueprint_refreshed_at": applied_at,
                },
            )
            if updated == blueprint_text:
                skipped_items.append(BlueprintRefreshSkippedItem(proposal_path, proposal_id, candidate.topic, "no section changes detected"))
                continue
            if not dry_run:
                candidate.blueprint_path.write_text(updated, encoding="utf-8")
            approved_items.append(
                BlueprintRefreshApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    topic=candidate.topic,
                    blueprint_path=candidate.blueprint_path,
                    sections=list(candidate.section_updates.keys()),
                )
            )
            updated_paths.append(candidate.blueprint_path)
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return BlueprintRefreshApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_blueprint_refresh_apply_result(result: BlueprintRefreshApplyResult) -> str:
    title = "Blueprint Refresh Apply Dry Run" if result.dry_run else "Blueprint Refresh Apply"
    action = "Would update notes" if result.dry_run else "Updated notes"
    return f"""{title}

Proposal notes scanned: {result.proposal_notes}
Approved checklist items: {len(result.approved_items)}
{action}: {len(result.updated_paths)}
Skipped items: {len(result.skipped_items)}

Approved items:
{_apply_item_lines(result.approved_items)}

Updated notes:
{_path_lines(result.updated_paths)}

Skipped items:
{_skipped_item_lines(result.skipped_items)}
"""


def render_blueprint_refresh_note(
    result: BlueprintRefreshResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("blueprint-refresh")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
blueprint_count: {result.blueprints_scanned}
evidence_ledger_count: {result.evidence_ledgers_scanned}
proposal_count: {len(result.candidates)}
warning_count: {len(result.warnings)}
---
# Blueprint Refresh

## Summary

| metric | value |
|---|---:|
| service blueprints scanned | {result.blueprints_scanned} |
| evidence ledgers scanned | {result.evidence_ledgers_scanned} |
| blueprint refresh candidates | {len(result.candidates)} |
| warnings | {len(result.warnings)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Warnings

{_warning_lines(result.warnings)}

## Review Checklist

- [ ] Confirm refreshed blueprint sections reflect the current evidence ledger.
- [ ] Apply accepted blueprint updates with `apply-blueprint-refresh`.
- [ ] Rerun `source-audit` and `bilingual-audit`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _ledger_snapshots(vault: Path) -> dict[str, list[LedgerClaim]]:
    ledgers: dict[str, list[LedgerClaim]] = {}
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "evidence-ledger":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        topic = _frontmatter_scalar(frontmatter, "topic") or _heading_topic(body)
        if topic:
            ledgers[topic] = _ledger_claims(body)
    return ledgers


def _ledger_path_for_topic(vault: Path, topic: str) -> Path | None:
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "evidence-ledger":
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        if _frontmatter_scalar(frontmatter, "topic") == topic:
            return path
    return None


def _ledger_claims(body: str) -> list[LedgerClaim]:
    claims: list[LedgerClaim] = []
    for line in body.splitlines():
        if not LEDGER_ROW_RE.match(line):
            continue
        cells = _table_cells(line)
        if len(cells) < 7:
            continue
        claims.append(
            LedgerClaim(
                claim_id=cells[0],
                claim=cells[1],
                source=cells[2],
                source_type=cells[3],
                checked_at=cells[4],
                confidence=cells[5],
                note=cells[6],
            )
        )
    return claims


def _table_cells(line: str) -> list[str]:
    raw = line.strip().strip("|").split("|")
    return [cell.replace("\\|", "|").strip() for cell in raw]


def _section_updates(topic: str, claims: list[LedgerClaim]) -> dict[str, str]:
    counts = _evidence_counts(claims)
    has_openai = any("OpenAI" in claim.claim or "OpenAI" in claim.source for claim in claims)
    has_langgraph = any("LangGraph" in claim.claim or "LangGraph" in claim.source for claim in claims)
    has_standards = counts.get("standards", 0) > 0
    has_papers = counts.get("papers", 0) > 0

    conclusion = (
        "Use OpenAI managed assistant patterns for faster customized assistant delivery, "
        "use LangGraph when stateful or multi-actor orchestration is required, and keep the decision evidence-led through Obsidian."
        if has_openai and has_langgraph
        else "Choose the implementation pattern only after checking official documentation, standards, and paper evidence in the Obsidian evidence ledger."
    )
    conclusion_ko = (
        "빠른 맞춤형 어시스턴트 제공에는 OpenAI 관리형 assistant 패턴을 사용하고, 상태 유지 또는 다중 행위자 오케스트레이션이 필요하면 LangGraph를 사용하며, Obsidian 근거 장부로 의사결정을 추적하세요."
        if has_openai and has_langgraph
        else "Obsidian 근거 장부의 공식 문서, 표준, 논문 근거를 확인한 뒤 구현 패턴을 선택하세요."
    )

    when_to_use = [
        "When the team must compare managed assistant APIs with graph-based agent orchestration.",
        "When source traceability, governance, and security review matter before implementation.",
    ]
    when_to_use_ko = [
        "관리형 assistant API와 그래프 기반 에이전트 오케스트레이션을 비교해야 할 때.",
        "구현 전에 출처 추적성, 거버넌스, 보안 검토가 중요할 때.",
    ]
    if has_papers:
        when_to_use.append("When paper or book-chapter evidence should support framework positioning, not replace official docs.")
        when_to_use_ko.append("논문 또는 책 챕터 근거가 공식 문서를 대체하지 않고 프레임워크 포지셔닝을 보강해야 할 때.")

    classifications = ["Managed assistant platform pattern", "Stateful graph orchestration pattern", "Evidence-led governance review pattern"]
    classifications_ko = ["관리형 assistant 플랫폼 패턴", "상태 기반 그래프 오케스트레이션 패턴", "근거 기반 거버넌스 검토 패턴"]

    baseline = """```text
question
-> official docs / standards / paper evidence
-> evidence ledger
-> choose managed assistant vs graph orchestration
-> prototype with verification gates
-> Obsidian review and promotion
```"""
    baseline_ko = """```text
질문
-> 공식 문서 / 표준 / 논문 근거
-> 근거 장부
-> 관리형 assistant와 그래프 오케스트레이션 중 선택
-> 검증 게이트가 있는 프로토타입
-> Obsidian 검토와 승격
```"""

    implementation = [
        "1. Confirm the user journey, tool boundary, memory/state needs, and data access constraints.",
        "2. Start with official documentation evidence for OpenAI and LangGraph capabilities.",
        "3. Use standards evidence to define risk, AI management, and LLM application security controls.",
        "4. Use paper evidence to support positioning and architecture tradeoffs after official docs are grounded.",
        "5. Prototype the smallest reversible baseline, then promote reviewed notes in Obsidian.",
    ]
    implementation_ko = [
        "1. 사용자 여정, 도구 경계, 메모리/상태 요구, 데이터 접근 제약을 확인합니다.",
        "2. OpenAI와 LangGraph 기능은 공식 문서 근거부터 확인합니다.",
        "3. 표준 근거로 위험, AI 관리, LLM 애플리케이션 보안 통제를 정의합니다.",
        "4. 공식 문서가 정리된 뒤 논문 근거로 포지셔닝과 아키텍처 tradeoff를 보강합니다.",
        "5. 되돌리기 쉬운 최소 기준형을 프로토타입으로 만들고, 검토된 노트를 Obsidian에서 승격합니다.",
    ]

    risks = [
        "Treating generated synthesis as verified fact.",
        "Using paper metadata without checking whether it is a paper, book chapter, or secondary source.",
        "Choosing LangGraph or OpenAI assistants before confirming state, tool, and governance requirements.",
    ]
    risks_ko = [
        "생성된 종합을 검증된 사실처럼 취급하는 것.",
        "논문, 책 챕터, 2차 출처 여부를 확인하지 않고 논문 메타데이터를 사용하는 것.",
        "상태, 도구, 거버넌스 요구를 확인하기 전에 LangGraph나 OpenAI assistants를 선택하는 것.",
    ]
    if has_standards:
        risks.append("Skipping NIST AI RMF, ISO/IEC 42001, or OWASP LLM risk checks before service use.")
        risks_ko.append("실서비스 사용 전에 NIST AI RMF, ISO/IEC 42001, OWASP LLM 위험 점검을 건너뛰는 것.")

    verification = [
        "Check every important claim against the evidence ledger.",
        "Confirm source type balance: official docs first, standards for controls, papers for context.",
        "Re-run source and bilingual audits after downstream updates.",
    ]
    verification_ko = [
        "중요한 모든 주장을 근거 장부와 대조합니다.",
        "출처 유형의 균형을 확인합니다. 공식 문서를 우선하고, 표준은 통제에, 논문은 맥락에 사용합니다.",
        "downstream 업데이트 후 source audit과 bilingual audit을 다시 실행합니다.",
    ]

    uncertain = [
        "Paper evidence is connected, but human review should confirm whether these book-chapter sources are strong enough for production decisions.",
        "Provider APIs and framework capabilities may change; verify exact current docs before implementation.",
    ]
    uncertain_ko = [
        "논문 근거는 연결되었지만, 이 책 챕터 출처가 프로덕션 의사결정에 충분히 강한 근거인지 사람의 검토가 필요합니다.",
        "Provider API와 프레임워크 기능은 바뀔 수 있으므로 구현 전에 최신 공식 문서를 확인해야 합니다.",
    ]

    return {
        "One-Line Conclusion": _bilingual(conclusion, conclusion_ko),
        "When To Use": _bilingual(_bullet_lines(when_to_use), _bullet_lines(when_to_use_ko)),
        "Structure Classification": _bilingual(_bullet_lines(classifications), _bullet_lines(classifications_ko)),
        "Recommended Baseline": _bilingual(baseline, baseline_ko),
        "Implementation Order": _bilingual("\n".join(implementation), "\n".join(implementation_ko)),
        "Operational Risks": _bilingual(_bullet_lines(risks), _bullet_lines(risks_ko)),
        "Verification": _bilingual(_bullet_lines(verification), _bullet_lines(verification_ko)),
        "Still Uncertain": _bilingual(_bullet_lines(uncertain), _bullet_lines(uncertain_ko)),
    }


def _evidence_counts(claims: list[LedgerClaim]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for claim in claims:
        source_type = claim.source_type or "unknown"
        counts[source_type] = counts.get(source_type, 0) + 1
    return dict(sorted(counts.items()))


def _fingerprint(topic: str, claims: list[LedgerClaim]) -> str:
    payload = {
        "topic": topic,
        "claims": [
            {
                "claim_id": claim.claim_id,
                "claim": claim.claim,
                "source": claim.source,
                "source_type": claim.source_type,
                "checked_at": claim.checked_at,
            }
            for claim in claims
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _section_content(body: str, section: str) -> str:
    pattern = re.compile(rf"(?m)^## {re.escape(section)}\s*$")
    match = pattern.search(body)
    if not match:
        return ""
    start = body.find("\n", match.end())
    start = len(body) if start == -1 else start + 1
    next_match = H2_RE.search(body, start)
    end = next_match.start() if next_match else len(body)
    return body[start:end].strip()


def _apply_sections(text: str, section_updates: dict[str, str]) -> str:
    updated = text
    for section, content in section_updates.items():
        updated = _replace_section(updated, section, content)
    return updated


def _replace_section(text: str, section: str, content: str) -> str:
    pattern = re.compile(rf"(?m)^## {re.escape(section)}\s*$")
    match = pattern.search(text)
    block = f"## {section}\n\n{content.strip()}\n"
    if not match:
        return text.rstrip() + "\n\n" + block
    start = match.start()
    content_start = text.find("\n", match.end())
    content_start = len(text) if content_start == -1 else content_start + 1
    next_match = H2_RE.search(text, content_start)
    end = next_match.start() if next_match else len(text)
    return text[:start] + block + "\n" + text[end:].lstrip("\n")


def _candidate_json(candidates: list[BlueprintRefreshCandidate]) -> str:
    # Keep JSON inside a Markdown code fence even when section content contains fenced code blocks.
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2).replace(
        "```",
        "\\u0060\\u0060\\u0060",
    )


def _candidate_to_json(candidate: BlueprintRefreshCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "topic": candidate.topic,
        "blueprint_path": candidate.blueprint_relative_path,
        "ledger_path": candidate.ledger_relative_path,
        "fingerprint": candidate.fingerprint,
        "evidence_counts": candidate.evidence_counts,
        "section_updates": candidate.section_updates,
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, BlueprintRefreshCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, BlueprintRefreshCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> BlueprintRefreshCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    topic = str(item.get("topic") or "").strip()
    blueprint_path = _vault_path(vault, str(item.get("blueprint_path") or ""))
    ledger_path = _vault_path(vault, str(item.get("ledger_path") or ""))
    section_updates = item.get("section_updates")
    evidence_counts = item.get("evidence_counts")
    if not proposal_id or not topic or blueprint_path is None or ledger_path is None or not isinstance(section_updates, dict):
        return None
    return BlueprintRefreshCandidate(
        proposal_id=proposal_id,
        topic=topic,
        blueprint_path=blueprint_path,
        blueprint_relative_path=blueprint_path.relative_to(vault).as_posix(),
        ledger_path=ledger_path,
        ledger_relative_path=ledger_path.relative_to(vault).as_posix(),
        fingerprint=str(item.get("fingerprint") or "").strip(),
        evidence_counts={str(key): int(value) for key, value in (evidence_counts or {}).items()} if isinstance(evidence_counts, dict) else {},
        section_updates={str(key): str(value) for key, value in section_updates.items() if str(key) in REFRESH_SECTIONS},
    )


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in BLUEPRINT_REFRESH_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _blueprint_refresh_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "blueprint-refresh":
            paths.append(path)
    return sorted(paths)


def _proposal_lines(candidates: list[BlueprintRefreshCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: BlueprintRefreshCandidate) -> str:
    sections = ", ".join(candidate.section_updates.keys())
    counts = ", ".join(f"{key}: {value}" for key, value in candidate.evidence_counts.items())
    return f"- [ ] {candidate.proposal_id} Refresh [{candidate.topic}]({candidate.blueprint_relative_path}) sections: {sections} (evidence: {counts})"


def _apply_item_lines(items: list[BlueprintRefreshApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.topic}: {', '.join(item.sections)} -> {item.blueprint_path}" for item in items)


def _skipped_item_lines(items: list[BlueprintRefreshSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.topic}: {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _warning_lines(warnings: list[str]) -> str:
    if not warnings:
        return "- None."
    return "\n".join(f"- {warning}" for warning in warnings)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _bilingual(original: str, korean: str) -> str:
    return f"""**원본**

{original.strip()}

**한국어 번역**

{korean.strip()}"""


def _bullet_lines(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _heading_topic(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].replace(" Service Blueprint", "").replace("Evidence Ledger:", "").strip()
    return ""


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate
