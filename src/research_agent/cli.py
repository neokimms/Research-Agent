from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .bilingual_audit import render_bilingual_audit, run_bilingual_audit, write_bilingual_audit_note
from .bilingual_upgrade import render_bilingual_upgrade_result, upgrade_bilingual_notes
from .blueprint_refresh import (
    apply_blueprint_refresh,
    build_blueprint_refresh,
    render_blueprint_refresh,
    render_blueprint_refresh_apply_result,
    write_blueprint_refresh_note,
)
from .config import load_dotenv, load_settings
from .doctor import run_doctor
from .low_priority_backlink_review import (
    apply_low_priority_backlinks,
    build_low_priority_backlink_review,
    render_low_priority_backlink_apply_result,
    render_low_priority_backlink_review,
    write_low_priority_backlink_review_note,
)
from .manual_orphan_review import (
    apply_manual_orphan_review,
    build_manual_orphan_review,
    render_manual_orphan_apply_result,
    render_manual_orphan_review,
    write_manual_orphan_review_note,
)
from .next_actions import build_next_actions, render_next_actions, write_next_actions_note
from .official_docs_refresh import (
    apply_official_docs_refresh,
    build_official_docs_refresh,
    render_official_docs_refresh,
    render_official_docs_refresh_apply_result,
    write_official_docs_refresh_note,
)
from .paper_refresh import (
    apply_paper_refresh,
    build_paper_refresh,
    render_paper_refresh,
    render_paper_refresh_apply_result,
    write_paper_refresh_note,
)
from .paper_downstream import (
    apply_paper_downstream_proposals,
    build_paper_downstream_proposals,
    render_paper_downstream_apply_result,
    render_paper_downstream_proposals,
    write_paper_downstream_proposals,
)
from .paper_claim_refresh import (
    apply_paper_claim_refresh,
    build_paper_claim_refresh,
    render_paper_claim_refresh,
    render_paper_claim_refresh_apply_result,
    write_paper_claim_refresh_note,
)
from .pipeline import ResearchPipeline
from .portal_api import (
    PORTAL_JOB_STORE_FILE,
    cleanup_portal_job_store,
    render_portal_job_cleanup_result,
    serve_portal_api,
)
from .review_promotion import (
    apply_review_promotion,
    build_review_promotion,
    render_review_promotion,
    render_review_promotion_apply_result,
    write_review_promotion_note,
)
from .run_cleanup import (
    apply_run_cleanup,
    build_run_cleanup,
    render_run_cleanup,
    render_run_cleanup_apply_result,
    write_run_cleanup_note,
)
from .secrets import select_llm_provider
from .source_audit import render_source_audit, run_source_audit, write_source_audit_note
from .source_reference_sync import render_source_reference_sync_result, sync_source_references
from .standards_refresh import (
    apply_standards_refresh,
    build_standards_refresh,
    render_standards_refresh,
    render_standards_refresh_apply_result,
    write_standards_refresh_note,
)
from .timeutil import now_local
from .verification_cleanup import (
    apply_verification_cleanup,
    build_verification_cleanup,
    render_verification_cleanup,
    render_verification_cleanup_apply_result,
    write_verification_cleanup_note,
)
from .vault_index import (
    apply_reviewed_backlinks,
    build_backlink_history,
    build_backlink_proposal_suggestions,
    build_backlink_review_queue,
    build_vault_index,
    render_apply_reviewed_backlinks_result,
    render_backlink_history,
    render_backlink_history_write_result,
    render_backlink_proposals,
    render_backlink_review_queue,
    render_vault_index,
    write_backlink_history_state,
    write_backlink_proposals,
    write_vault_index,
)
from .vault_health import build_vault_health, render_vault_health, write_vault_health_note


