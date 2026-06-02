from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .obsidian import ObsidianWriter, REVIEWED_STATUSES
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


CLEANUP_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>V\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)


@dataclass(frozen=True)
class VerificationReplacement:
    old: str
    new: str
    occurrence_count: int


@dataclass(frozen=True)
class VerificationCleanupCandidate:
    proposal_id: str
    path: Path
    relative_path: str
    note_type: str
    topic: str
    replacements: list[VerificationReplacement]


@dataclass(frozen=True)
class VerificationCleanupSkippedItem:
    path: Path
    relative_path: str
    note_type: str
    topic: str
    reason: str


@dataclass(frozen=True)
class VerificationCleanupResult:
    vault_path: Path
    notes_scanned: int
    candidates: list[VerificationCleanupCandidate]
    skipped_items: list[VerificationCleanupSkippedItem]


@dataclass(frozen=True)
class VerificationCleanupWriteResult:
    result: VerificationCleanupResult
    note_path: Path


@dataclass(frozen=True)
class VerificationCleanupApplyItem:
    proposal_path: Path
    proposal_id: str
    path: Path
    relative_path: str
    replacements: int


@dataclass(frozen=True)
class VerificationCleanupApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[VerificationCleanupApplyItem]
    updated_paths: list[Path]
    skipped_items: list[VerificationCleanupSkippedItem]


STALE_REPLACEMENTS = [
    (
        "No paper sources were collected in this run; add paper metadata in a follow-up research pass if papers are required.",
        "Paper sources are now connected in this evidence ledger; confirm each paper or book-chapter source is strong enough for the intended decision.",
    ),
    (
        "이 실행에서는 논문 출처가 수집되지 않았으므로, 논문 근거가 필요하면 후속 research pass에서 논문 메타데이터를 추가합니다.",
        "논문 출처가 이제 이 근거 장부에 연결되었습니다. 각 논문 또는 책 챕터 출처가 의도한 의사결정에 충분히 강한 근거인지 확인하세요.",
    ),
    (
        "Official documentation and standards pages are resolved; paper metadata still needs review.",
        "Paper evidence is connected, but human review should confirm whether these book-chapter sources are strong enough for production decisions.",
    ),
    (
        "공식 문서와 표준 출처는 정확한 세부 페이지로 확정되었습니다. 논문 메타데이터는 아직 검토가 필요합니다.",
        "논문 근거는 연결되었지만, 이 책 챕터 출처가 프로덕션 의사결정에 충분히 강한 근거인지 사람의 검토가 필요합니다.",
    ),
    (
        "Exact source pages and paper metadata need human review.",
        "Exact source pages are resolved, and connected paper evidence needs human strength review.",
    ),
    (
        "정확한 출처 페이지와 논문 메타데이터는 사람의 검토가 필요합니다.",
        "정확한 출처 페이지는 확정되었고, 연결된 논문 근거는 사람이 근거 강도를 검토해야 합니다.",
    ),
]


def build_verification_cleanup(settings: Settings, *, max_proposals: int = 50) -> VerificationCleanupResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    candidates: list[VerificationCleanupCandidate] = []
    skipped_items: list[VerificationCleanupSkippedItem] = []
    notes_scanned = 0

    for path in sorted(_markdown_files(vault), key=lambda item: item.relative_to(vault).as_posix()):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        note_type = _frontmatter_scalar(frontmatter, "type")
        if note_type not in {"evidence-ledger", "service-blueprint"}:
            continue
        if _frontmatter_scalar(frontmatter, "generated_by") != "research-agent":
            continue
        notes_scanned += 1
        relative_path = path.relative_to(vault).as_posix()
        topic = _frontmatter_scalar(frontmatter, "topic") or _heading_topic(body)
        status = _frontmatter_scalar(frontmatter, "status")
        if status.lower() in REVIEWED_STATUSES:
            skipped_items.append(VerificationCleanupSkippedItem(path, relative_path, note_type, topic, "protected reviewed/evergreen note"))
            continue
        replacements = _candidate_replacements(text)
        if not replacements:
            continue
        proposal_id = f"V{len(candidates) + 1:03d}"
        candidates.append(
            VerificationCleanupCandidate(
                proposal_id=proposal_id,
                path=path,
                relative_path=relative_path,
                note_type=note_type,
                topic=topic,
                replacements=replacements,
            )
        )
        if len(candidates) >= max_proposals:
            break

    return VerificationCleanupResult(
        vault_path=vault,
        notes_scanned=notes_scanned,
        candidates=candidates,
        skipped_items=skipped_items,
    )


def render_verification_cleanup(result: VerificationCleanupResult, *, max_proposals: int = 50, max_skipped: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    shown_skipped = result.skipped_items[:max_skipped]
    hidden_skipped = len(result.skipped_items) - len(shown_skipped)
    return f"""Verification Text Cleanup

Vault: {result.vault_path}
Generated verification notes scanned: {result.notes_scanned}
Cleanup candidates: {len(result.candidates)}
Skipped notes: {len(result.skipped_items)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Skipped:
{_skipped_lines(shown_skipped)}
{_hidden_skipped_line(hidden_skipped, max_skipped)}
"""


def write_verification_cleanup_note(settings: Settings, *, max_proposals: int = 50) -> VerificationCleanupWriteResult:
    result = build_verification_cleanup(settings, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_verification-cleanup.md",
        render_verification_cleanup_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return VerificationCleanupWriteResult(result=result, note_path=path)


def apply_verification_cleanup(settings: Settings, *, dry_run: bool = True, applied_at: str = "") -> VerificationCleanupApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[VerificationCleanupApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[VerificationCleanupSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _verification_cleanup_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(VerificationCleanupSkippedItem(proposal_path, proposal_path.relative_to(vault).as_posix(), "verification-cleanup", "", "candidate record not found"))
                continue
            if not candidate.path.exists():
                skipped_items.append(_skip_from_candidate(candidate, "note not found"))
                continue
            current_text = candidate.path.read_text(encoding="utf-8", errors="replace")
            frontmatter, _body = _split_frontmatter(current_text)
            status = _frontmatter_scalar(frontmatter, "status")
            if status.lower() in REVIEWED_STATUSES:
                skipped_items.append(_skip_from_candidate(candidate, "protected reviewed/evergreen note"))
                continue
            updated, replacement_count = _apply_replacements(current_text, candidate.replacements)
            if replacement_count == 0:
                skipped_items.append(_skip_from_candidate(candidate, "stale text already cleaned or pattern not found"))
                continue
            values = {"verification_cleanup_provider": "deterministic-stale-text"}
            if applied_at:
                values["verification_cleaned_at"] = applied_at
            updated = _set_frontmatter_scalars(updated, values)
            if not dry_run:
                candidate.path.write_text(updated, encoding="utf-8")
            approved_items.append(
                VerificationCleanupApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    path=candidate.path,
                    relative_path=candidate.relative_path,
                    replacements=replacement_count,
                )
            )
            updated_paths.append(candidate.path)
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return VerificationCleanupApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_verification_cleanup_apply_result(result: VerificationCleanupApplyResult) -> str:
    title = "Verification Text Cleanup Apply Dry Run" if result.dry_run else "Verification Text Cleanup Apply"
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


def render_verification_cleanup_note(
    result: VerificationCleanupResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("verification-cleanup")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
notes_scanned: {result.notes_scanned}
proposal_count: {len(result.candidates)}
skipped_count: {len(result.skipped_items)}
---
# Verification Text Cleanup

## Summary

| metric | value |
|---|---:|
| generated verification notes scanned | {result.notes_scanned} |
| cleanup candidates | {len(result.candidates)} |
| skipped notes | {len(result.skipped_items)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Skipped

{_skipped_lines(result.skipped_items)}

## Review Checklist

- [ ] Confirm stale verification text is no longer true for each candidate.
- [ ] Apply accepted cleanup updates with `apply-verification-cleanup`.
- [ ] Rerun `review-promotion-proposals`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _candidate_replacements(text: str) -> list[VerificationReplacement]:
    replacements: list[VerificationReplacement] = []
    for old, new in STALE_REPLACEMENTS:
        count = text.count(old)
        if count:
            replacements.append(VerificationReplacement(old=old, new=new, occurrence_count=count))
    return replacements


def _apply_replacements(text: str, replacements: list[VerificationReplacement]) -> tuple[str, int]:
    updated = text
    count = 0
    for replacement in replacements:
        occurrences = updated.count(replacement.old)
        if occurrences:
            updated = updated.replace(replacement.old, replacement.new)
            count += occurrences
    return updated, count


def _candidate_json(candidates: list[VerificationCleanupCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: VerificationCleanupCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "path": candidate.relative_path,
        "note_type": candidate.note_type,
        "topic": candidate.topic,
        "replacements": [
            {
                "old": replacement.old,
                "new": replacement.new,
                "occurrence_count": replacement.occurrence_count,
            }
            for replacement in candidate.replacements
        ],
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, VerificationCleanupCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, VerificationCleanupCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> VerificationCleanupCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    relative_path = str(item.get("path") or "").strip()
    path = _vault_path(vault, relative_path)
    note_type = str(item.get("note_type") or "").strip()
    raw_replacements = item.get("replacements")
    if not proposal_id or path is None or note_type not in {"evidence-ledger", "service-blueprint"} or not isinstance(raw_replacements, list):
        return None
    replacements: list[VerificationReplacement] = []
    for raw in raw_replacements:
        if not isinstance(raw, dict):
            continue
        old = str(raw.get("old") or "")
        new = str(raw.get("new") or "")
        if old and new:
            replacements.append(
                VerificationReplacement(
                    old=old,
                    new=new,
                    occurrence_count=int(raw.get("occurrence_count") or 0),
                )
            )
    if not replacements:
        return None
    return VerificationCleanupCandidate(
        proposal_id=proposal_id,
        path=path,
        relative_path=relative_path,
        note_type=note_type,
        topic=str(item.get("topic") or "").strip(),
        replacements=replacements,
    )


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in CLEANUP_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _verification_cleanup_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "verification-cleanup":
            paths.append(path)
    return sorted(paths)


def _proposal_lines(candidates: list[VerificationCleanupCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: VerificationCleanupCandidate) -> str:
    return (
        f"- [ ] {candidate.proposal_id} Clean [{candidate.relative_path}]({candidate.relative_path}) "
        f"({candidate.note_type}, replacements: {sum(item.occurrence_count for item in candidate.replacements)})"
    )


def _apply_item_lines(items: list[VerificationCleanupApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.relative_path}: {item.replacements} replacement(s)" for item in items)


def _skipped_lines(items: list[VerificationCleanupSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.relative_path} ({item.note_type}): {item.reason}" for item in items)


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


def _skip_from_candidate(candidate: VerificationCleanupCandidate, reason: str) -> VerificationCleanupSkippedItem:
    return VerificationCleanupSkippedItem(
        path=candidate.path,
        relative_path=candidate.relative_path,
        note_type=candidate.note_type,
        topic=candidate.topic,
        reason=reason,
    )


def _heading_topic(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].replace("Service Blueprint", "").replace("Evidence Ledger:", "").strip()
    return ""


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate
