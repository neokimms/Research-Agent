from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .low_priority_backlink_review import build_low_priority_backlink_queue, build_low_priority_backlink_review
from .manual_orphan_review import build_manual_orphan_queue, build_manual_orphan_review
from .obsidian import ObsidianWriter
from .review_promotion import build_review_promotion
from .run_cleanup import build_run_cleanup, build_run_cleanup_queue
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_health import FAIL, WARN, VaultHealthReport, build_vault_health
from .vault_index import build_backlink_proposal_suggestions, build_backlink_review_queue, build_vault_index
from .verification_cleanup import build_verification_cleanup


@dataclass(frozen=True)
class NextActionItem:
    priority: int
    category: str
    title: str
    count: int
    command: str
    detail: str


@dataclass(frozen=True)
class NextActionsReport:
    vault_path: Path
    health: VaultHealthReport
    items: list[NextActionItem]

    @property
    def action_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class NextActionsWriteResult:
    report: NextActionsReport
    note_path: Path


def build_next_actions(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
    max_items: int = 20,
) -> NextActionsReport:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    health = build_vault_health(
        settings,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
        min_score=min_score,
    )
    items: list[NextActionItem] = []

    if health.status == FAIL:
        items.append(
            NextActionItem(
                priority=1,
                category="health",
                title="Fix failing vault health checks",
                count=1,
                command="vault-health",
                detail="Health status is FAIL. Inspect failing checks before applying queue actions.",
            )
        )

    _append_audit_items(items, health)

    backlink_queue = build_backlink_review_queue(vault)
    checked_ready = [item for item in backlink_queue.completed if not item.resolved]
    if checked_ready:
        items.append(
            NextActionItem(
                priority=1,
                category="backlinks",
                title="Apply checked backlink checklist items",
                count=len(checked_ready),
                command="apply-reviewed-backlinks --dry-run",
                detail=_sample_paths(item.relative_path for item in checked_ready),
            )
        )
    if backlink_queue.pending:
        items.append(
            NextActionItem(
                priority=2,
                category="backlinks",
                title="Review pending backlink checklist items",
                count=len(backlink_queue.pending),
                command="review-backlinks",
                detail=_sample_paths(item.relative_path for item in backlink_queue.pending),
            )
        )

    actionable_backlinks = build_backlink_proposal_suggestions(
        vault,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
        min_score=min_score,
    )
    if actionable_backlinks:
        items.append(
            NextActionItem(
                priority=2,
                category="backlinks",
                title="Create actionable backlink proposals",
                count=len(actionable_backlinks),
                command=f"backlink-proposals --write-note --min-score {min_score}",
                detail=_sample_paths(f"{item.source.relative_path} -> {item.target.relative_path}" for item in actionable_backlinks),
            )
        )

    review_promotion = build_review_promotion(settings, max_proposals=max_items)
    if review_promotion.candidates:
        items.append(
            NextActionItem(
                priority=2,
                category="review",
                title="Promote generated notes ready for reviewed status",
                count=len(review_promotion.candidates),
                command="review-promotion-proposals --write-note",
                detail=_sample_paths(candidate.relative_path for candidate in review_promotion.candidates),
            )
        )

    verification_cleanup = build_verification_cleanup(settings, max_proposals=max_items)
    if verification_cleanup.candidates:
        items.append(
            NextActionItem(
                priority=2,
                category="cleanup",
                title="Clean stale verification text",
                count=len(verification_cleanup.candidates),
                command="verification-cleanup --write-note",
                detail=_sample_paths(candidate.relative_path for candidate in verification_cleanup.candidates),
            )
        )

    manual_queue = build_manual_orphan_queue(vault)
    if manual_queue.checked_actions:
        items.append(
            NextActionItem(
                priority=2,
                category="manual-review",
                title="Apply checked manual orphan actions",
                count=manual_queue.checked_actions,
                command="apply-manual-orphan-review --dry-run",
                detail=_sample_paths(path.relative_to(vault).as_posix() for path in manual_queue.checked_proposal_paths),
            )
        )
    elif manual_queue.pending_candidates:
        items.append(
            NextActionItem(
                priority=3,
                category="manual-review",
                title="Review existing manual orphan proposal note",
                count=manual_queue.pending_candidates,
                command="apply-manual-orphan-review --dry-run",
                detail="Check one action per candidate in " + _sample_paths(path.relative_to(vault).as_posix() for path in manual_queue.proposal_paths),
            )
        )
    else:
        manual_orphans = build_manual_orphan_review(settings, max_proposals=max_items)
        if manual_orphans.candidates:
            items.append(
                NextActionItem(
                    priority=3,
                    category="manual-review",
                    title="Create manual orphan proposal note",
                    count=len(manual_orphans.candidates),
                    command="manual-orphan-proposals --write-note",
                    detail=_sample_paths(candidate.relative_path for candidate in manual_orphans.candidates),
                )
            )

    low_priority_queue = build_low_priority_backlink_queue(vault)
    if low_priority_queue.checked_ignores:
        items.append(
            NextActionItem(
                priority=2,
                category="backlinks",
                title="Apply checked low-priority backlink ignores",
                count=low_priority_queue.checked_ignores,
                command="apply-low-priority-backlinks --dry-run",
                detail=_sample_paths(path.relative_to(vault).as_posix() for path in low_priority_queue.checked_proposal_paths),
            )
        )
    elif low_priority_queue.pending_candidates:
        items.append(
            NextActionItem(
                priority=3,
                category="backlinks",
                title="Review existing low-priority backlink proposal note",
                count=low_priority_queue.pending_candidates,
                command="apply-low-priority-backlinks --dry-run",
                detail="Check Ignore items in " + _sample_paths(path.relative_to(vault).as_posix() for path in low_priority_queue.proposal_paths),
            )
        )
    else:
        low_priority_backlinks = build_low_priority_backlink_review(
            settings,
            stale_days=stale_days,
            max_suggestions=max_suggestions,
            max_proposals=max_items,
            min_score=min_score,
        )
        if low_priority_backlinks.candidates:
            items.append(
                NextActionItem(
                    priority=3,
                    category="backlinks",
                    title="Create low-priority backlink proposal note",
                    count=len(low_priority_backlinks.candidates),
                    command="low-priority-backlink-proposals --write-note",
                    detail=_sample_paths(f"{item.source_path} -> {item.target_path}" for item in low_priority_backlinks.candidates),
                )
            )

    run_cleanup_queue = build_run_cleanup_queue(vault)
    if run_cleanup_queue.checked_items:
        items.append(
            NextActionItem(
                priority=2,
                category="cleanup",
                title="Apply checked run cleanup archive actions",
                count=run_cleanup_queue.checked_items,
                command="apply-run-cleanup --dry-run",
                detail=_sample_paths(path.relative_to(vault).as_posix() for path in run_cleanup_queue.checked_proposal_paths),
            )
        )
    elif run_cleanup_queue.pending_candidates:
        items.append(
            NextActionItem(
                priority=3,
                category="cleanup",
                title="Review existing run cleanup proposal note",
                count=run_cleanup_queue.pending_candidates,
                command="apply-run-cleanup --dry-run",
                detail="Check archive items in " + _sample_paths(path.relative_to(vault).as_posix() for path in run_cleanup_queue.proposal_paths),
            )
        )
    else:
        run_cleanup = build_run_cleanup(settings, max_proposals=max_items)
        if run_cleanup.candidates:
            items.append(
                NextActionItem(
                    priority=3,
                    category="cleanup",
                    title="Create run cleanup proposal note",
                    count=len(run_cleanup.candidates),
                    command="run-cleanup-proposals --write-note",
                    detail=_sample_paths(candidate.relative_path for candidate in run_cleanup.candidates),
                )
            )

    index = build_vault_index(vault, stale_days=stale_days, max_suggestions=max_suggestions)
    if index.stale_notes:
        items.append(
            NextActionItem(
                priority=3,
                category="maintenance",
                title="Review stale generated notes",
                count=len(index.stale_notes),
                command="index-vault",
                detail=_sample_paths(note.relative_path for note in index.stale_notes),
            )
        )

    ordered = sorted(items, key=lambda item: (item.priority, item.category, item.title))
    return NextActionsReport(vault_path=vault, health=health, items=ordered)


