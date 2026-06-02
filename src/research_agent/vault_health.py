from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .bilingual_audit import run_bilingual_audit
from .config import Settings
from .obsidian import ObsidianWriter, REVIEWED_STATUSES
from .review_promotion import build_review_promotion
from .run_cleanup import build_run_cleanup
from .source_audit import run_source_audit
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import (
    build_backlink_proposal_suggestions,
    build_backlink_review_queue,
    build_vault_index,
)
from .verification_cleanup import build_verification_cleanup


OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
CORE_NOTE_TYPES = {"source-note", "evidence-ledger", "service-blueprint", "topic-map"}


@dataclass(frozen=True)
class VaultHealthCheck:
    status: str
    name: str
    detail: str


@dataclass(frozen=True)
class VaultHealthReport:
    vault_path: Path
    checks: list[VaultHealthCheck]
    notes_indexed: int
    status_counts: Counter[str]
    type_counts: Counter[str]
    reviewed_core_notes: int
    draft_core_notes: int
    source_failures: int
    source_warnings: int
    bilingual_failures: int
    bilingual_warnings: int
    backlink_candidates: int
    pending_backlinks: int
    checked_backlinks: int
    run_cleanup_candidates: int
    stale_generated_notes: int
    review_promotion_candidates: int
    verification_cleanup_candidates: int

    @property
    def status(self) -> str:
        if any(check.status == FAIL for check in self.checks):
            return FAIL
        if any(check.status == WARN for check in self.checks):
            return WARN
        return OK

    @property
    def has_failures(self) -> bool:
        return self.status == FAIL


@dataclass(frozen=True)
class VaultHealthWriteResult:
    report: VaultHealthReport
    note_path: Path


def build_vault_health(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
) -> VaultHealthReport:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    checks: list[VaultHealthCheck] = []
    if not vault.exists() or not vault.is_dir():
        detail = "vault path does not exist" if not vault.exists() else "vault path is not a directory"
        return VaultHealthReport(
            vault_path=vault,
            checks=[VaultHealthCheck(FAIL, "vault path", detail)],
            notes_indexed=0,
            status_counts=Counter(),
            type_counts=Counter(),
            reviewed_core_notes=0,
            draft_core_notes=0,
            source_failures=0,
            source_warnings=0,
            bilingual_failures=0,
            bilingual_warnings=0,
            backlink_candidates=0,
            pending_backlinks=0,
            checked_backlinks=0,
            run_cleanup_candidates=0,
            stale_generated_notes=0,
            review_promotion_candidates=0,
            verification_cleanup_candidates=0,
        )

    index = build_vault_index(vault, stale_days=stale_days, max_suggestions=max_suggestions)
    source_audit = run_source_audit(vault)
    bilingual_audit = run_bilingual_audit(vault)
    backlink_candidates = build_backlink_proposal_suggestions(
        vault,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
        min_score=min_score,
    )
    backlink_queue = build_backlink_review_queue(vault)
    run_cleanup = build_run_cleanup(settings)
    review_promotion = build_review_promotion(settings)
    verification_cleanup = build_verification_cleanup(settings)

    status_counts = Counter(note.status or "unknown" for note in index.notes)
    type_counts = Counter(note.note_type or "unknown" for note in index.notes)
    core_notes = [
        note
        for note in index.notes
        if note.note_type in CORE_NOTE_TYPES and note.generated_by == "research-agent"
    ]
    reviewed_core_notes = sum(1 for note in core_notes if note.status.lower() in REVIEWED_STATUSES)
    draft_core_notes = sum(1 for note in core_notes if note.status.lower() == "draft")
    checked_ready = [item for item in backlink_queue.completed if not item.resolved]

    checks.append(VaultHealthCheck(OK, "vault path", str(vault)))
    checks.append(_source_audit_check(source_audit.failure_count, source_audit.warning_count))
    checks.append(_bilingual_audit_check(bilingual_audit.failure_count, bilingual_audit.warning_count))
    checks.append(_core_review_check(len(core_notes), reviewed_core_notes, draft_core_notes))
    checks.append(_backlink_candidate_check(len(backlink_candidates), min_score))
    checks.append(_backlink_queue_check(len(backlink_queue.pending), len(checked_ready)))
    checks.append(_run_cleanup_check(len(run_cleanup.candidates)))
    checks.append(_stale_note_check(len(index.stale_notes), stale_days))
    checks.append(_review_promotion_check(len(review_promotion.candidates)))
    checks.append(_verification_cleanup_check(len(verification_cleanup.candidates)))

    return VaultHealthReport(
        vault_path=vault,
        checks=checks,
        notes_indexed=len(index.notes),
        status_counts=status_counts,
        type_counts=type_counts,
        reviewed_core_notes=reviewed_core_notes,
        draft_core_notes=draft_core_notes,
        source_failures=source_audit.failure_count,
        source_warnings=source_audit.warning_count,
        bilingual_failures=bilingual_audit.failure_count,
        bilingual_warnings=bilingual_audit.warning_count,
        backlink_candidates=len(backlink_candidates),
        pending_backlinks=len(backlink_queue.pending),
        checked_backlinks=len(checked_ready),
        run_cleanup_candidates=len(run_cleanup.candidates),
        stale_generated_notes=len(index.stale_notes),
        review_promotion_candidates=len(review_promotion.candidates),
        verification_cleanup_candidates=len(verification_cleanup.candidates),
    )


