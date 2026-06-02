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
    WIKILINK_RE,
    _add_related_note_link,
    _frontmatter_scalar,
    _markdown_files,
    _set_frontmatter_scalars,
    _split_frontmatter,
    build_vault_index,
)


CHECK_RE = re.compile(
    r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>M\d{3})\s+"
    r"(?P<action>Ignore|Archive|Link to (?P<link>\[\[[^\]]+\]\])):",
    re.MULTILINE,
)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)


@dataclass(frozen=True)
class ManualOrphanCandidate:
    proposal_id: str
    path: Path
    relative_path: str
    title: str
    status: str
    reason: str


@dataclass(frozen=True)
class ManualOrphanSkippedItem:
    path: Path
    relative_path: str
    status: str
    reason: str


@dataclass(frozen=True)
class ManualOrphanReviewResult:
    vault_path: Path
    candidates: list[ManualOrphanCandidate]


@dataclass(frozen=True)
class ManualOrphanReviewWriteResult:
    result: ManualOrphanReviewResult
    note_path: Path


@dataclass(frozen=True)
class ManualOrphanAction:
    proposal_path: Path
    proposal_id: str
    action: str
    target: str


@dataclass(frozen=True)
class ManualOrphanApplyItem:
    proposal_path: Path
    proposal_id: str
    action: str
    path: Path
    relative_path: str
    target: str


@dataclass(frozen=True)
class ManualOrphanApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[ManualOrphanApplyItem]
    updated_paths: list[Path]
    skipped_items: list[ManualOrphanSkippedItem]


@dataclass(frozen=True)
class ManualOrphanQueue:
    proposal_paths: list[Path]
    pending_candidates: int
    checked_actions: int
    checked_proposal_paths: list[Path]


def build_manual_orphan_review(settings: Settings, *, max_proposals: int = 50) -> ManualOrphanReviewResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    index = build_vault_index(vault)
    candidates = [
        ManualOrphanCandidate(
            proposal_id=f"M{index + 1:03d}",
            path=note.path,
            relative_path=note.relative_path,
            title=note.title,
            status=note.status or "missing",
            reason=_candidate_reason(note.status),
        )
        for index, note in enumerate(index.orphan_manual_review_notes[:max_proposals])
    ]
    return ManualOrphanReviewResult(vault_path=vault, candidates=candidates)


def build_manual_orphan_queue(vault_path: Path) -> ManualOrphanQueue:
    vault = vault_path.expanduser().resolve()
    proposal_paths: list[Path] = []
    pending_candidates = 0
    checked_actions = 0
    checked_proposal_paths: list[Path] = []

    for proposal_path in _manual_orphan_review_notes(vault):
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "proposal_state").lower() == "applied":
            continue
        records = _candidate_records(text, vault=vault)
        actions = _checked_actions(text, proposal_path=proposal_path)
        if not records and not actions:
            continue
        proposal_paths.append(proposal_path)
        pending_candidates += len(records)
        checked_actions += len(actions)
        if actions:
            checked_proposal_paths.append(proposal_path)

    return ManualOrphanQueue(
        proposal_paths=proposal_paths,
        pending_candidates=pending_candidates,
        checked_actions=checked_actions,
        checked_proposal_paths=checked_proposal_paths,
    )