def render_next_actions(report: NextActionsReport) -> str:
    return f"""Next Actions

Vault: {report.vault_path}
Health: {report.health.status}
Notes indexed: {report.health.notes_indexed}
Action groups: {report.action_count}

Actions:
{_action_lines(report.items)}
"""


def write_next_actions_note(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
    max_items: int = 20,
) -> NextActionsWriteResult:
    report = build_next_actions(
        settings,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
        min_score=min_score,
        max_items=max_items,
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
        f"{timestamp.date().isoformat()}_next-actions.md",
        render_next_actions_note(report, checked_at=timestamp.isoformat(timespec="seconds")),
    )
    return NextActionsWriteResult(report=report, note_path=path)


def render_next_actions_note(report: NextActionsReport, *, checked_at: str) -> str:
    return f"""---
type: {yaml_scalar("next-actions")}
status: {yaml_scalar("draft")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
health_status: {yaml_scalar(report.health.status)}
notes_indexed: {report.health.notes_indexed}
action_count: {report.action_count}
---
# Next Actions

## Summary

| metric | value |
|---|---:|
| health | {report.health.status} |
| notes indexed | {report.health.notes_indexed} |
| action groups | {report.action_count} |

## Actions

{_action_lines(report.items)}
"""


def _append_audit_items(items: list[NextActionItem], health: VaultHealthReport) -> None:
    if health.source_failures:
        items.append(
            NextActionItem(1, "audit", "Fix source audit failures", health.source_failures, "source-audit --write-note", "Source audit has failures.")
        )
    elif health.source_warnings:
        items.append(
            NextActionItem(2, "audit", "Review source audit warnings", health.source_warnings, "source-audit --write-note", "Source audit has warnings.")
        )

    if health.bilingual_failures:
        items.append(
            NextActionItem(1, "audit", "Fix bilingual audit failures", health.bilingual_failures, "bilingual-audit --write-note", "Bilingual audit has failures.")
        )
    elif health.bilingual_warnings:
        items.append(
            NextActionItem(2, "audit", "Review bilingual audit warnings", health.bilingual_warnings, "bilingual-audit --write-note", "Bilingual audit has warnings.")
        )


def _action_lines(items: list[NextActionItem]) -> str:
    if not items:
        return "- No immediate actions. Vault is clear at the configured thresholds."
    lines: list[str] = []
    for item in items:
        lines.append(f"- P{item.priority} [{item.category}] {item.title} ({item.count})")
        lines.append(f"  - Command: `{item.command}`")
        lines.append(f"  - Detail: {item.detail}")
    return "\n".join(lines)


def _sample_paths(values: object, *, limit: int = 3) -> str:
    items = [str(value) for value in values]
    if not items:
        return "No examples."
    shown = ", ".join(items[:limit])
    hidden = len(items) - limit
    if hidden > 0:
        return f"{shown}, plus {hidden} more"
    return shown