def render_vault_health(report: VaultHealthReport) -> str:
    return f"""Vault Health

Vault: {report.vault_path}
Overall status: {report.status}
Notes indexed: {report.notes_indexed}

Checks:
{_check_lines(report.checks)}

Key Metrics:
- Reviewed core notes: {report.reviewed_core_notes}
- Draft core notes: {report.draft_core_notes}
- Source audit failures/warnings: {report.source_failures}/{report.source_warnings}
- Bilingual audit failures/warnings: {report.bilingual_failures}/{report.bilingual_warnings}
- Backlink candidates: {report.backlink_candidates}
- Pending backlink checklist items: {report.pending_backlinks}
- Checked backlink items ready to apply: {report.checked_backlinks}
- Run cleanup candidates: {report.run_cleanup_candidates}
- Stale generated notes: {report.stale_generated_notes}
- Review promotion candidates: {report.review_promotion_candidates}
- Verification cleanup candidates: {report.verification_cleanup_candidates}

Statuses:
{_counter_lines(report.status_counts)}

Note Types:
{_counter_lines(report.type_counts)}
"""


def write_vault_health_note(
    settings: Settings,
    *,
    stale_days: int = 90,
    max_suggestions: int = 20,
    min_score: int = 3,
) -> VaultHealthWriteResult:
    report = build_vault_health(
        settings,
        stale_days=stale_days,
        max_suggestions=max_suggestions,
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
        f"{timestamp.date().isoformat()}_vault-health.md",
        render_vault_health_note(report, checked_at=timestamp.isoformat(timespec="seconds")),
    )
    return VaultHealthWriteResult(report=report, note_path=path)


def render_vault_health_note(report: VaultHealthReport, *, checked_at: str) -> str:
    return f"""---
type: {yaml_scalar("vault-health")}
status: {yaml_scalar("draft")}
health_status: {yaml_scalar(report.status)}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
notes_indexed: {report.notes_indexed}
reviewed_core_notes: {report.reviewed_core_notes}
draft_core_notes: {report.draft_core_notes}
source_failures: {report.source_failures}
source_warnings: {report.source_warnings}
bilingual_failures: {report.bilingual_failures}
bilingual_warnings: {report.bilingual_warnings}
backlink_candidates: {report.backlink_candidates}
pending_backlinks: {report.pending_backlinks}
run_cleanup_candidates: {report.run_cleanup_candidates}
stale_generated_notes: {report.stale_generated_notes}
---
# Vault Health

## Summary

| metric | value |
|---|---:|
| overall status | {report.status} |
| notes indexed | {report.notes_indexed} |
| reviewed core notes | {report.reviewed_core_notes} |
| draft core notes | {report.draft_core_notes} |
| source audit failures/warnings | {report.source_failures}/{report.source_warnings} |
| bilingual audit failures/warnings | {report.bilingual_failures}/{report.bilingual_warnings} |
| backlink candidates | {report.backlink_candidates} |
| pending backlink checklist items | {report.pending_backlinks} |
| checked backlink items ready to apply | {report.checked_backlinks} |
| run cleanup candidates | {report.run_cleanup_candidates} |
| stale generated notes | {report.stale_generated_notes} |
| review promotion candidates | {report.review_promotion_candidates} |
| verification cleanup candidates | {report.verification_cleanup_candidates} |

## Checks

{_check_lines(report.checks)}

## Statuses

{_counter_lines(report.status_counts)}

## Note Types

{_counter_lines(report.type_counts)}
"""


def _source_audit_check(failures: int, warnings: int) -> VaultHealthCheck:
    if failures:
        return VaultHealthCheck(FAIL, "source audit", f"{failures} failure(s), {warnings} warning(s)")
    if warnings:
        return VaultHealthCheck(WARN, "source audit", f"0 failures, {warnings} warning(s)")
    return VaultHealthCheck(OK, "source audit", "PASS with 0 warnings")


def _bilingual_audit_check(failures: int, warnings: int) -> VaultHealthCheck:
    if failures:
        return VaultHealthCheck(FAIL, "bilingual audit", f"{failures} failure(s), {warnings} warning(s)")
    if warnings:
        return VaultHealthCheck(WARN, "bilingual audit", f"0 failures, {warnings} warning(s)")
    return VaultHealthCheck(OK, "bilingual audit", "PASS with 0 warnings")


def _core_review_check(total: int, reviewed: int, draft: int) -> VaultHealthCheck:
    if total == 0:
        return VaultHealthCheck(WARN, "core reviewed notes", "no generated core notes found")
    if draft:
        return VaultHealthCheck(WARN, "core reviewed notes", f"{reviewed}/{total} reviewed; {draft} draft")
    return VaultHealthCheck(OK, "core reviewed notes", f"{reviewed}/{total} reviewed or evergreen")


def _backlink_candidate_check(candidates: int, min_score: int) -> VaultHealthCheck:
    if candidates:
        return VaultHealthCheck(WARN, "backlink candidates", f"{candidates} candidate(s) at min score {min_score}")
    return VaultHealthCheck(OK, "backlink candidates", f"0 candidates at min score {min_score}")


def _backlink_queue_check(pending: int, checked_ready: int) -> VaultHealthCheck:
    if checked_ready:
        return VaultHealthCheck(WARN, "backlink checklist", f"{checked_ready} checked item(s) ready to apply; {pending} pending")
    if pending:
        return VaultHealthCheck(WARN, "backlink checklist", f"{pending} pending item(s)")
    return VaultHealthCheck(OK, "backlink checklist", "no pending or checked items")


def _run_cleanup_check(candidates: int) -> VaultHealthCheck:
    if candidates:
        return VaultHealthCheck(WARN, "run cleanup", f"{candidates} archive candidate(s)")
    return VaultHealthCheck(OK, "run cleanup", "0 archive candidates")


def _stale_note_check(stale_notes: int, stale_days: int) -> VaultHealthCheck:
    if stale_notes:
        return VaultHealthCheck(WARN, "stale generated notes", f"{stale_notes} older than {stale_days} day(s)")
    return VaultHealthCheck(OK, "stale generated notes", f"0 older than {stale_days} day(s)")


def _review_promotion_check(candidates: int) -> VaultHealthCheck:
    if candidates:
        return VaultHealthCheck(WARN, "review promotion", f"{candidates} draft note(s) ready for review")
    return VaultHealthCheck(OK, "review promotion", "0 draft promotion candidates")


def _verification_cleanup_check(candidates: int) -> VaultHealthCheck:
    if candidates:
        return VaultHealthCheck(WARN, "verification cleanup", f"{candidates} stale verification candidate(s)")
    return VaultHealthCheck(OK, "verification cleanup", "0 stale verification candidates")


def _check_lines(checks: list[VaultHealthCheck]) -> str:
    if not checks:
        return "- None."
    return "\n".join(f"- [{check.status}] {check.name}: {check.detail}" for check in checks)


def _counter_lines(counter: Counter[str]) -> str:
    if not counter:
        return "- None."
    return "\n".join(f"- {key}: {count}" for key, count in sorted(counter.items()))
