from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Settings
from .obsidian import REVIEWED_STATUSES, ObsidianWriter
from .textutil import yaml_scalar
from .timeutil import now_local


WIKILINK_RE = re.compile(r"\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
BACKLINK_PROPOSAL_RE = re.compile(
    r"^- \[(?P<state>[ xX])\] Add (?P<link>\[\[[^\]]+\]\])(?: \(score (?P<score>\d+)\))?(?:: (?P<reason>.*))?$"
)
RELATED_NOTES_HEADING_RE = re.compile(r"^## Related Notes\s*$", re.MULTILINE)
BACKLINK_PROPOSALS_HEADING_RE = re.compile(r"^## Backlink Proposals\s*$", re.MULTILINE)
H2_HEADING_RE = re.compile(r"^## ", re.MULTILINE)
TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]+")
LOW_PRIORITY_BACKLINK_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>L\d{3})\s+Ignore:", re.MULTILINE)
LOW_PRIORITY_CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)
STOPWORDS = {
    "ai",
    "and",
    "api",
    "blueprint",
    "draft",
    "evidence",
    "for",
    "guide",
    "index",
    "ledger",
    "note",
    "notes",
    "research",
    "run",
    "service",
    "source",
    "system",
    "systems",
    "the",
    "topic",
    "vault",
    "with",
}
ACTIONABLE_BACKLINK_MIN_SCORE = 3
ORPHAN_INCOMING_IGNORED_NOTE_TYPES = {"low-priority-backlink-review", "manual-orphan-review"}
SUGGESTION_IGNORED_NOTE_TYPES = {"low-priority-backlink-review", "manual-orphan-review"}
GENERATED_HISTORY_NOTE_TYPES = {
    "backlink-proposals",
    "bilingual-audit",
    "blueprint-refresh",
    "low-priority-backlink-review",
    "manual-orphan-review",
    "official-docs-refresh",
    "paper-claim-refresh",
    "paper-downstream-proposals",
    "paper-refresh",
    "review-promotion",
    "run-cleanup",
    "run-log",
    "source-audit",
    "standards-refresh",
    "vault-health",
    "verification-cleanup",
}


@dataclass(frozen=True)
class VaultNote:
    path: Path
    relative_path: str
    link_target: str
    title: str
    note_type: str = "unknown"
    status: str = ""
    topic: str = ""
    checked_at: str = ""
    generated_by: str = ""
    rerun_of: str = ""
    orphan_review: str = ""
    aliases: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    outgoing_links: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class BacklinkSuggestion:
    source: VaultNote
    target: VaultNote
    reason: str
    score: int


@dataclass(frozen=True)
class VaultIndex:
    notes: list[VaultNote]
    suggestions: list[BacklinkSuggestion]
    stale_notes: list[VaultNote]
    orphan_notes: list[VaultNote]
    orphan_review_notes: list[VaultNote]
    orphan_manual_review_notes: list[VaultNote]
    orphan_history_notes: list[VaultNote]
    orphan_reference_notes: list[VaultNote]


@dataclass(frozen=True)
class BacklinkProposalResult:
    proposal_path: Path | None
    appended_paths: list[Path]
    superseded_paths: list[Path]
    suggestions: list[BacklinkSuggestion]
    applied_suggestions: list[BacklinkSuggestion]
    skipped_suggestions: list[BacklinkSuggestion]


@dataclass(frozen=True)
class BacklinkReviewItem:
    source_path: Path
    relative_path: str
    target: str
    line: str
    checked: bool
    resolved: bool
    score: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class BacklinkReviewQueue:
    items: list[BacklinkReviewItem]
    pending: list[BacklinkReviewItem]
    completed: list[BacklinkReviewItem]
    resolved: list[BacklinkReviewItem]


@dataclass(frozen=True)
class ReviewedBacklinkApplyResult:
    dry_run: bool
    checked_items: list[BacklinkReviewItem]
    applied_items: list[BacklinkReviewItem]
    already_resolved_items: list[BacklinkReviewItem]
    pending_items: list[BacklinkReviewItem]
    updated_paths: list[Path]


@dataclass(frozen=True)
class BacklinkHistoryEntry:
    path: Path
    relative_path: str
    created_at: str
    checked_at: str
    status: str
    proposal_state: str
    effective_state: str
    candidate_links: int
    applied_checklist_items: int
    skipped_protected_notes: int
    min_score: int | None


@dataclass(frozen=True)
class BacklinkHistory:
    entries: list[BacklinkHistoryEntry]
    latest: BacklinkHistoryEntry | None
    state_counts: Counter[str]


@dataclass(frozen=True)
class BacklinkHistoryStateChange:
    path: Path
    relative_path: str
    before_state: str
    after_state: str


@dataclass(frozen=True)
class BacklinkHistoryWriteResult:
    dry_run: bool
    changes: list[BacklinkHistoryStateChange]
    unchanged_count: int


def write_vault_index(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
) -> Path:
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    index = build_vault_index(writer.vault_path, stale_days=stale_days, max_suggestions=max_suggestions)
    today = now_local(settings.app.timezone).date().isoformat()
    return writer.write_note(
        settings.obsidian.taxonomy_dir,
        f"{today}_vault-index.md",
        render_vault_index(index, checked_at=today, stale_days=stale_days),
    )