DEFAULT_CONFIG = Path("config/research-agent.example.toml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-agent")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to TOML config.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Optional .env file.")
    parser.add_argument("--vault", type=Path, default=None, help="Override Obsidian vault path.")
    parser.add_argument("--provider", choices=["auto", "openai", "gemini"], default=None, help="Override LLM provider selection.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_vault = subparsers.add_parser("init-vault", help="Create the configured Obsidian folder structure.")
    init_vault.set_defaults(func=_init_vault)

    doctor = subparsers.add_parser("doctor", help="Check configuration, Common Module integration, vault safety, and API key state.")
    doctor.add_argument("--no-write-test", action="store_true", help="Skip temporary vault write and protection checks.")
    doctor.add_argument("--openai-smoke", action="store_true", help="Run a minimal paid OpenAI Responses API smoke test.")
    doctor.add_argument("--gemini-smoke", action="store_true", help="Run a minimal paid Gemini API smoke test.")
    doctor.add_argument("--provider", choices=["auto", "openai", "gemini"], default=None, dest="provider_after", help="Override LLM provider selection.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    doctor.set_defaults(func=_doctor)

    run = subparsers.add_parser("run", help="Run a research task and write Obsidian notes.")
    run.add_argument("topic", help="Research topic or question.")
    run.add_argument("--offline", action="store_true", help="Skip LLM provider calls and network paper collectors.")
    run.add_argument("--dry-run", action="store_true", help="Preview planned artifacts without writing files or calling APIs.")
    run.add_argument("--provider", choices=["auto", "openai", "gemini"], default=None, dest="provider_after", help="Override LLM provider selection.")
    run.add_argument("--max-papers-per-source", type=int, default=2, help="Paper records to request per paper source.")
    run.set_defaults(func=_run)

    index_vault = subparsers.add_parser("index-vault", help="Index the Obsidian vault and write backlink suggestions.")
    index_vault.add_argument("--stale-days", type=int, default=90, help="Mark generated notes stale after this many days.")
    index_vault.add_argument("--max-suggestions", type=int, default=20, help="Maximum backlink suggestions to include.")
    index_vault.add_argument("--dry-run", action="store_true", help="Print the index note without writing it.")
    index_vault.set_defaults(func=_index_vault)

    backlink_proposals = subparsers.add_parser("backlink-proposals", help="Create or apply Obsidian backlink proposal workflow.")
    backlink_proposals.add_argument("--stale-days", type=int, default=90, help="Use the same stale-note window as vault indexing.")
    backlink_proposals.add_argument("--max-suggestions", type=int, default=20, help="Maximum backlink candidates to review.")
    backlink_proposals.add_argument("--min-score", type=int, default=3, help="Minimum suggestion score to include.")
    backlink_proposals.add_argument("--dry-run", action="store_true", help="Print the proposal note without writing or appending.")
    backlink_proposals.add_argument("--apply", action="store_true", help="Append proposal checklists to source notes.")
    backlink_proposals.add_argument("--include-reviewed", action="store_true", help="Allow appending checklists to reviewed/evergreen notes.")
    backlink_proposals.add_argument("--supersede-previous", action="store_true", help="Mark existing proposed backlink proposal notes as superseded after writing the new note.")
    backlink_proposals.set_defaults(func=_backlink_proposals)

    review_backlinks = subparsers.add_parser("review-backlinks", help="Summarize appended backlink proposal checklist status.")
    review_backlinks.set_defaults(func=_review_backlinks)

    apply_reviewed = subparsers.add_parser("apply-reviewed-backlinks", help="Move checked backlink proposals into Related Notes.")
    apply_reviewed.add_argument("--dry-run", action="store_true", help="Preview checked backlink application without writing.")
    apply_reviewed.set_defaults(func=_apply_reviewed_backlinks)

    backlink_history = subparsers.add_parser("backlink-history", help="Summarize backlink proposal note history.")
    backlink_history.add_argument("--write-state", action="store_true", help="Write inferred proposal_state into proposal note frontmatter.")
    backlink_history.add_argument("--dry-run", action="store_true", help="Preview --write-state changes without writing.")
    backlink_history.set_defaults(func=_backlink_history)

    upgrade_bilingual = subparsers.add_parser("upgrade-bilingual", help="Preview or apply bilingual Korean translation appendix to existing generated reports.")
    upgrade_bilingual.add_argument("--apply", action="store_true", help="Write bilingual frontmatter and Korean translation draft appendices.")
    upgrade_bilingual.add_argument("--include-reviewed", action="store_true", help="Allow updating reviewed/evergreen generated notes.")
    upgrade_bilingual.add_argument("--max-notes", type=int, default=None, help="Limit how many candidate notes to preview or update.")
    upgrade_bilingual.add_argument("--refresh-translation", action="store_true", help="Regenerate existing Korean translation draft appendices using the current dictionary.")
    upgrade_bilingual.set_defaults(func=_upgrade_bilingual)

    bilingual_audit = subparsers.add_parser("bilingual-audit", help="Audit generated reports for bilingual metadata and Korean translation quality markers.")
    bilingual_audit.add_argument("--no-refresh-check", action="store_true", help="Skip checking whether translation appendices match the current dictionary.")
    bilingual_audit.add_argument("--max-issues", type=int, default=50, help="Maximum issues to print.")
    bilingual_audit.add_argument("--write-note", action="store_true", help="Write the audit result as an Obsidian note under the configured run directory.")
    bilingual_audit.set_defaults(func=_bilingual_audit)

    source_audit = subparsers.add_parser("source-audit", help="Audit generated source notes for URL, identity, and claim-link quality.")
    source_audit.add_argument("--max-issues", type=int, default=50, help="Maximum issues to print.")
    source_audit.add_argument("--write-note", action="store_true", help="Write the audit result as an Obsidian note under the configured run directory.")
    source_audit.set_defaults(func=_source_audit)

    official_docs_refresh = subparsers.add_parser("official-docs-refresh", help="Propose exact official documentation URLs for seed official-docs source notes.")
    official_docs_refresh.add_argument("--limit", type=int, default=6, help="Maximum exact official docs candidates to collect per topic.")
    official_docs_refresh.add_argument("--max-proposals", type=int, default=50, help="Maximum proposals to print or write.")
    official_docs_refresh.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    official_docs_refresh.set_defaults(func=_official_docs_refresh)

    apply_official_docs_refresh_parser = subparsers.add_parser("apply-official-docs-refresh", help="Apply checked official docs refresh proposals to source notes.")
    apply_official_docs_refresh_parser.add_argument("--dry-run", action="store_true", help="Preview checked proposal application without writing.")
    apply_official_docs_refresh_parser.set_defaults(func=_apply_official_docs_refresh)

    standards_refresh = subparsers.add_parser("standards-refresh", help="Propose exact standards/security framework URLs for seed standards source notes.")
    standards_refresh.add_argument("--limit", type=int, default=6, help="Maximum exact standards candidates to collect per topic.")
    standards_refresh.add_argument("--max-proposals", type=int, default=50, help="Maximum proposals to print or write.")
    standards_refresh.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    standards_refresh.set_defaults(func=_standards_refresh)

    apply_standards_refresh_parser = subparsers.add_parser("apply-standards-refresh", help="Apply checked standards refresh proposals to source notes.")
    apply_standards_refresh_parser.add_argument("--dry-run", action="store_true", help="Preview checked proposal application without writing.")
    apply_standards_refresh_parser.set_defaults(func=_apply_standards_refresh)

    sync_source_refs = subparsers.add_parser("sync-source-references", help="Sync evidence ledger and service blueprint references from source notes.")
    sync_source_refs.add_argument("--apply", action="store_true", help="Write synced evidence ledger and service blueprint references. Defaults to dry-run.")
    sync_source_refs.add_argument("--max-replacements", type=int, default=50, help="Maximum replacements to print.")
    sync_source_refs.set_defaults(func=_sync_source_references)

    paper_refresh = subparsers.add_parser("paper-refresh", help="Collect paper metadata candidates and write a human-reviewable proposal.")
    paper_refresh.add_argument("topic", nargs="?", default="", help="Optional topic. Defaults to generated topics found in the vault.")
    paper_refresh.add_argument("--limit-each", type=int, default=3, help="Maximum paper candidates to request from each configured paper source.")
    paper_refresh.add_argument("--max-proposals", type=int, default=20, help="Maximum proposals to print or write.")
    paper_refresh.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    paper_refresh.set_defaults(func=_paper_refresh)

    apply_paper_refresh_parser = subparsers.add_parser("apply-paper-refresh", help="Create checked paper source notes from paper refresh proposals.")
    apply_paper_refresh_parser.add_argument("--dry-run", action="store_true", help="Preview checked paper source note creation without writing.")
    apply_paper_refresh_parser.set_defaults(func=_apply_paper_refresh)

    paper_downstream = subparsers.add_parser("paper-downstream-proposals", help="Propose paper source updates for evidence ledger, service blueprint, and topic map notes.")
    paper_downstream.add_argument("--max-proposals", type=int, default=50, help="Maximum proposals to print or write.")
    paper_downstream.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    paper_downstream.set_defaults(func=_paper_downstream_proposals)

    apply_paper_downstream = subparsers.add_parser("apply-paper-downstream", help="Apply checked paper downstream proposals to generated notes.")
    apply_paper_downstream.add_argument("--dry-run", action="store_true", help="Preview checked downstream updates without writing.")
    apply_paper_downstream.set_defaults(func=_apply_paper_downstream)

    paper_claim_refresh = subparsers.add_parser("paper-claim-refresh", help="Propose better paper source summaries and claims for generic metadata-only paper notes.")
    paper_claim_refresh.add_argument("--max-proposals", type=int, default=50, help="Maximum proposals to print or write.")
    paper_claim_refresh.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    paper_claim_refresh.add_argument("--no-network", action="store_true", help="Use only existing local source-note metadata.")
    paper_claim_refresh.set_defaults(func=_paper_claim_refresh)

    apply_paper_claim_refresh_parser = subparsers.add_parser("apply-paper-claim-refresh", help="Apply checked paper claim refresh proposals to paper source notes.")
    apply_paper_claim_refresh_parser.add_argument("--dry-run", action="store_true", help="Preview checked paper claim updates without writing.")
    apply_paper_claim_refresh_parser.set_defaults(func=_apply_paper_claim_refresh)

    blueprint_refresh = subparsers.add_parser("blueprint-refresh", help="Propose service blueprint section updates from the current evidence ledger.")
    blueprint_refresh.add_argument("--max-proposals", type=int, default=50, help="Maximum proposals to print or write.")
    blueprint_refresh.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    blueprint_refresh.set_defaults(func=_blueprint_refresh)

    apply_blueprint_refresh_parser = subparsers.add_parser("apply-blueprint-refresh", help="Apply checked blueprint refresh proposals to service blueprint notes.")
    apply_blueprint_refresh_parser.add_argument("--dry-run", action="store_true", help="Preview checked blueprint section updates without writing.")
    apply_blueprint_refresh_parser.set_defaults(func=_apply_blueprint_refresh)

    review_promotion = subparsers.add_parser("review-promotion-proposals", help="Propose generated draft notes that are ready for reviewed status.")
    review_promotion.add_argument("--max-proposals", type=int, default=50, help="Maximum promotion candidates to print or write.")
    review_promotion.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    review_promotion.set_defaults(func=_review_promotion_proposals)

    apply_review_promotion_parser = subparsers.add_parser("apply-review-promotion", help="Apply checked review promotion proposals to note frontmatter.")
    apply_review_promotion_parser.add_argument("--dry-run", action="store_true", help="Preview checked review promotions without writing.")
    apply_review_promotion_parser.set_defaults(func=_apply_review_promotion)

    verification_cleanup = subparsers.add_parser("verification-cleanup", help="Propose cleanup for stale verification text in generated notes.")
    verification_cleanup.add_argument("--max-proposals", type=int, default=50, help="Maximum cleanup candidates to print or write.")
    verification_cleanup.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    verification_cleanup.set_defaults(func=_verification_cleanup)

    apply_verification_cleanup_parser = subparsers.add_parser("apply-verification-cleanup", help="Apply checked verification cleanup proposals to generated notes.")
    apply_verification_cleanup_parser.add_argument("--dry-run", action="store_true", help="Preview checked verification cleanup updates without writing.")
    apply_verification_cleanup_parser.set_defaults(func=_apply_verification_cleanup)

    run_cleanup = subparsers.add_parser("run-cleanup-proposals", help="Propose archival status updates for completed run history notes.")
    run_cleanup.add_argument("--max-proposals", type=int, default=100, help="Maximum cleanup candidates to print or write.")
    run_cleanup.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    run_cleanup.set_defaults(func=_run_cleanup_proposals)

    apply_run_cleanup_parser = subparsers.add_parser("apply-run-cleanup", help="Apply checked run cleanup proposals to run note frontmatter.")
    apply_run_cleanup_parser.add_argument("--dry-run", action="store_true", help="Preview checked run cleanup updates without writing.")
    apply_run_cleanup_parser.set_defaults(func=_apply_run_cleanup)

    portal_job_cleanup = subparsers.add_parser("portal-job-cleanup", help="Prune old terminal jobs from the Research Agent Portal JSON job store.")
    portal_job_cleanup.add_argument("--job-store-path", type=Path, default=None, help="Optional JSON job store path. Defaults to the configured vault run directory.")
    portal_job_cleanup.add_argument("--retention-days", type=int, default=90, help="Prune terminal jobs older than this many days. Use 0 to disable age pruning.")
    portal_job_cleanup.add_argument("--retention-limit", type=int, default=200, help="Keep the newest N terminal jobs. Use 0 to disable count pruning.")
    portal_job_cleanup.add_argument("--max-removed", type=int, default=50, help="Maximum pruned job ids to print.")
    portal_job_cleanup.add_argument("--apply", action="store_true", help="Write the pruned job store. Without this flag, only preview changes.")
    portal_job_cleanup.set_defaults(func=_portal_job_cleanup)

    manual_orphan_review = subparsers.add_parser("manual-orphan-proposals", help="Propose actions for disconnected manual notes with status metadata.")
    manual_orphan_review.add_argument("--max-proposals", type=int, default=50, help="Maximum manual orphan candidates to print or write.")
    manual_orphan_review.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    manual_orphan_review.set_defaults(func=_manual_orphan_proposals)

    apply_manual_orphan_review_parser = subparsers.add_parser("apply-manual-orphan-review", help="Apply checked manual orphan review actions.")
    apply_manual_orphan_review_parser.add_argument("--dry-run", action="store_true", help="Preview checked manual orphan actions without writing.")
    apply_manual_orphan_review_parser.set_defaults(func=_apply_manual_orphan_review)

    low_priority_backlinks = subparsers.add_parser("low-priority-backlink-proposals", help="Propose ignore decisions for low-priority backlink signals.")
    low_priority_backlinks.add_argument("--stale-days", type=int, default=90, help="Use the same stale-note window as vault indexing.")
    low_priority_backlinks.add_argument("--max-suggestions", type=int, default=20, help="Maximum backlink signals to inspect.")
    low_priority_backlinks.add_argument("--max-proposals", type=int, default=50, help="Maximum low-priority signals to print or write.")
    low_priority_backlinks.add_argument("--min-score", type=int, default=3, help="Minimum score treated as actionable; lower positive scores are review candidates.")
    low_priority_backlinks.add_argument("--write-note", action="store_true", help="Write proposals as an Obsidian note under the configured run directory.")
    low_priority_backlinks.set_defaults(func=_low_priority_backlink_proposals)

    apply_low_priority_backlinks_parser = subparsers.add_parser("apply-low-priority-backlinks", help="Apply checked low-priority backlink ignores.")
    apply_low_priority_backlinks_parser.add_argument("--dry-run", action="store_true", help="Preview checked low-priority backlink ignores without writing.")
    apply_low_priority_backlinks_parser.set_defaults(func=_apply_low_priority_backlinks)

    next_actions = subparsers.add_parser("next-actions", help="Summarize the next actionable vault maintenance steps.")
    next_actions.add_argument("--stale-days", type=int, default=90, help="Use this stale-note window when summarizing actions.")
    next_actions.add_argument("--max-suggestions", type=int, default=20, help="Maximum backlink suggestions to inspect.")
    next_actions.add_argument("--min-score", type=int, default=3, help="Minimum backlink score treated as actionable.")
    next_actions.add_argument("--max-items", type=int, default=20, help="Maximum example items per action source.")
    next_actions.add_argument("--write-note", action="store_true", help="Write the next-action summary as an Obsidian note under the configured run directory.")
    next_actions.set_defaults(func=_next_actions)

    vault_health = subparsers.add_parser("vault-health", help="Summarize audit, review, backlink, cleanup, and stale-note health for the vault.")
    vault_health.add_argument("--stale-days", type=int, default=90, help="Mark generated notes stale after this many days.")
    vault_health.add_argument("--max-suggestions", type=int, default=20, help="Maximum backlink suggestions to consider.")
    vault_health.add_argument("--min-score", type=int, default=3, help="Minimum backlink suggestion score to treat as actionable.")
    vault_health.add_argument("--write-note", action="store_true", help="Write the health summary as an Obsidian note under the configured run directory.")
    vault_health.set_defaults(func=_vault_health)

    portal_api = subparsers.add_parser("serve-portal-api", help="Serve a JSON API for web portals and AI Agent Architecture portal integration.")
    portal_api.add_argument("--host", default="127.0.0.1", help="Host for the portal API server.")
    portal_api.add_argument("--port", type=int, default=8780, help="Port for the portal API server.")
    portal_api.add_argument("--auth", choices=["none", "bearer"], default="none", help="Auth mode for the portal API server.")
    portal_api.add_argument("--token-env", default="RESEARCH_AGENT_PORTAL_TOKEN", help="Environment variable containing the bearer token.")
    portal_api.add_argument("--max-workers", type=int, default=1, help="Maximum background research jobs.")
    portal_api.add_argument("--max-active-jobs", type=int, default=20, help="Maximum queued plus running jobs.")
    portal_api.add_argument("--job-store-path", type=Path, default=None, help="Optional JSON job store path.")
    portal_api.add_argument("--job-retention-days", type=int, default=0, help="Automatically prune terminal jobs older than this many days. 0 disables age pruning.")
    portal_api.add_argument("--job-retention-limit", type=int, default=0, help="Automatically keep only the newest N terminal jobs. 0 disables count pruning.")
    portal_api.set_defaults(func=_serve_portal_api)

    args = parser.parse_args(argv)
    load_dotenv(args.env_file)

    try:
        provider_override = getattr(args, "provider_after", None) or args.provider
        settings = load_settings(args.config, vault_override=args.vault, provider_override=provider_override)
        load_dotenv(args.env_file, common_module_path=settings.common.module_path)
        return args.func(args, settings)
    except Exception as exc:
        print(f"research-agent: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _init_vault(args: argparse.Namespace, settings) -> int:
    pipeline = ResearchPipeline(settings)
    pipeline.writer.ensure_structure()
    print(f"Initialized vault structure at {pipeline.writer.vault_path}")
    return 0


def _doctor(args: argparse.Namespace, settings) -> int:
    report = run_doctor(
        settings,
        config_path=args.config,
        env_file=args.env_file,
        write_test=not args.no_write_test,
        openai_smoke=args.openai_smoke,
        gemini_smoke=args.gemini_smoke,
    )
    print(report.to_json() if args.json else report.to_text())
    return 1 if report.has_failures else 0


def _run(args: argparse.Namespace, settings) -> int:
    pipeline = ResearchPipeline(settings)
    if args.dry_run:
        plan = pipeline.dry_run(
            args.topic,
            offline=args.offline,
            max_papers_per_source=args.max_papers_per_source,
        )
        print("Dry Run Plan")
        print("")
        print(f"Topic: {plan.topic}")
        print(f"Vault: {plan.vault_path}")
        print(f"Mode: {plan.mode}")
        print("")
        print("Will create:")
        for artifact in plan.artifacts:
            suffix = f" ({artifact.status})" if artifact.status != "planned" else ""
            note = f" - {artifact.note}" if artifact.note else ""
            print(f"- [{artifact.kind}] {artifact.path}{suffix}{note}")
        print("")
        print("Safety:")
        for check in plan.safety:
            print(f"[{check.status}] {check.name}: {check.detail}")
        return 0

    artifacts = pipeline.run(
        args.topic,
        offline=args.offline,
        max_papers_per_source=args.max_papers_per_source,
    )
    print("Research run complete")
    print(f"Run note: {artifacts.run_note}")
    print(f"Evidence ledger: {artifacts.evidence_ledger}")
    print(f"Service blueprint: {artifacts.service_blueprint}")
    print(f"Topic map: {artifacts.topic_map}")
    print("Source notes:")
    for path in artifacts.source_notes:
        print(f"- {path}")
    return 0


def _index_vault(args: argparse.Namespace, settings) -> int:
    if args.dry_run:
        index = build_vault_index(
            settings.obsidian.vault_path,
            stale_days=args.stale_days,
            max_suggestions=args.max_suggestions,
        )
        print(render_vault_index(index, checked_at="dry-run", stale_days=args.stale_days))
        return 0

    path = write_vault_index(
        settings,
        stale_days=args.stale_days,
        max_suggestions=args.max_suggestions,
    )
    print(f"Vault index written: {path}")
    return 0


def _backlink_proposals(args: argparse.Namespace, settings) -> int:
    if args.dry_run:
        suggestions = build_backlink_proposal_suggestions(
            settings.obsidian.vault_path,
            stale_days=args.stale_days,
            max_suggestions=args.max_suggestions,
            min_score=args.min_score,
        )
        print(render_backlink_proposals(suggestions, checked_at="dry-run", min_score=args.min_score))
        return 0

    result = write_backlink_proposals(
        settings,
        stale_days=args.stale_days,
        max_suggestions=args.max_suggestions,
        min_score=args.min_score,
        apply=args.apply,
        include_reviewed=args.include_reviewed,
        supersede_previous=args.supersede_previous,
    )
    print(f"Backlink proposal note written: {result.proposal_path}")
    print(f"Candidate links: {len(result.suggestions)}")
    print(f"Superseded previous proposal notes: {len(result.superseded_paths)}")
    if args.apply:
        print(f"Applied checklist items: {len(result.applied_suggestions)}")
        print(f"Updated notes: {len(result.appended_paths)}")
        print(f"Skipped protected notes: {len(result.skipped_suggestions)}")
    else:
        print("No source notes were modified. Re-run with --apply to append review checklists.")
    return 0


def _review_backlinks(args: argparse.Namespace, settings) -> int:
    queue = build_backlink_review_queue(settings.obsidian.vault_path)
    print(render_backlink_review_queue(queue))
    return 0


def _apply_reviewed_backlinks(args: argparse.Namespace, settings) -> int:
    result = apply_reviewed_backlinks(settings.obsidian.vault_path, dry_run=args.dry_run)
    print(render_apply_reviewed_backlinks_result(result))
    return 0


def _backlink_history(args: argparse.Namespace, settings) -> int:
    if args.write_state:
        result = write_backlink_history_state(settings.obsidian.vault_path, dry_run=args.dry_run)
        print(render_backlink_history_write_result(result))
        return 0
    if args.dry_run:
        print("research-agent: --dry-run only applies with --write-state", file=sys.stderr)
        return 1
    history = build_backlink_history(settings.obsidian.vault_path)
    print(render_backlink_history(history))
    return 0


def _upgrade_bilingual(args: argparse.Namespace, settings) -> int:
    provider = select_llm_provider(settings)
    result = upgrade_bilingual_notes(
        settings.obsidian.vault_path,
        dry_run=not args.apply,
        include_reviewed=args.include_reviewed,
        max_notes=args.max_notes,
        translator_mode="dictionary",
        refresh_translation=args.refresh_translation,
    )
    print(render_bilingual_upgrade_result(result))
    if provider.available:
        print(
            f"API key state: {provider.provider} key detected. "
            "upgrade-bilingual currently uses the built-in dictionary for deterministic note upgrades."
        )
    else:
        print("API key state: no supported key detected. Using the built-in translation dictionary.")
    return 0


def _bilingual_audit(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_bilingual_audit_note(
            settings,
            refresh_check=not args.no_refresh_check,
            translator_mode="dictionary",
            max_issues=args.max_issues,
        )
        print(render_bilingual_audit(write_result.result, max_issues=args.max_issues))
        print(f"Audit note written: {write_result.note_path}")
        return 0 if write_result.result.passed else 1

    result = run_bilingual_audit(
        settings.obsidian.vault_path,
        refresh_check=not args.no_refresh_check,
        translator_mode="dictionary",
    )
    print(render_bilingual_audit(result, max_issues=args.max_issues))
    return 0 if result.passed else 1


def _source_audit(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_source_audit_note(settings, max_issues=args.max_issues)
        print(render_source_audit(write_result.result, max_issues=args.max_issues))
        print(f"Audit note written: {write_result.note_path}")
        return 0 if write_result.result.passed else 1

    result = run_source_audit(settings.obsidian.vault_path)
    print(render_source_audit(result, max_issues=args.max_issues))
    return 0 if result.passed else 1


def _official_docs_refresh(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_official_docs_refresh_note(
            settings,
            limit=args.limit,
            max_proposals=args.max_proposals,
        )
        print(render_official_docs_refresh(write_result.result, max_proposals=args.max_proposals))
        print(f"Official docs refresh note written: {write_result.note_path}")
        return 0

    result = build_official_docs_refresh(settings, limit=args.limit)
    print(render_official_docs_refresh(result, max_proposals=args.max_proposals))
    return 0


def _apply_official_docs_refresh(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_official_docs_refresh(
        settings.obsidian.vault_path,
        dry_run=args.dry_run,
        applied_at=applied_at,
    )
    print(render_official_docs_refresh_apply_result(result))
    return 0


def _standards_refresh(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_standards_refresh_note(
            settings,
            limit=args.limit,
            max_proposals=args.max_proposals,
        )
        print(render_standards_refresh(write_result.result, max_proposals=args.max_proposals))
        print(f"Standards refresh note written: {write_result.note_path}")
        return 0

    result = build_standards_refresh(settings, limit=args.limit)
    print(render_standards_refresh(result, max_proposals=args.max_proposals))
    return 0


def _apply_standards_refresh(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_standards_refresh(
        settings.obsidian.vault_path,
        dry_run=args.dry_run,
        applied_at=applied_at,
    )
    print(render_standards_refresh_apply_result(result))
    return 0


def _sync_source_references(args: argparse.Namespace, settings) -> int:
    result = sync_source_references(settings, dry_run=not args.apply)
    print(render_source_reference_sync_result(result, max_replacements=args.max_replacements))
    return 0


def _paper_refresh(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_paper_refresh_note(
            settings,
            topic=args.topic,
            limit_each=args.limit_each,
            max_proposals=args.max_proposals,
        )
        print(render_paper_refresh(write_result.result, max_proposals=args.max_proposals))
        print(f"Paper refresh note written: {write_result.note_path}")
        return 0

    result = build_paper_refresh(
        settings,
        topic=args.topic,
        limit_each=args.limit_each,
        max_proposals=args.max_proposals,
    )
    print(render_paper_refresh(result, max_proposals=args.max_proposals))
    return 0


def _apply_paper_refresh(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_paper_refresh(
        settings,
        dry_run=args.dry_run,
        applied_at=applied_at,
    )
    print(render_paper_refresh_apply_result(result))
    return 0


def _paper_downstream_proposals(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_paper_downstream_proposals(settings, max_proposals=args.max_proposals)
        print(render_paper_downstream_proposals(write_result.result, max_proposals=args.max_proposals))
        print(f"Paper downstream proposal note written: {write_result.note_path}")
        return 0

    result = build_paper_downstream_proposals(settings, max_proposals=args.max_proposals)
    print(render_paper_downstream_proposals(result, max_proposals=args.max_proposals))
    return 0


def _apply_paper_downstream(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_paper_downstream_proposals(
        settings,
        dry_run=args.dry_run,
        applied_at=applied_at,
    )
    print(render_paper_downstream_apply_result(result))
    return 0


def _paper_claim_refresh(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_paper_claim_refresh_note(
            settings,
            max_proposals=args.max_proposals,
            fetch_metadata=not args.no_network,
        )
        print(render_paper_claim_refresh(write_result.result, max_proposals=args.max_proposals))
        print(f"Paper claim refresh note written: {write_result.note_path}")
        return 0

    result = build_paper_claim_refresh(
        settings,
        max_proposals=args.max_proposals,
        fetch_metadata=not args.no_network,
    )
    print(render_paper_claim_refresh(result, max_proposals=args.max_proposals))
    return 0


def _apply_paper_claim_refresh(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_paper_claim_refresh(settings, dry_run=args.dry_run, applied_at=applied_at)
    print(render_paper_claim_refresh_apply_result(result))
    return 0


def _blueprint_refresh(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_blueprint_refresh_note(settings, max_proposals=args.max_proposals)
        print(render_blueprint_refresh(write_result.result, max_proposals=args.max_proposals))
        print(f"Blueprint refresh note written: {write_result.note_path}")
        return 0

    result = build_blueprint_refresh(settings, max_proposals=args.max_proposals)
    print(render_blueprint_refresh(result, max_proposals=args.max_proposals))
    return 0


def _apply_blueprint_refresh(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_blueprint_refresh(settings, dry_run=args.dry_run, applied_at=applied_at)
    print(render_blueprint_refresh_apply_result(result))
    return 0


def _review_promotion_proposals(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_review_promotion_note(settings, max_proposals=args.max_proposals)
        print(render_review_promotion(write_result.result, max_proposals=args.max_proposals))
        print(f"Review promotion note written: {write_result.note_path}")
        return 0

    result = build_review_promotion(settings, max_proposals=args.max_proposals)
    print(render_review_promotion(result, max_proposals=args.max_proposals))
    return 0


def _apply_review_promotion(args: argparse.Namespace, settings) -> int:
    reviewed_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_review_promotion(settings, dry_run=args.dry_run, reviewed_at=reviewed_at)
    print(render_review_promotion_apply_result(result))
    return 0


def _verification_cleanup(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_verification_cleanup_note(settings, max_proposals=args.max_proposals)
        print(render_verification_cleanup(write_result.result, max_proposals=args.max_proposals))
        print(f"Verification cleanup note written: {write_result.note_path}")
        return 0

    result = build_verification_cleanup(settings, max_proposals=args.max_proposals)
    print(render_verification_cleanup(result, max_proposals=args.max_proposals))
    return 0


def _apply_verification_cleanup(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_verification_cleanup(settings, dry_run=args.dry_run, applied_at=applied_at)
    print(render_verification_cleanup_apply_result(result))
    return 0


def _run_cleanup_proposals(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_run_cleanup_note(settings, max_proposals=args.max_proposals)
        print(render_run_cleanup(write_result.result, max_proposals=args.max_proposals))
        print(f"Run cleanup note written: {write_result.note_path}")
        return 0

    result = build_run_cleanup(settings, max_proposals=args.max_proposals)
    print(render_run_cleanup(result, max_proposals=args.max_proposals))
    return 0


def _apply_run_cleanup(args: argparse.Namespace, settings) -> int:
    archived_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_run_cleanup(settings, dry_run=args.dry_run, archived_at=archived_at)
    print(render_run_cleanup_apply_result(result))
    return 0


def _portal_job_cleanup(args: argparse.Namespace, settings) -> int:
    if args.max_removed <= 0:
        raise ValueError("--max-removed must be greater than 0")
    job_store_path = args.job_store_path or (
        settings.obsidian.vault_path.expanduser().resolve() / settings.obsidian.run_dir / PORTAL_JOB_STORE_FILE
    )
    result = cleanup_portal_job_store(
        job_store_path,
        retention_days=args.retention_days,
        retention_limit=args.retention_limit,
        dry_run=not args.apply,
    )
    print(render_portal_job_cleanup_result(result, max_removed=args.max_removed))
    return 0


def _manual_orphan_proposals(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_manual_orphan_review_note(settings, max_proposals=args.max_proposals)
        print(render_manual_orphan_review(write_result.result, max_proposals=args.max_proposals))
        print(f"Manual orphan review note written: {write_result.note_path}")
        return 0

    result = build_manual_orphan_review(settings, max_proposals=args.max_proposals)
    print(render_manual_orphan_review(result, max_proposals=args.max_proposals))
    return 0


def _apply_manual_orphan_review(args: argparse.Namespace, settings) -> int:
    reviewed_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_manual_orphan_review(settings, dry_run=args.dry_run, reviewed_at=reviewed_at)
    print(render_manual_orphan_apply_result(result))
    return 0


def _low_priority_backlink_proposals(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_low_priority_backlink_review_note(
            settings,
            stale_days=args.stale_days,
            max_suggestions=args.max_suggestions,
            max_proposals=args.max_proposals,
            min_score=args.min_score,
        )
        print(render_low_priority_backlink_review(write_result.result, max_proposals=args.max_proposals))
        print(f"Low-priority backlink review note written: {write_result.note_path}")
        return 0

    result = build_low_priority_backlink_review(
        settings,
        stale_days=args.stale_days,
        max_suggestions=args.max_suggestions,
        max_proposals=args.max_proposals,
        min_score=args.min_score,
    )
    print(render_low_priority_backlink_review(result, max_proposals=args.max_proposals))
    return 0


def _apply_low_priority_backlinks(args: argparse.Namespace, settings) -> int:
    applied_at = now_local(settings.app.timezone).isoformat(timespec="seconds")
    result = apply_low_priority_backlinks(settings, dry_run=args.dry_run, applied_at=applied_at)
    print(render_low_priority_backlink_apply_result(result))
    return 0


def _next_actions(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_next_actions_note(
            settings,
            stale_days=args.stale_days,
            max_suggestions=args.max_suggestions,
            min_score=args.min_score,
            max_items=args.max_items,
        )
        print(render_next_actions(write_result.report))
        print(f"Next actions note written: {write_result.note_path}")
        return 0

    report = build_next_actions(
        settings,
        stale_days=args.stale_days,
        max_suggestions=args.max_suggestions,
        min_score=args.min_score,
        max_items=args.max_items,
    )
    print(render_next_actions(report))
    return 0


def _vault_health(args: argparse.Namespace, settings) -> int:
    if args.write_note:
        write_result = write_vault_health_note(
            settings,
            stale_days=args.stale_days,
            max_suggestions=args.max_suggestions,
            min_score=args.min_score,
        )
        print(render_vault_health(write_result.report))
        print(f"Vault health note written: {write_result.note_path}")
        return 1 if write_result.report.has_failures else 0

    report = build_vault_health(
        settings,
        stale_days=args.stale_days,
        max_suggestions=args.max_suggestions,
        min_score=args.min_score,
    )
    print(render_vault_health(report))
    return 1 if report.has_failures else 0


def _serve_portal_api(args: argparse.Namespace, settings) -> int:
    token = os.environ.get(args.token_env, "") if args.token_env else ""
    if args.auth == "bearer" and not token:
        print(f"research-agent: bearer auth requires token in {args.token_env}", file=sys.stderr)
        return 1
    print(f"Research Agent Portal API: http://{args.host}:{args.port}")
    print("Endpoints: GET /health, POST /runs, GET /jobs, GET /runs, GET /vault-health, GET /next-actions")
    serve_portal_api(
        settings,
        config_path=args.config,
        env_file=args.env_file,
        host=args.host,
        port=args.port,
        auth_mode=args.auth,
        bearer_token=token or None,
        max_workers=args.max_workers,
        max_active_jobs=args.max_active_jobs,
        job_store_path=args.job_store_path,
        job_retention_days=args.job_retention_days,
        job_retention_limit=args.job_retention_limit,
    )
    return 0