def render_manual_orphan_review(result: ManualOrphanReviewResult, *, max_proposals: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Manual Orphan Review Proposals

Vault: {result.vault_path}
Manual orphan candidates: {len(result.candidates)}

Proposals:
{_proposal_summary_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Action checklist:
{_action_checklist_lines(shown)}
"""


def write_manual_orphan_review_note(settings: Settings, *, max_proposals: int = 50) -> ManualOrphanReviewWriteResult:
    result = build_manual_orphan_review(settings, max_proposals=max_proposals)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_manual-orphan-review.md",
        render_manual_orphan_review_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return ManualOrphanReviewWriteResult(result=result, note_path=path)


def render_manual_orphan_review_note(
    result: ManualOrphanReviewResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("manual-orphan-review")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
proposal_count: {len(result.candidates)}
---
# Manual Orphan Review

## Summary

| metric | value |
|---|---:|
| manual orphan candidates | {len(result.candidates)} |

## Proposals

{_proposal_summary_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Action Checklist

Check at most one action per proposal. For link actions, replace `TARGET_NOTE` with an Obsidian wikilink target before applying.

{_action_checklist_lines(shown)}

## Review Checklist

- [ ] Confirm each note should remain standalone, be archived, or be linked.
- [ ] Apply accepted actions with `apply-manual-orphan-review`.
- [ ] Rerun `index-vault`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def apply_manual_orphan_review(
    settings: Settings,
    *,
    dry_run: bool = True,
    reviewed_at: str = "",
) -> ManualOrphanApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[ManualOrphanApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[ManualOrphanSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _manual_orphan_review_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        actions = _checked_actions(text, proposal_path=proposal_path)
        grouped = _group_actions(actions)
        for proposal_id, proposal_actions in grouped.items():
            if len(proposal_actions) > 1:
                skipped_items.append(
                    ManualOrphanSkippedItem(
                        path=proposal_path,
                        relative_path=proposal_path.relative_to(vault).as_posix(),
                        status="manual-orphan-review",
                        reason=f"{proposal_id} has multiple checked actions",
                    )
                )
                continue
            action = proposal_actions[0]
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(_skip_from_path(proposal_path, vault=vault, reason=f"{proposal_id} candidate record not found"))
                continue
            if not candidate.path.exists():
                skipped_items.append(_skip_from_candidate(candidate, "manual note not found"))
                continue
            updated, changed, skip_reason = _apply_action_to_text(
                candidate.path.read_text(encoding="utf-8", errors="replace"),
                action=action,
                reviewed_at=reviewed_at,
            )
            if skip_reason:
                skipped_items.append(_skip_from_candidate(candidate, skip_reason))
                continue
            if not changed:
                skipped_items.append(_skip_from_candidate(candidate, "note already has requested orphan review state"))
                continue
            if not dry_run:
                candidate.path.write_text(updated, encoding="utf-8")
            approved_items.append(
                ManualOrphanApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    action=action.action,
                    path=candidate.path,
                    relative_path=candidate.relative_path,
                    target=action.target,
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

    return ManualOrphanApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_manual_orphan_apply_result(result: ManualOrphanApplyResult) -> str:
    title = "Manual Orphan Review Apply Dry Run" if result.dry_run else "Manual Orphan Review Apply"
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


def _candidate_reason(status: str) -> str:
    normalized = status.lower()
    if normalized == "active":
        return "manual note has active status but no incoming or outgoing links"
    if normalized == "completed":
        return "manual note has completed status but no incoming or outgoing links"
    return "manual note has status metadata but no incoming or outgoing links"


def _apply_action_to_text(text: str, *, action: ManualOrphanAction, reviewed_at: str) -> tuple[str, bool, str]:
    frontmatter, _body = _split_frontmatter(text)
    current_review = _frontmatter_scalar(frontmatter, "orphan_review").lower()
    values = {
        "orphan_reviewed_by": "research-agent",
        "orphan_review_reason": "manual orphan review",
    }
    if reviewed_at:
        values["orphan_reviewed_at"] = reviewed_at

    if action.action == "ignore":
        if current_review in {"ignored", "standalone"}:
            return text, False, ""
        values["orphan_review"] = "ignored"
        return _set_frontmatter_scalars(text, values), True, ""

    if action.action == "archive":
        if _frontmatter_scalar(frontmatter, "status").lower() == "archived":
            return text, False, ""
        values.update(
            {
                "status": "archived",
                "orphan_review": "archived",
                "archived_by": "research-agent",
                "archive_reason": "manual orphan review",
            }
        )
        if reviewed_at:
            values["archived_at"] = reviewed_at
        return _set_frontmatter_scalars(text, values), True, ""

    if action.action == "link":
        if not action.target or action.target == "TARGET_NOTE":
            return text, False, "link action still uses TARGET_NOTE placeholder"
        linked, added = _add_related_note_link(text, action.target)
        values["orphan_review"] = "linked"
        updated = _set_frontmatter_scalars(linked, values)
        return updated, added or current_review != "linked", ""

    return text, False, f"unsupported action: {action.action}"


def _manual_orphan_review_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "manual-orphan-review":
            paths.append(path)
    return sorted(paths)


def _checked_actions(text: str, *, proposal_path: Path) -> list[ManualOrphanAction]:
    actions: list[ManualOrphanAction] = []
    for match in CHECK_RE.finditer(text):
        if match.group("state").strip().lower() != "x":
            continue
        raw_action = match.group("action")
        link = match.group("link") or ""
        if raw_action == "Ignore":
            actions.append(ManualOrphanAction(proposal_path, match.group("proposal_id"), "ignore", ""))
        elif raw_action == "Archive":
            actions.append(ManualOrphanAction(proposal_path, match.group("proposal_id"), "archive", ""))
        elif link:
            actions.append(ManualOrphanAction(proposal_path, match.group("proposal_id"), "link", _wikilink_target(link)))
    return actions


def _group_actions(actions: list[ManualOrphanAction]) -> dict[str, list[ManualOrphanAction]]:
    grouped: dict[str, list[ManualOrphanAction]] = {}
    for action in actions:
        grouped.setdefault(action.proposal_id, []).append(action)
    return grouped


def _wikilink_target(link: str) -> str:
    match = WIKILINK_RE.search(link)
    if not match:
        return ""
    target = match.group(1).strip()
    return target[:-3] if target.endswith(".md") else target


def _candidate_json(candidates: list[ManualOrphanCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: ManualOrphanCandidate) -> dict:
    return {
        "proposal_id": candidate.proposal_id,
        "path": candidate.relative_path,
        "title": candidate.title,
        "status": candidate.status,
        "reason": candidate.reason,
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, ManualOrphanCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, ManualOrphanCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> ManualOrphanCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    relative_path = str(item.get("path") or "").strip()
    path = _vault_path(vault, relative_path)
    if not proposal_id or path is None:
        return None
    return ManualOrphanCandidate(
        proposal_id=proposal_id,
        path=path,
        relative_path=relative_path,
        title=str(item.get("title") or Path(relative_path).stem).strip(),
        status=str(item.get("status") or "").strip(),
        reason=str(item.get("reason") or "").strip(),
    )


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate


def _proposal_summary_lines(candidates: list[ManualOrphanCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(
        f"- {candidate.proposal_id} [{candidate.relative_path}]({candidate.relative_path}) "
        f"(status: {candidate.status}); reason: {candidate.reason}"
        for candidate in candidates
    )


def _action_checklist_lines(candidates: list[ManualOrphanCandidate]) -> str:
    if not candidates:
        return "- None."
    lines: list[str] = []
    for candidate in candidates:
        lines.extend(
            [
                f"- [ ] {candidate.proposal_id} Ignore: [[{candidate.relative_path[:-3]}|{candidate.title}]]",
                f"- [ ] {candidate.proposal_id} Archive: [[{candidate.relative_path[:-3]}|{candidate.title}]]",
                f"- [ ] {candidate.proposal_id} Link to [[TARGET_NOTE]]: [[{candidate.relative_path[:-3]}|{candidate.title}]]",
            ]
        )
    return "\n".join(lines)


def _apply_item_lines(items: list[ManualOrphanApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.proposal_id} {item.relative_path}: {item.action}{_target_suffix(item.target)}"
        for item in items
    )


def _target_suffix(target: str) -> str:
    return f" -> [[{target}|{Path(target).stem}]]" if target else ""


def _skipped_lines(items: list[ManualOrphanSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.relative_path} (status: {item.status or 'unknown'}): {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _skip_from_path(path: Path, *, vault: Path, reason: str) -> ManualOrphanSkippedItem:
    return ManualOrphanSkippedItem(
        path=path,
        relative_path=path.relative_to(vault).as_posix(),
        status="unknown",
        reason=reason,
    )


def _skip_from_candidate(candidate: ManualOrphanCandidate, reason: str) -> ManualOrphanSkippedItem:
    return ManualOrphanSkippedItem(
        path=candidate.path,
        relative_path=candidate.relative_path,
        status=candidate.status,
        reason=reason,
    )