def render_backlink_proposals(
    suggestions: list[BacklinkSuggestion],
    *,
    checked_at: str,
    min_score: int = 3,
    proposal_state: str = "proposed",
    applied_at: str = "",
    applied_suggestions: list[BacklinkSuggestion] | None = None,
    skipped_suggestions: list[BacklinkSuggestion] | None = None,
) -> str:
    applied = applied_suggestions or []
    skipped = skipped_suggestions or []
    state_lines = [f"proposal_state: {yaml_scalar(proposal_state)}"]
    if applied_at:
        state_lines.append(f"applied_at: {yaml_scalar(applied_at)}")
    state_frontmatter = "\n".join(state_lines)
    return f"""---
type: backlink-proposals
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
generated_by: research-agent
{state_frontmatter}
---

# Backlink Proposals

## Summary

- Candidate links: {len(suggestions)}
- Applied checklist items: {len(applied)}
- Skipped protected notes: {len(skipped)}
- Minimum score: {min_score}

## Review Queue

{_proposal_lines(suggestions)}

## Applied Checklist Items

{_proposal_lines(applied)}

## Skipped Protected Notes

These sources have `status: reviewed` or `status: evergreen`. Re-run with explicit review approval before appending to them.

{_proposal_lines(skipped)}
"""


def build_backlink_proposal_suggestions(
    vault_path: Path,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
) -> list[BacklinkSuggestion]:
    index = build_vault_index(vault_path, stale_days=stale_days, max_suggestions=max_suggestions)
    return _filter_suggestions(index.suggestions, min_score=min_score)


def write_backlink_proposals(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
    apply: bool = False,
    include_reviewed: bool = False,
    supersede_previous: bool = False,
) -> BacklinkProposalResult:
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    index = build_vault_index(writer.vault_path, stale_days=stale_days, max_suggestions=max_suggestions)
    suggestions = _filter_suggestions(index.suggestions, min_score=min_score)
    applied_suggestions: list[BacklinkSuggestion] = []
    skipped_suggestions: list[BacklinkSuggestion] = []
    appended_paths: list[Path] = []

    if apply:
        appendable, skipped_suggestions = _split_appendable_suggestions(
            suggestions,
            include_reviewed=include_reviewed,
        )
        applied_suggestions, appended_paths = _append_backlink_proposals(appendable)

    today = now_local(settings.app.timezone).date().isoformat()
    proposal_state = "applied" if apply else "proposed"
    proposal_path = writer.write_note(
        settings.obsidian.run_dir,
        f"{today}_backlink-proposals.md",
        render_backlink_proposals(
            suggestions,
            checked_at=today,
            min_score=min_score,
            proposal_state=proposal_state,
            applied_at=today if apply else "",
            applied_suggestions=applied_suggestions,
            skipped_suggestions=skipped_suggestions,
        ),
    )
    superseded_paths = (
        _supersede_previous_proposals(writer.vault_path, proposal_path, checked_at=today)
        if supersede_previous
        else []
    )
    return BacklinkProposalResult(
        proposal_path=proposal_path,
        appended_paths=appended_paths,
        superseded_paths=superseded_paths,
        suggestions=suggestions,
        applied_suggestions=applied_suggestions,
        skipped_suggestions=skipped_suggestions,
    )


def build_backlink_review_queue(vault_path: Path) -> BacklinkReviewQueue:
    vault = vault_path.expanduser().resolve()
    items: list[BacklinkReviewItem] = []
    for path in _markdown_files(vault):
        items.extend(_parse_backlink_review_items(path, vault))
    completed = [item for item in items if item.checked]
    resolved = [item for item in items if not item.checked and item.resolved]
    pending = [item for item in items if not item.checked and not item.resolved]
    return BacklinkReviewQueue(
        items=sorted(items, key=_review_item_key),
        pending=sorted(pending, key=_review_item_key),
        completed=sorted(completed, key=_review_item_key),
        resolved=sorted(resolved, key=_review_item_key),
    )


def render_backlink_review_queue(queue: BacklinkReviewQueue) -> str:
    return f"""Backlink Review Queue

Total proposals: {len(queue.items)}
Pending: {len(queue.pending)}
Completed: {len(queue.completed)}
Resolved by existing wikilink: {len(queue.resolved)}

Pending notes:
{_review_note_counts(queue.pending)}

Resolved by existing wikilink:
{_review_item_lines(queue.resolved)}

Completed:
{_review_item_lines(queue.completed)}
"""


def apply_reviewed_backlinks(vault_path: Path, *, dry_run: bool = False) -> ReviewedBacklinkApplyResult:
    queue = build_backlink_review_queue(vault_path)
    checked_items = [item for item in queue.completed if not item.resolved]
    already_resolved_items = [item for item in queue.completed if item.resolved]
    grouped: dict[Path, list[BacklinkReviewItem]] = {}
    for item in checked_items:
        grouped.setdefault(item.source_path, []).append(item)

    applied_items: list[BacklinkReviewItem] = []
    updated_paths: list[Path] = []
    for path, items in grouped.items():
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        changed = False
        for item in items:
            updated, added = _add_related_note_link(updated, item.target)
            if added:
                applied_items.append(item)
                changed = True
            else:
                already_resolved_items.append(item)
        if changed:
            updated_paths.append(path)
            if not dry_run:
                path.write_text(updated, encoding="utf-8")

    return ReviewedBacklinkApplyResult(
        dry_run=dry_run,
        checked_items=sorted(checked_items, key=_review_item_key),
        applied_items=sorted(applied_items, key=_review_item_key),
        already_resolved_items=sorted(already_resolved_items, key=_review_item_key),
        pending_items=queue.pending,
        updated_paths=sorted(updated_paths),
    )


