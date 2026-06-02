from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .bilingual_audit import run_bilingual_audit
from .blueprint import REQUIRED_BLUEPRINT_SECTIONS
from .config import Settings
from .obsidian import ObsidianWriter, REVIEWED_STATUSES
from .source_audit import run_source_audit
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


PROMOTABLE_TYPES = {"source-note", "evidence-ledger", "service-blueprint", "topic-map"}
PROMOTION_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>R\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)
LEDGER_ROW_RE = re.compile(r"(?m)^\|\s*E\d{3,}\s*\|")


@dataclass(frozen=True)
class ReviewPromotionCandidate:
    proposal_id: str
    path: Path
    relative_path: str
    note_type: str
    topic: str
    current_status: str
    basis: list[str]


@dataclass(frozen=True)
class ReviewPromotionSkippedItem:
    path: Path
    relative_path: str
    note_type: str
    topic: str
    status: str
    reason: str


@dataclass(frozen=True)
class ReviewPromotionResult:
    vault_path: Path
    notes_scanned: int
    candidates: list[ReviewPromotionCandidate]
    skipped_items: list[ReviewPromotionSkippedItem]


@dataclass(frozen=True)
class ReviewPromotionWriteResult:
    result: ReviewPromotionResult
    note_path: Path


@dataclass(frozen=True)
class ReviewPromotionApplyItem:
    proposal_path: Path
    proposal_id: str
    path: Path
    relative_path: str
    note_type: str
    topic: str


@dataclass(frozen=True)
class ReviewPromotionApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[ReviewPromotionApplyItem]
    updated_paths: list[Path]
    skipped_items: list[ReviewPromotionSkippedItem]


def build_review_promotion(settings: Settings, *, max_proposals: int = 50) -> ReviewPromotionResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    candidates: list[ReviewPromotionCandidate] = []
    skipped_items: list[ReviewPromotionSkippedItem] = []
    notes_scanned = 0

    for path in sorted(_markdown_files(vault), key=lambda item: item.relative_to(vault).as_posix()):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        note_type = _frontmatter_scalar(frontmatter, "type")
        if note_type not in PROMOTABLE_TYPES:
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        notes_scanned += 1
        relative_path = path.relative_to(vault).as_posix()
        topic = _frontmatter_scalar(frontmatter, "topic") or _heading_topic(body)
        status = _frontmatter_scalar(frontmatter, "status") or "missing"
        eligible, basis_or_reasons = _promotion_check(vault, path, note_type=note_type, text=text, frontmatter=frontmatter, body=body)
        if status.lower() in REVIEWED_STATUSES:
            skipped_items.append(ReviewPromotionSkippedItem(path, relative_path, note_type, topic, status, "already reviewed/evergreen"))
            continue
        if status.lower() != "draft":
            skipped_items.append(ReviewPromotionSkippedItem(path, relative_path, note_type, topic, status, f"status is not draft: {status}"))
            continue
        if not eligible:
            skipped_items.append(ReviewPromotionSkippedItem(path, relative_path, note_type, topic, status, "; ".join(basis_or_reasons)))
            continue
        proposal_id = f"R{len(candidates) + 1:03d}"
        candidates.append(
            ReviewPromotionCandidate(
                proposal_id=proposal_id,
                path=path,
                relative_path=relative_path,
                note_type=note_type,
                topic=topic,
                current_status=status,
                basis=basis_or_reasons,
            )
        )
        if len(candidates) >= max_proposals:
            break

    return ReviewPromotionResult(
        vault_path=vault,
        notes_scanned=notes_scanned,
        candidates=candidates,
        skipped_items=skipped_items,
    )


def render_review_promotion(result: ReviewPromotionResult, *, max_proposals: int = 50, max_skipped: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    shown_skipped = result.skipped_items[:max_skipped]
    hidden_skipped = len(result.skipped_items) - len(shown_skipped)
    return f"""Review Promotion Proposals

Vault: {result.vault_path}
Generated draft notes scanned: {result.notes_scanned}
Promotion candidates: {len(result.candidates)}
Skipped notes: {len(result.skipped_items)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Skipped:
{_skipped_lines(shown_skipped)}
{_hidden_skipped_line(hidden_skipped, max_skipped)}
"""


def write_review_promotion_note(settings: Settings, *, max_proposals: int = 50) -> ReviewPromotionWriteResult:
    result = build_review_promotion(settings, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_review-promotion.md",
        render_review_promotion_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return ReviewPromotionWriteResult(result=result, note_path=path)


def apply_review_promotion(settings: Settings, *, dry_run: bool = True, reviewed_at: str = "") -> ReviewPromotionApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[ReviewPromotionApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[ReviewPromotionSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _review_promotion_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(ReviewPromotionSkippedItem(proposal_path, proposal_path.relative_to(vault).as_posix(), "review-promotion", "", "", "candidate record not found"))
                continue
            if not candidate.path.exists():
                skipped_items.append(_skip_from_candidate(candidate, "note not found"))
                continue
            current_text = candidate.path.read_text(encoding="utf-8", errors="replace")
            frontmatter, body = _split_frontmatter(current_text)
            status = _frontmatter_scalar(frontmatter, "status")
            if status.lower() in REVIEWED_STATUSES:
                skipped_items.append(_skip_from_candidate(candidate, "already reviewed/evergreen"))
                continue
            if status.lower() != "draft":
                skipped_items.append(_skip_from_candidate(candidate, f"status is not draft: {status or 'missing'}"))
                continue
            eligible, reasons = _promotion_check(vault, candidate.path, note_type=candidate.note_type, text=current_text, frontmatter=frontmatter, body=body)
            if not eligible:
                skipped_items.append(_skip_from_candidate(candidate, "; ".join(reasons)))
                continue
            values = {
                "status": "reviewed",
                "reviewed_by": "research-agent",
                "review_basis": "source-audit/bilingual-audit/structural-checks",
            }
            if reviewed_at:
                values["reviewed_at"] = reviewed_at
            updated = _set_frontmatter_scalars(current_text, values)
            if not dry_run:
                candidate.path.write_text(updated, encoding="utf-8")
            approved_items.append(
                ReviewPromotionApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=candidate.proposal_id,
                    path=candidate.path,
                    relative_path=candidate.relative_path,
                    note_type=candidate.note_type,
                    topic=candidate.topic,
                )
            )
            updated_paths.append(candidate.path)
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if reviewed_at:
                values["applied_at"] = reviewed_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return ReviewPromotionApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_review_promotion_apply_result(result: ReviewPromotionApplyResult) -> str:
    title = "Review Promotion Apply Dry Run" if result.dry_run else "Review Promotion Apply"
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
{_skipped_lines(result.skipped_items)}
"""


def render_review_promotion_note(
    result: ReviewPromotionResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("review-promotion")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
notes_scanned: {result.notes_scanned}
proposal_count: {len(result.candidates)}
skipped_count: {len(result.skipped_items)}
---
# Review Promotion

## Summary

| metric | value |
|---|---:|
| generated draft notes scanned | {result.notes_scanned} |
| promotion candidates | {len(result.candidates)} |
| skipped notes | {len(result.skipped_items)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Skipped

{_skipped_lines(result.skipped_items)}

## Review Checklist

- [ ] Confirm each candidate is ready to be promoted from draft to reviewed.
- [ ] Apply accepted promotions with `apply-review-promotion`.
- [ ] Rerun `source-audit` and `bilingual-audit`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _promotion_check(
    vault: Path,
    path: Path,
    *,
    note_type: str,
    text: str,
    frontmatter: dict[str, str | list[str]],
    body: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    bilingual = run_bilingual_audit(vault, target_paths=[path])
    if bilingual.issues:
        reasons.append(f"bilingual audit issues: {_issue_summary(bilingual.issues)}")
    else:
        reasons.append("bilingual audit clean")

    if note_type == "source-note":
        source = run_source_audit(vault, target_paths=[path])
        if source.issues:
            reasons.append(f"source audit issues: {_issue_summary(source.issues)}")
        else:
            reasons.append("source audit clean")

    structural_issues = _structural_issues(note_type, text=text, frontmatter=frontmatter, body=body)
    if structural_issues:
        reasons.append("structural issues: " + "; ".join(structural_issues))
    else:
        reasons.append("structural checks clean")

    eligible = not any("issues:" in reason for reason in reasons)
    return eligible, reasons


def _structural_issues(note_type: str, *, text: str, frontmatter: dict[str, str | list[str]], body: str) -> list[str]:
    issues: list[str] = []
    if note_type == "evidence-ledger":
        if not LEDGER_ROW_RE.search(body):
            issues.append("no evidence ledger claim rows")
        if _has_paper_rows(body) and "No paper sources were collected" in body:
            issues.append("stale paper verification text remains")
    elif note_type == "service-blueprint":
        for section in REQUIRED_BLUEPRINT_SECTIONS:
            if not re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", body):
                issues.append(f"missing section: {section}")
        if "TBD after reviewing the evidence ledger" in body:
            issues.append("TBD placeholder remains")
        if "paper metadata still needs review" in body:
            issues.append("stale paper metadata uncertainty remains")
    elif note_type == "topic-map":
        for section in ["Core Notes", "Source Notes", "Claim Index"]:
            if not re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", body):
                issues.append(f"missing section: {section}")
        if "No claims extracted yet" in body:
            issues.append("claim index placeholder remains")
    elif note_type == "source-note":
        if not _frontmatter_scalar(frontmatter, "source_id"):
            issues.append("missing source_id")
        if "Crossref metadata record." in body:
            issues.append("generic paper metadata claim remains")
    return issues


def _candidate_json(candidates: list[ReviewPromotionCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: ReviewPromotionCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "path": candidate.relative_path,
        "note_type": candidate.note_type,
        "topic": candidate.topic,
        "current_status": candidate.current_status,
        "basis": candidate.basis,
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, ReviewPromotionCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, ReviewPromotionCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> ReviewPromotionCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    relative_path = str(item.get("path") or "").strip()
    path = _vault_path(vault, relative_path)
    note_type = str(item.get("note_type") or "").strip()
    if not proposal_id or path is None or note_type not in PROMOTABLE_TYPES:
        return None
    basis = item.get("basis")
    return ReviewPromotionCandidate(
        proposal_id=proposal_id,
        path=path,
        relative_path=relative_path,
        note_type=note_type,
        topic=str(item.get("topic") or "").strip(),
        current_status=str(item.get("current_status") or "").strip(),
        basis=[str(value) for value in basis] if isinstance(basis, list) else [],
    )


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in PROMOTION_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _review_promotion_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "review-promotion":
            paths.append(path)
    return sorted(paths)


def _proposal_lines(candidates: list[ReviewPromotionCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: ReviewPromotionCandidate) -> str:
    topic = f", topic: {candidate.topic}" if candidate.topic else ""
    basis = "; ".join(candidate.basis)
    return f"- [ ] {candidate.proposal_id} Promote [{candidate.relative_path}]({candidate.relative_path}) ({candidate.note_type}{topic}); basis: {basis}"


def _apply_item_lines(items: list[ReviewPromotionApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.relative_path} ({item.note_type}, topic: {item.topic})" for item in items)


def _skipped_lines(items: list[ReviewPromotionSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.relative_path} ({item.note_type}, status: {item.status or 'unknown'}): {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _hidden_skipped_line(hidden: int, max_skipped: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more skipped note(s) hidden by --max-skipped={max_skipped}."


def _issue_summary(issues: list[object]) -> str:
    parts = []
    for issue in issues[:3]:
        check = getattr(issue, "check", "unknown")
        detail = getattr(issue, "detail", "")
        parts.append(f"{check}: {detail}")
    hidden = len(issues) - len(parts)
    suffix = f"; {hidden} more" if hidden > 0 else ""
    return "; ".join(parts) + suffix


def _skip_from_candidate(candidate: ReviewPromotionCandidate, reason: str) -> ReviewPromotionSkippedItem:
    return ReviewPromotionSkippedItem(
        path=candidate.path,
        relative_path=candidate.relative_path,
        note_type=candidate.note_type,
        topic=candidate.topic,
        status=candidate.current_status,
        reason=reason,
    )


def _has_paper_rows(body: str) -> bool:
    return any(" | papers | " in line for line in body.splitlines())


def _heading_topic(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].replace("Service Blueprint", "").replace("Evidence Ledger:", "").replace("Topic Map:", "").strip()
    return ""


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate
