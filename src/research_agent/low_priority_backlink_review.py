from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .obsidian import ObsidianWriter
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import (
    ACTIONABLE_BACKLINK_MIN_SCORE,
    BacklinkSuggestion,
    _frontmatter_scalar,
    _markdown_files,
    _set_frontmatter_scalars,
    _split_frontmatter,
    build_vault_index,
)


CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>L\d{3})\s+Ignore:", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)


@dataclass(frozen=True)
class LowPriorityBacklinkCandidate:
    proposal_id: str
    source_path: str
    source_title: str
    target_path: str
    target_title: str
    score: int
    reason: str


@dataclass(frozen=True)
class LowPriorityBacklinkSkippedItem:
    proposal_path: Path
    proposal_id: str
    reason: str


@dataclass(frozen=True)
class LowPriorityBacklinkReviewResult:
    vault_path: Path
    candidates: list[LowPriorityBacklinkCandidate]
    min_score: int


@dataclass(frozen=True)
class LowPriorityBacklinkReviewWriteResult:
    result: LowPriorityBacklinkReviewResult
    note_path: Path


@dataclass(frozen=True)
class LowPriorityBacklinkApplyItem:
    proposal_path: Path
    proposal_id: str
    source_path: str
    target_path: str
    score: int
    reason: str


@dataclass(frozen=True)
class LowPriorityBacklinkApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[LowPriorityBacklinkApplyItem]
    updated_paths: list[Path]
    skipped_items: list[LowPriorityBacklinkSkippedItem]


@dataclass(frozen=True)
class LowPriorityBacklinkQueue:
    proposal_paths: list[Path]
    pending_candidates: int
    checked_ignores: int
    checked_proposal_paths: list[Path]


def build_low_priority_backlink_review(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    max_proposals: int = 50,
    min_score: int = ACTIONABLE_BACKLINK_MIN_SCORE,
) -> LowPriorityBacklinkReviewResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    index = build_vault_index(vault, stale_days=stale_days, max_suggestions=max_suggestions)
    low_priority = [
        suggestion
        for suggestion in index.suggestions
        if 0 < suggestion.score < min_score
    ][:max_proposals]
    candidates = [
        _candidate_from_suggestion(suggestion, proposal_id=f"L{index + 1:03d}")
        for index, suggestion in enumerate(low_priority)
    ]
    return LowPriorityBacklinkReviewResult(vault_path=vault, candidates=candidates, min_score=min_score)


def build_low_priority_backlink_queue(vault_path: Path) -> LowPriorityBacklinkQueue:
    vault = vault_path.expanduser().resolve()
    proposal_paths: list[Path] = []
    pending_candidates = 0
    checked_ignores = 0
    checked_proposal_paths: list[Path] = []

    for proposal_path in _low_priority_review_notes(vault):
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "proposal_state").lower() == "applied":
            continue
        records = _candidate_records(text)
        checked_ids = _checked_ids(text)
        if not records and not checked_ids:
            continue
        proposal_paths.append(proposal_path)
        pending_candidates += len(records)
        checked_ignores += len(checked_ids)
        if checked_ids:
            checked_proposal_paths.append(proposal_path)

    return LowPriorityBacklinkQueue(
        proposal_paths=proposal_paths,
        pending_candidates=pending_candidates,
        checked_ignores=checked_ignores,
        checked_proposal_paths=checked_proposal_paths,
    )


def render_low_priority_backlink_review(
    result: LowPriorityBacklinkReviewResult,
    *,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Low-Priority Backlink Review Proposals

Vault: {result.vault_path}
Low-priority signals: {len(result.candidates)}
Actionable threshold: score {result.min_score}+

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Ignore checklist:
{_ignore_checklist_lines(shown)}
"""


def write_low_priority_backlink_review_note(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    max_proposals: int = 50,
    min_score: int = ACTIONABLE_BACKLINK_MIN_SCORE,
) -> LowPriorityBacklinkReviewWriteResult:
    result = build_low_priority_backlink_review(
        settings,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
        max_proposals=max_proposals,
        min_score=min_score,
    )
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_low-priority-backlink-review.md",
        render_low_priority_backlink_review_note(
            result,
            checked_at=timestamp.isoformat(timespec="seconds"),
            max_proposals=max_proposals,
        ),
    )
    return LowPriorityBacklinkReviewWriteResult(result=result, note_path=path)


def render_low_priority_backlink_review_note(
    result: LowPriorityBacklinkReviewResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("low-priority-backlink-review")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
proposal_count: {len(result.candidates)}
min_score: {result.min_score}
---
# Low-Priority Backlink Review

## Summary

| metric | value |
|---|---:|
| low-priority signals | {len(result.candidates)} |
| actionable threshold | score {result.min_score}+ |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Ignore Checklist

Check signals that should stay hidden from future vault index notes.

{_ignore_checklist_lines(shown)}

## Review Checklist

- [ ] Confirm these score-below-threshold signals do not need actionable backlink work.
- [ ] Apply accepted ignores with `apply-low-priority-backlinks`.
- [ ] Rerun `index-vault`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def apply_low_priority_backlinks(
    settings: Settings,
    *,
    dry_run: bool = True,
    applied_at: str = "",
) -> LowPriorityBacklinkApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[LowPriorityBacklinkApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[LowPriorityBacklinkSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _low_priority_review_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text)
        checked_ids = _checked_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(LowPriorityBacklinkSkippedItem(proposal_path, proposal_id, "candidate record not found"))
                continue
            approved_items.append(
                LowPriorityBacklinkApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    source_path=candidate.source_path,
                    target_path=candidate.target_path,
                    score=candidate.score,
                    reason=candidate.reason,
                )
            )
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")
            updated_paths.append(proposal_path)

    if dry_run:
        updated_paths = sorted(proposal_paths_to_mark, key=lambda item: item.as_posix())

    return LowPriorityBacklinkApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_low_priority_backlink_apply_result(result: LowPriorityBacklinkApplyResult) -> str:
    title = "Low-Priority Backlink Apply Dry Run" if result.dry_run else "Low-Priority Backlink Apply"
    action = "Would ignore signals" if result.dry_run else "Ignored signals"
    return f"""{title}

Proposal notes scanned: {result.proposal_notes}
Approved checklist items: {len(result.approved_items)}
{action}: {len(result.approved_items)}
Proposal notes updated: {len(result.updated_paths)}
Skipped items: {len(result.skipped_items)}

Approved items:
{_apply_item_lines(result.approved_items)}

Updated proposal notes:
{_path_lines(result.updated_paths)}

Skipped items:
{_skipped_lines(result.skipped_items)}
"""


def _candidate_from_suggestion(suggestion: BacklinkSuggestion, *, proposal_id: str) -> LowPriorityBacklinkCandidate:
    return LowPriorityBacklinkCandidate(
        proposal_id=proposal_id,
        source_path=suggestion.source.relative_path,
        source_title=suggestion.source.title,
        target_path=suggestion.target.relative_path,
        target_title=suggestion.target.title,
        score=suggestion.score,
        reason=suggestion.reason,
    )


def _low_priority_review_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "low-priority-backlink-review":
            paths.append(path)
    return sorted(paths)


def _checked_ids(text: str) -> list[str]:
    return [
        match.group("proposal_id")
        for match in CHECK_RE.finditer(text)
        if match.group("state").strip().lower() == "x"
    ]


def _candidate_json(candidates: list[LowPriorityBacklinkCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: LowPriorityBacklinkCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "source_path": candidate.source_path,
        "source_title": candidate.source_title,
        "target_path": candidate.target_path,
        "target_title": candidate.target_title,
        "score": candidate.score,
        "reason": candidate.reason,
    }


def _candidate_records(text: str) -> dict[str, LowPriorityBacklinkCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, LowPriorityBacklinkCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict) -> LowPriorityBacklinkCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    source_path = str(item.get("source_path") or "").strip()
    target_path = str(item.get("target_path") or "").strip()
    if not proposal_id or not source_path or not target_path:
        return None
    return LowPriorityBacklinkCandidate(
        proposal_id=proposal_id,
        source_path=source_path,
        source_title=str(item.get("source_title") or Path(source_path).stem).strip(),
        target_path=target_path,
        target_title=str(item.get("target_title") or Path(target_path).stem).strip(),
        score=_int_value(item.get("score")),
        reason=str(item.get("reason") or "").strip(),
    )


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _proposal_lines(candidates: list[LowPriorityBacklinkCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(
        f"- {candidate.proposal_id} [[{_link_target(candidate.source_path)}|{candidate.source_title}]] -> "
        f"[[{_link_target(candidate.target_path)}|{candidate.target_title}]] "
        f"(score {candidate.score}); reason: {candidate.reason}"
        for candidate in candidates
    )


def _ignore_checklist_lines(candidates: list[LowPriorityBacklinkCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(
        f"- [ ] {candidate.proposal_id} Ignore: [[{_link_target(candidate.source_path)}|{candidate.source_title}]] -> "
        f"[[{_link_target(candidate.target_path)}|{candidate.target_title}]]"
        for candidate in candidates
    )


def _apply_item_lines(items: list[LowPriorityBacklinkApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.proposal_id} {item.source_path} -> {item.target_path} (score {item.score}): {item.reason}"
        for item in items
    )


def _skipped_lines(items: list[LowPriorityBacklinkSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_path}: {item.proposal_id} {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _link_target(relative_path: str) -> str:
    return relative_path[:-3] if relative_path.endswith(".md") else relative_path