def render_apply_reviewed_backlinks_result(result: ReviewedBacklinkApplyResult) -> str:
    title = "Apply Reviewed Backlinks Dry Run" if result.dry_run else "Apply Reviewed Backlinks"
    action = "Would update notes" if result.dry_run else "Updated notes"
    items = "Would apply items" if result.dry_run else "Applied items"
    return f"""{title}

Checked items ready: {len(result.checked_items)}
{items}: {len(result.applied_items)}
Already resolved: {len(result.already_resolved_items)}
Pending checklist items left: {len(result.pending_items)}
{action}: {len(result.updated_paths)}

Updated note paths:
{_path_lines(result.updated_paths)}

Applied links:
{_review_item_lines(result.applied_items)}
"""


def build_backlink_history(vault_path: Path) -> BacklinkHistory:
    vault = vault_path.expanduser().resolve()
    raw_entries: list[tuple[Path, str, dict[str, str | list[str]], str]] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "backlink-proposals":
            continue
        raw_entries.append((path, path.relative_to(vault).as_posix(), frontmatter, body))

    raw_entries.sort(key=lambda item: _history_sort_key(item[0], item[2]))
    latest_path = raw_entries[-1][0] if raw_entries else None
    entries: list[BacklinkHistoryEntry] = []
    for path, relative, frontmatter, body in raw_entries:
        candidate_links = _summary_int(body, "Candidate links")
        applied_items = _summary_int(body, "Applied checklist items")
        skipped_notes = _summary_int(body, "Skipped protected notes")
        explicit_state = _frontmatter_scalar(frontmatter, "proposal_state")
        inferred_state = _infer_proposal_state(
            explicit_state=explicit_state,
            candidate_links=candidate_links,
            applied_checklist_items=applied_items,
            is_latest=path == latest_path,
        )
        entries.append(
            BacklinkHistoryEntry(
                path=path,
                relative_path=relative,
                created_at=_frontmatter_scalar(frontmatter, "created_at"),
                checked_at=_frontmatter_scalar(frontmatter, "checked_at"),
                status=_frontmatter_scalar(frontmatter, "status"),
                proposal_state=explicit_state,
                effective_state=inferred_state,
                candidate_links=candidate_links,
                applied_checklist_items=applied_items,
                skipped_protected_notes=skipped_notes,
                min_score=_summary_optional_int(body, "Minimum score"),
            )
        )
    counts = Counter(entry.effective_state for entry in entries)
    return BacklinkHistory(entries=entries, latest=entries[-1] if entries else None, state_counts=counts)


def render_backlink_history(history: BacklinkHistory) -> str:
    latest = history.latest.relative_path if history.latest else "None"
    return f"""Backlink Proposal History

Total proposal notes: {len(history.entries)}
Latest note: {latest}

States:
{_counter_table(history.state_counts, "state")}

Entries:
{_history_entry_lines(history.entries)}
"""


def write_backlink_history_state(vault_path: Path, *, dry_run: bool = False) -> BacklinkHistoryWriteResult:
    history = build_backlink_history(vault_path)
    changes: list[BacklinkHistoryStateChange] = []
    unchanged_count = 0
    for entry in history.entries:
        if entry.proposal_state == entry.effective_state:
            unchanged_count += 1
            continue
        changes.append(
            BacklinkHistoryStateChange(
                path=entry.path,
                relative_path=entry.relative_path,
                before_state=entry.proposal_state or "missing",
                after_state=entry.effective_state,
            )
        )
        if not dry_run:
            text = entry.path.read_text(encoding="utf-8", errors="replace")
            updated = _set_frontmatter_scalars(text, {"proposal_state": entry.effective_state})
            entry.path.write_text(updated, encoding="utf-8")
    return BacklinkHistoryWriteResult(
        dry_run=dry_run,
        changes=changes,
        unchanged_count=unchanged_count,
    )


def render_backlink_history_write_result(result: BacklinkHistoryWriteResult) -> str:
    title = "Backlink History State Write Dry Run" if result.dry_run else "Backlink History State Write"
    action = "Would update notes" if result.dry_run else "Updated notes"
    return f"""{title}

{action}: {len(result.changes)}
Unchanged notes: {result.unchanged_count}

Changes:
{_history_state_change_lines(result.changes)}
"""


def build_vault_index(vault_path: Path, *, stale_days: int = 90, max_suggestions: int = 20) -> VaultIndex:
    vault = vault_path.expanduser().resolve()
    parsed_notes = sorted(
        (_parse_note(path, vault) for path in _markdown_files(vault)),
        key=lambda note: note.relative_path,
    )
    notes = [note for note in parsed_notes if note.note_type not in {"vault-index", "backlink-proposals"}]
    ignored_pairs = _low_priority_ignored_pairs(vault)
    suggestions = _backlink_suggestions(notes, max_suggestions=max_suggestions, ignored_pairs=ignored_pairs)
    stale_notes = _stale_notes(notes, stale_days=stale_days)
    orphan_notes = _orphan_notes(notes)
    (
        orphan_review_notes,
        orphan_manual_review_notes,
        orphan_history_notes,
        orphan_reference_notes,
    ) = _split_orphan_notes(orphan_notes)
    return VaultIndex(
        notes=notes,
        suggestions=suggestions,
        stale_notes=stale_notes,
        orphan_notes=orphan_notes,
        orphan_review_notes=orphan_review_notes,
        orphan_manual_review_notes=orphan_manual_review_notes,
        orphan_history_notes=orphan_history_notes,
        orphan_reference_notes=orphan_reference_notes,
    )


def render_vault_index(
    index: VaultIndex,
    *,
    checked_at: str,
    stale_days: int,
    actionable_min_score: int = ACTIONABLE_BACKLINK_MIN_SCORE,
) -> str:
    type_counts = Counter(note.note_type or "unknown" for note in index.notes)
    status_counts = Counter(note.status or "unknown" for note in index.notes)
    topic_counts = Counter(note.topic for note in index.notes if note.topic)
    actionable_suggestions = _filter_suggestions(index.suggestions, min_score=actionable_min_score)
    low_priority_suggestions = [
        suggestion for suggestion in index.suggestions if 0 < suggestion.score < actionable_min_score
    ]
    return f"""---
type: vault-index
created_at: {yaml_scalar(checked_at)}
checked_at: {yaml_scalar(checked_at)}
status: draft
generated_by: research-agent
---

# Vault Index

## Summary

- Notes indexed: {len(index.notes)}
- Backlink suggestions: {len(actionable_suggestions)}
- Low-priority backlink signals: {len(low_priority_suggestions)}
- Orphan notes: {len(index.orphan_notes)}
- Orphan notes to review: {len(index.orphan_review_notes)}
- Manual orphan notes to review: {len(index.orphan_manual_review_notes)}
- Generated history orphan notes: {len(index.orphan_history_notes)}
- Reference orphan notes: {len(index.orphan_reference_notes)}
- Stale generated notes: {len(index.stale_notes)}

## Note Types

{_counter_table(type_counts, "type")}

## Statuses

{_counter_table(status_counts, "status")}

## Topic Clusters

{_topic_cluster_lines(index.notes, topic_counts)}

## Backlink Suggestions

Actionable suggestions at score {actionable_min_score}+.

{_suggestion_lines(actionable_suggestions)}

## Low-Priority Backlink Signals

Signals below score {actionable_min_score} are retained for inspection but excluded from proposal and health queues.

{_suggestion_lines(low_priority_suggestions)}

## Orphan Notes To Review

Generated or typed notes that are disconnected and likely worth reviewing.

{_note_lines(index.orphan_review_notes)}

## Manual Orphan Notes To Review

Manual notes with status metadata that are disconnected. These may be active artifacts worth linking, archiving, or intentionally leaving standalone.

{_note_lines(index.orphan_manual_review_notes)}

## Generated History Orphan Notes

Generated run, audit, proposal, cleanup, and health snapshots. These are usually safe to keep as chronological history even when they are disconnected.

{_note_lines(index.orphan_history_notes)}

## Reference Orphan Notes

Manual/reference notes that are disconnected and not waiting for orphan review. These are lower priority because they may be standalone reference material.

{_note_lines(index.orphan_reference_notes)}

## Stale Generated Notes

Generated notes with `checked_at` older than {stale_days} days.

{_note_lines(index.stale_notes)}
"""


def _markdown_files(vault: Path) -> list[Path]:
    if not vault.exists():
        return []
    files: list[Path] = []
    for path in vault.rglob("*.md"):
        if any(part.startswith(".") for part in path.relative_to(vault).parts):
            continue
        files.append(path)
    return files


def _parse_note(path: Path, vault: Path) -> VaultNote:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(text)
    relative = path.relative_to(vault).as_posix()
    link_target = relative[:-3] if relative.endswith(".md") else relative
    title = _title(frontmatter, body, path)
    aliases = set(_frontmatter_list(frontmatter, "aliases"))
    tags = {_normalize_tag(tag) for tag in _frontmatter_list(frontmatter, "tags")}
    topic = _frontmatter_scalar(frontmatter, "topic")
    return VaultNote(
        path=path,
        relative_path=relative,
        link_target=link_target,
        title=title,
        note_type=_frontmatter_scalar(frontmatter, "type") or "unknown",
        status=_frontmatter_scalar(frontmatter, "status"),
        topic=topic,
        checked_at=_frontmatter_scalar(frontmatter, "checked_at"),
        generated_by=_frontmatter_scalar(frontmatter, "generated_by"),
        rerun_of=_frontmatter_scalar(frontmatter, "rerun_of"),
        orphan_review=_frontmatter_scalar(frontmatter, "orphan_review"),
        aliases=aliases,
        tags={tag for tag in tags if tag},
        terms=_note_terms(title=title, topic=topic, aliases=aliases, tags=tags, link_target=link_target),
        outgoing_links=_extract_wikilinks(body),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4 :]
    values: dict[str, str | list[str]] = {}
    current_key = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            existing = values.get(current_key)
            item = stripped[2:].strip().strip('"').strip("'")
            if isinstance(existing, list):
                existing.append(item)
            elif isinstance(existing, str) and existing:
                values[current_key] = [existing, item]
            else:
                values[current_key] = [item]
            continue
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            values[current_key] = [
                item.strip().strip('"').strip("'")
                for item in cleaned[1:-1].split(",")
                if item.strip()
            ]
        else:
            values[current_key] = cleaned.strip('"').strip("'")
    return values, body


def _set_frontmatter_scalars(text: str, values: dict[str, str]) -> str:
    if not text.startswith("---\n"):
        lines = ["---", *(f"{key}: {yaml_scalar(value)}" for key, value in values.items()), "---", text.rstrip()]
        return "\n".join(lines).rstrip() + "\n"
    end = text.find("\n---", 4)
    if end == -1:
        lines = ["---", *(f"{key}: {yaml_scalar(value)}" for key, value in values.items()), "---", text.rstrip()]
        return "\n".join(lines).rstrip() + "\n"
    raw = text[4:end]
    body = text[end + 4 :]
    pending = dict(values)
    updated_lines: list[str] = []
    for line in raw.splitlines():
        if ":" in line and not line.startswith(" "):
            key = line.split(":", 1)[0].strip()
            if key in pending:
                updated_lines.append(f"{key}: {yaml_scalar(pending.pop(key))}")
                continue
        updated_lines.append(line)
    for key, value in pending.items():
        updated_lines.append(f"{key}: {yaml_scalar(value)}")
    return "---\n" + "\n".join(updated_lines).rstrip() + "\n---" + body


def _frontmatter_scalar(frontmatter: dict[str, str | list[str]], key: str) -> str:
    value = frontmatter.get(key)
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def _frontmatter_list(frontmatter: dict[str, str | list[str]], key: str) -> list[str]:
    value = frontmatter.get(key)
    if isinstance(value, list):
        return [item for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def _title(frontmatter: dict[str, str | list[str]], body: str, path: Path) -> str:
    title = _frontmatter_scalar(frontmatter, "title")
    if title:
        return title
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _extract_wikilinks(body: str) -> set[str]:
    links: set[str] = set()
    for match in WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        if target.endswith(".md"):
            target = target[:-3]
        links.add(target)
        links.add(Path(target).stem)
    return links


def _backlink_suggestions(
    notes: list[VaultNote],
    *,
    max_suggestions: int,
    ignored_pairs: set[frozenset[str]] | None = None,
) -> list[BacklinkSuggestion]:
    suggestions: list[BacklinkSuggestion] = []
    seen_pairs: set[frozenset[str]] = set()
    ignored = ignored_pairs or set()
    for index, source in enumerate(notes):
        for target in notes[index + 1 :]:
            if source == target:
                continue
            if source.note_type in SUGGESTION_IGNORED_NOTE_TYPES or target.note_type in SUGGESTION_IGNORED_NOTE_TYPES:
                continue
            pair_key = frozenset({source.relative_path, target.relative_path})
            if pair_key in ignored:
                continue
            if pair_key in seen_pairs:
                continue
            if source.note_type == target.note_type:
                continue
            if _is_linked(source, target) or _is_linked(target, source):
                continue
            score, reason = _rerun_lineage_suggestion_score(source, target)
            if score <= 0:
                score, reason = _suggestion_score(source, target)
            if score > 0:
                seen_pairs.add(pair_key)
                ordered_source, ordered_target = _suggestion_direction(source, target)
                suggestions.append(BacklinkSuggestion(source=ordered_source, target=ordered_target, reason=reason, score=score))
    suggestions.sort(key=lambda item: (-item.score, item.source.relative_path, item.target.relative_path))
    return suggestions[:max_suggestions]


def _low_priority_ignored_pairs(vault: Path) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") != "low-priority-backlink-review":
            continue
        if _frontmatter_scalar(frontmatter, "proposal_state").lower() != "applied":
            continue
        records = _low_priority_candidate_records(text)
        checked_ids = {
            match.group("proposal_id")
            for match in LOW_PRIORITY_BACKLINK_CHECK_RE.finditer(text)
            if match.group("state").strip().lower() == "x"
        }
        for proposal_id in checked_ids:
            record = records.get(proposal_id)
            if not record:
                continue
            source = record.get("source_path", "").strip()
            target = record.get("target_path", "").strip()
            if source and target:
                pairs.add(frozenset({source, target}))
    return pairs


def _low_priority_candidate_records(text: str) -> dict[str, dict[str, str]]:
    match = LOW_PRIORITY_CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        import json

        items = json.loads(match.group("json"))
    except (ImportError, ValueError):
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        proposal_id = str(item.get("proposal_id") or "").strip()
        source = str(item.get("source_path") or "").strip()
        target = str(item.get("target_path") or "").strip()
        if proposal_id and source and target:
            records[proposal_id] = {"source_path": source, "target_path": target}
    return records


def _suggestion_score(source: VaultNote, target: VaultNote) -> tuple[int, str]:
    exact_topic = bool(source.topic and target.topic and _normalize_topic(source.topic) == _normalize_topic(target.topic))
    shared_tags = sorted(source.tags & target.tags)
    shared_terms = sorted(source.terms & target.terms)

    pair = {source.note_type, target.note_type}
    if exact_topic and pair == {"service-blueprint", "evidence-ledger"}:
        return 5, "same topic; blueprint and evidence ledger should cross-link"
    if exact_topic and "topic-map" in pair:
        return 4, "same topic; topic map should connect related run artifacts"
    if "run-log" in pair:
        return 0, ""
    if exact_topic and "source-note" in pair and ("evidence-ledger" in pair or "service-blueprint" in pair):
        return 3, "same topic; source note should support synthesis or evidence review"
    if exact_topic:
        return 2, "same topic and different note types"

    if shared_tags:
        tag_text = ", ".join(shared_tags[:4])
        return 3, f"shared tags: {tag_text}"

    if len(shared_terms) >= 2:
        term_text = ", ".join(shared_terms[:5])
        bonus = 1 if _connects_generated_to_existing(source, target) else 0
        reason = f"shared terms: {term_text}"
        if bonus:
            reason += "; connects generated research with existing vault note"
        return min(4, 2 + bonus), reason

    return 0, ""


def _rerun_lineage_suggestion_score(source: VaultNote, target: VaultNote) -> tuple[int, str]:
    if {source.note_type, target.note_type} != {"run-log", "topic-map"}:
        return 0, ""
    if not source.rerun_of or not target.rerun_of or source.rerun_of != target.rerun_of:
        return 0, ""
    if source.topic and target.topic and _normalize_topic(source.topic) != _normalize_topic(target.topic):
        return 0, ""
    return 6, f"same rerun lineage `{source.rerun_of}`; connect rerun run log and topic map"


def _suggestion_direction(source: VaultNote, target: VaultNote) -> tuple[VaultNote, VaultNote]:
    pair = {source.note_type, target.note_type}
    if pair == {"service-blueprint", "evidence-ledger"}:
        return _with_type_first(source, target, "service-blueprint")
    if "topic-map" in pair:
        return _with_type_first(source, target, "topic-map")
    if pair == {"source-note", "evidence-ledger"}:
        return _with_type_first(source, target, "source-note")
    if pair == {"source-note", "service-blueprint"}:
        return _with_type_first(source, target, "source-note")
    if source.generated_by == "research-agent" and target.generated_by != "research-agent":
        return source, target
    if target.generated_by == "research-agent" and source.generated_by != "research-agent":
        return target, source
    return (source, target) if source.relative_path < target.relative_path else (target, source)


def _with_type_first(source: VaultNote, target: VaultNote, note_type: str) -> tuple[VaultNote, VaultNote]:
    return (source, target) if source.note_type == note_type else (target, source)


def _connects_generated_to_existing(source: VaultNote, target: VaultNote) -> bool:
    return (source.generated_by == "research-agent") != (target.generated_by == "research-agent")


def _is_linked(source: VaultNote, target: VaultNote) -> bool:
    return target.link_target in source.outgoing_links or Path(target.link_target).stem in source.outgoing_links


def _stale_notes(notes: list[VaultNote], *, stale_days: int) -> list[VaultNote]:
    today = date.today()
    stale: list[VaultNote] = []
    for note in notes:
        if note.generated_by != "research-agent" or not note.checked_at:
            continue
        try:
            checked = date.fromisoformat(note.checked_at[:10])
        except ValueError:
            continue
        if (today - checked).days > stale_days:
            stale.append(note)
    return stale


def _orphan_notes(notes: list[VaultNote]) -> list[VaultNote]:
    incoming = Counter()
    by_target = {note.link_target: note for note in notes}
    by_stem = {Path(note.link_target).stem: note for note in notes}
    for note in notes:
        if note.note_type in ORPHAN_INCOMING_IGNORED_NOTE_TYPES:
            continue
        for link in note.outgoing_links:
            target = by_target.get(link) or by_stem.get(link)
            if target:
                incoming[target.relative_path] += 1
    return [note for note in notes if not note.outgoing_links and incoming[note.relative_path] == 0]


def _split_orphan_notes(
    notes: list[VaultNote],
) -> tuple[list[VaultNote], list[VaultNote], list[VaultNote], list[VaultNote]]:
    review: list[VaultNote] = []
    manual_review: list[VaultNote] = []
    history: list[VaultNote] = []
    reference: list[VaultNote] = []
    for note in notes:
        if _is_generated_history_orphan(note):
            history.append(note)
        elif _is_review_orphan(note):
            review.append(note)
        elif _is_manual_review_orphan(note):
            manual_review.append(note)
        else:
            reference.append(note)
    return review, manual_review, history, reference


def _is_generated_history_orphan(note: VaultNote) -> bool:
    return note.relative_path.startswith("60_Runs/") and (
        note.note_type in GENERATED_HISTORY_NOTE_TYPES or note.generated_by == "research-agent"
    )


def _is_review_orphan(note: VaultNote) -> bool:
    return note.generated_by == "research-agent" or note.note_type != "unknown"


def _is_manual_review_orphan(note: VaultNote) -> bool:
    resolved = {"archived", "ignored", "linked", "standalone"}
    return bool(note.status) and note.status.lower() != "archived" and note.orphan_review.lower() not in resolved


def _counter_table(counter: Counter[str], label: str) -> str:
    if not counter:
        return "- None."
    rows = [f"| {label} | count |", "|---|---|"]
    for key, count in sorted(counter.items()):
        rows.append(f"| {key} | {count} |")
    return "\n".join(rows)


def _topic_cluster_lines(notes: list[VaultNote], topic_counts: Counter[str]) -> str:
    if not topic_counts:
        return "- No topics found."
    lines: list[str] = []
    notes_by_topic: dict[str, list[VaultNote]] = {}
    for note in notes:
        if note.topic:
            notes_by_topic.setdefault(note.topic, []).append(note)
    for topic, count in topic_counts.most_common():
        note_links = ", ".join(_wikilink(note) for note in notes_by_topic.get(topic, [])[:5])
        suffix = f" plus {count - 5} more" if count > 5 else ""
        lines.append(f"- {topic}: {count} notes - {note_links}{suffix}")
    return "\n".join(lines)


def _suggestion_lines(suggestions: list[BacklinkSuggestion]) -> str:
    if not suggestions:
        return "- No backlink suggestions."
    rows = ["| score | source | target | reason |", "|---|---|---|---|"]
    for item in suggestions:
        rows.append(f"| {item.score} | {_wikilink(item.source)} | {_wikilink(item.target)} | {item.reason} |")
    return "\n".join(rows)


def _proposal_lines(suggestions: list[BacklinkSuggestion]) -> str:
    if not suggestions:
        return "- None."
    return "\n".join(
        f"- score {item.score}: {_wikilink(item.source)} -> {_wikilink(item.target)}; reason: {item.reason}; checklist: `{_proposal_checklist_item(item)}`"
        for item in suggestions
    )


def _filter_suggestions(suggestions: list[BacklinkSuggestion], *, min_score: int) -> list[BacklinkSuggestion]:
    return [suggestion for suggestion in suggestions if suggestion.score >= min_score]


def _split_appendable_suggestions(
    suggestions: list[BacklinkSuggestion],
    *,
    include_reviewed: bool,
) -> tuple[list[BacklinkSuggestion], list[BacklinkSuggestion]]:
    appendable: list[BacklinkSuggestion] = []
    skipped: list[BacklinkSuggestion] = []
    for suggestion in suggestions:
        if not include_reviewed and suggestion.source.status.lower() in REVIEWED_STATUSES:
            skipped.append(suggestion)
        else:
            appendable.append(suggestion)
    return appendable, skipped


def _append_backlink_proposals(
    suggestions: list[BacklinkSuggestion],
) -> tuple[list[BacklinkSuggestion], list[Path]]:
    grouped: dict[Path, list[BacklinkSuggestion]] = {}
    for suggestion in suggestions:
        grouped.setdefault(suggestion.source.path, []).append(suggestion)

    applied: list[BacklinkSuggestion] = []
    appended_paths: list[Path] = []
    for path, items in grouped.items():
        text = path.read_text(encoding="utf-8")
        outgoing_links = _extract_wikilinks(text)
        lines: list[str] = []
        for item in items:
            if item.target.link_target in outgoing_links or Path(item.target.link_target).stem in outgoing_links:
                continue
            line = _proposal_checklist_item(item)
            if line in text:
                continue
            lines.append(line)
            applied.append(item)
            outgoing_links.add(item.target.link_target)
            outgoing_links.add(Path(item.target.link_target).stem)
        if not lines:
            continue
        path.write_text(_append_proposal_lines(text, lines), encoding="utf-8")
        appended_paths.append(path)
    return applied, appended_paths


def _append_proposal_lines(text: str, lines: list[str]) -> str:
    block = "\n".join(lines)
    if "## Backlink Proposals" in text:
        return text.rstrip() + "\n" + block + "\n"
    return text.rstrip() + "\n\n## Backlink Proposals\n\n" + block + "\n"


def _proposal_checklist_item(suggestion: BacklinkSuggestion) -> str:
    return f"- [ ] Add {_wikilink(suggestion.target)} (score {suggestion.score}): {suggestion.reason}"


def _supersede_previous_proposals(vault_path: Path, new_path: Path, *, checked_at: str) -> list[Path]:
    vault = vault_path.expanduser().resolve()
    new_path = new_path.resolve()
    superseded: list[Path] = []
    new_relative = new_path.relative_to(vault).as_posix()
    for entry in build_backlink_history(vault).entries:
        if entry.path.resolve() == new_path or entry.effective_state != "proposed":
            continue
        text = entry.path.read_text(encoding="utf-8", errors="replace")
        updated = _set_frontmatter_scalars(
            text,
            {
                "proposal_state": "superseded",
                "superseded_at": checked_at,
                "superseded_by": new_relative,
            },
        )
        entry.path.write_text(updated, encoding="utf-8")
        superseded.append(entry.path)
    return superseded


def _history_sort_key(path: Path, frontmatter: dict[str, str | list[str]]) -> tuple[str, str, int, float, str]:
    checked_at = _frontmatter_scalar(frontmatter, "checked_at")
    created_at = _frontmatter_scalar(frontmatter, "created_at")
    variant = _filename_variant(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return checked_at, created_at, variant, mtime, path.as_posix()


def _filename_variant(path: Path) -> int:
    match = re.match(r"^.+-(\d+)$", path.stem)
    if not match:
        return 1
    return int(match.group(1))


def _summary_int(body: str, label: str) -> int:
    return _summary_optional_int(body, label) or 0


def _summary_optional_int(body: str, label: str) -> int | None:
    match = re.search(rf"^- {re.escape(label)}:\s*(\d+)\s*$", body, re.MULTILINE)
    return int(match.group(1)) if match else None


def _infer_proposal_state(
    *,
    explicit_state: str,
    candidate_links: int,
    applied_checklist_items: int,
    is_latest: bool,
) -> str:
    if explicit_state:
        return explicit_state
    if applied_checklist_items > 0:
        return "applied"
    if candidate_links == 0:
        return "empty"
    return "proposed" if is_latest else "superseded"


def _history_entry_lines(entries: list[BacklinkHistoryEntry]) -> str:
    if not entries:
        return "- None."
    rows = ["| state | note | candidates | applied | skipped | checked_at |", "|---|---|---|---|---|---|"]
    for entry in reversed(entries):
        checked_at = entry.checked_at or "unknown"
        rows.append(
            f"| {entry.effective_state} | {entry.relative_path} | {entry.candidate_links} | {entry.applied_checklist_items} | {entry.skipped_protected_notes} | {checked_at} |"
        )
    return "\n".join(rows)


def _history_state_change_lines(changes: list[BacklinkHistoryStateChange]) -> str:
    if not changes:
        return "- None."
    rows = ["| note | before | after |", "|---|---|---|"]
    for change in changes:
        rows.append(f"| {change.relative_path} | {change.before_state} | {change.after_state} |")
    return "\n".join(rows)


def _parse_backlink_review_items(path: Path, vault: Path) -> list[BacklinkReviewItem]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = BACKLINK_PROPOSALS_HEADING_RE.search(text)
    if not match:
        return []
    before = text[: match.start()]
    after = text[match.end() :]
    relative = path.relative_to(vault).as_posix()
    body_links = _extract_wikilinks(before)
    items: list[BacklinkReviewItem] = []
    for line in after.splitlines():
        match = BACKLINK_PROPOSAL_RE.match(line.strip())
        if not match:
            continue
        link = match.group("link")
        link_match = WIKILINK_RE.search(link)
        if not link_match:
            continue
        target = link_match.group(1).strip()
        if target.endswith(".md"):
            target = target[:-3]
        score_text = match.group("score")
        items.append(
            BacklinkReviewItem(
                source_path=path,
                relative_path=relative,
                target=target,
                line=line.strip(),
                checked=match.group("state").lower() == "x",
                resolved=target in body_links or Path(target).stem in body_links,
                score=int(score_text) if score_text else None,
                reason=match.group("reason") or "",
            )
        )
    return items


def _add_related_note_link(text: str, target: str) -> tuple[str, bool]:
    target = target[:-3] if target.endswith(".md") else target
    stem = Path(target).stem
    before_proposals = _before_backlink_proposals(text)
    if target in _extract_wikilinks(before_proposals) or stem in _extract_wikilinks(before_proposals):
        return text, False

    link_line = f"- [[{target}|{stem}]]"
    related_match = RELATED_NOTES_HEADING_RE.search(text)
    if related_match:
        next_heading = _next_h2_start(text, related_match.end())
        section = text[related_match.start() : next_heading]
        if target in _extract_wikilinks(section) or stem in _extract_wikilinks(section):
            return text, False
        updated_section = section.rstrip() + "\n\n" + link_line + "\n\n"
        return text[: related_match.start()] + updated_section + text[next_heading:], True

    proposal_match = BACKLINK_PROPOSALS_HEADING_RE.search(text)
    related_section = "\n\n## Related Notes\n\n" + link_line + "\n"
    if proposal_match:
        return text[: proposal_match.start()].rstrip() + related_section + "\n" + text[proposal_match.start() :], True
    return text.rstrip() + related_section, True


def _before_backlink_proposals(text: str) -> str:
    match = BACKLINK_PROPOSALS_HEADING_RE.search(text)
    return text[: match.start()] if match else text


def _next_h2_start(text: str, start: int) -> int:
    match = H2_HEADING_RE.search(text, start)
    return match.start() if match else len(text)


def _review_item_key(item: BacklinkReviewItem) -> tuple[str, str, int]:
    score = item.score if item.score is not None else -1
    return item.relative_path, item.target, -score


def _review_note_counts(items: list[BacklinkReviewItem]) -> str:
    if not items:
        return "- None."
    counts = Counter(item.relative_path for item in items)
    return "\n".join(f"- {path}: {count}" for path, count in sorted(counts.items()))


def _review_item_lines(items: list[BacklinkReviewItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(
        f"- {item.relative_path} -> [[{item.target}|{Path(item.target).stem}]]"
        for item in items
    )


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _note_lines(notes: list[VaultNote]) -> str:
    if not notes:
        return "- None."
    return "\n".join(f"- {_wikilink(note)} ({note.note_type or 'unknown'}, {note.status or 'no status'})" for note in notes)


def _wikilink(note: VaultNote) -> str:
    return f"[[{note.link_target}|{Path(note.link_target).stem}]]"


def _note_terms(*, title: str, topic: str, aliases: set[str], tags: set[str], link_target: str) -> set[str]:
    parts = [title, topic, Path(link_target).stem, *aliases, *tags]
    terms: set[str] = set()
    for part in parts:
        terms.update(_tokenize(part))
    return terms


def _tokenize(text: str) -> set[str]:
    tokens = set()
    for match in TOKEN_RE.finditer(text.lower().replace("-", " ")):
        token = match.group(0)
        if len(token) < 2 or token in STOPWORDS or token.isdigit():
            continue
        tokens.add(token)
    return tokens


def _normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").replace(" ", "-").lower()


def _normalize_topic(topic: str) -> str:
    return " ".join(sorted(_tokenize(topic))) or topic.strip().lower()
