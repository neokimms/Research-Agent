#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research_agent.config import (  # noqa: E402
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.portal_api import ResearchPortalAPIAdapter  # noqa: E402
from research_agent.vault_index import (  # noqa: E402
    apply_reviewed_backlinks,
    build_backlink_proposal_suggestions,
    build_backlink_review_queue,
    write_backlink_proposals,
)


class SmokeError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.vault:
        vault = args.vault.expanduser().resolve()
        vault.mkdir(parents=True, exist_ok=True)
        return _run_smoke(vault, keep_vault=True, args=args)

    if args.keep_vault:
        vault = Path(tempfile.mkdtemp(prefix="research-agent-rerun-lineage-")) / "vault"
        vault.mkdir(parents=True, exist_ok=True)
        return _run_smoke(vault.resolve(), keep_vault=True, args=args)

    with tempfile.TemporaryDirectory(prefix="research-agent-rerun-lineage-") as temp:
        vault = Path(temp) / "vault"
        vault.mkdir(parents=True, exist_ok=True)
        return _run_smoke(vault.resolve(), keep_vault=False, args=args)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe live-offline rerun lineage smoke test against a temporary Obsidian vault.",
    )
    parser.add_argument("--vault", type=Path, help="Optional vault path. Defaults to an isolated temporary vault.")
    parser.add_argument("--keep-vault", action="store_true", help="Print the temporary vault path for inspection.")
    parser.add_argument("--topic", default="agentic RAG rerun lineage smoke")
    parser.add_argument("--rerun-of", default="failed-source-smoke")
    parser.add_argument("--job-id", default="rerun-lineage-smoke")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return args


def _run_smoke(vault: Path, *, keep_vault: bool, args: argparse.Namespace) -> int:
    settings = _settings(vault)
    (vault / "60_Runs").mkdir(parents=True, exist_ok=True)
    adapter = ResearchPortalAPIAdapter(
        settings,
        job_store_path=vault / "60_Runs" / "research_portal_jobs.json",
        job_id_factory=lambda: args.job_id,
    )
    try:
        queued = adapter.handle_request(
            "/runs",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {
                    "objective": args.topic,
                    "provider": "auto",
                    "offline": True,
                    "dry_run": False,
                    "max_papers_per_source": 1,
                    "rerun_of": args.rerun_of,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        ).json()
        if queued.get("status") != "queued" or queued.get("rerun_of") != args.rerun_of:
            raise SmokeError(f"Unexpected queued job payload: {queued}")

        job = adapter.wait_for_job(args.job_id, timeout_seconds=args.timeout)
        _assert_completed_run(job, args)

        actions = adapter.handle_request("/next-actions").json()
        _assert_next_actions_include_backlink(actions)
    finally:
        adapter.close(wait=True)

    summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
    paths = summary.get("paths") if isinstance(summary.get("paths"), dict) else {}
    run_note = _existing_path(paths.get("run_note"))
    topic_map = _existing_path(paths.get("topic_map"))
    _assert_lineage_markdown(run_note, args.rerun_of)
    _assert_lineage_markdown(topic_map, args.rerun_of)

    suggestions = build_backlink_proposal_suggestions(vault, max_suggestions=10, min_score=6)
    lineage = [item for item in suggestions if item.reason.startswith(f"same rerun lineage `{args.rerun_of}`")]
    if len(lineage) != 1:
        raise SmokeError(f"Expected one rerun lineage backlink suggestion, got {len(lineage)}: {suggestions}")

    proposal = write_backlink_proposals(settings, max_suggestions=10, min_score=6, apply=True)
    if len(proposal.applied_suggestions) != 1 or not proposal.appended_paths:
        raise SmokeError(f"Expected one applied backlink checklist, got: {proposal}")

    source = proposal.applied_suggestions[0].source.path
    _approve_first_backlink_checklist(source)
    queue = build_backlink_review_queue(vault)
    if len(queue.completed) != 1:
        raise SmokeError(f"Expected one checked backlink item before apply, got: {queue}")

    applied = apply_reviewed_backlinks(vault, dry_run=False)
    if len(applied.applied_items) != 1 or not applied.updated_paths:
        raise SmokeError(f"Expected one applied reviewed backlink, got: {applied}")

    remaining = build_backlink_proposal_suggestions(vault, max_suggestions=10, min_score=6)
    remaining_lineage = [item for item in remaining if item.reason.startswith(f"same rerun lineage `{args.rerun_of}`")]
    if remaining_lineage:
        raise SmokeError(f"Lineage backlink still suggested after apply: {remaining_lineage}")

    print("OK rerun lineage smoke completed")
    print(f"vault: {vault if keep_vault else 'temporary vault cleaned'}")
    print(f"job_id: {args.job_id}")
    print(f"rerun_of: {args.rerun_of}")
    print(f"run_note: {run_note.relative_to(vault).as_posix()}")
    print(f"topic_map: {topic_map.relative_to(vault).as_posix()}")
    print(f"proposal_note: {proposal.proposal_path.relative_to(vault).as_posix() if proposal.proposal_path else '-'}")
    print("lineage_backlink: applied")
    return 0


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
        sources=SourceSettings(
            official_doc_domains=["developers.openai.com"],
            standards_domains=[],
            paper_sources=[],
        ),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _assert_completed_run(job: dict[str, Any], args: argparse.Namespace) -> None:
    if job.get("status") != "completed":
        raise SmokeError(f"Job did not complete: {job}")
    if job.get("mode") != "run":
        raise SmokeError(f"Expected live run mode, got mode={job.get('mode')!r}")
    if job.get("dry_run") is not False or job.get("offline") is not True:
        raise SmokeError(f"Expected live offline flags, got: {job}")
    if job.get("rerun_of") != args.rerun_of:
        raise SmokeError(f"rerun_of was not preserved: {job}")
    summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
    if summary.get("type") != "run" or summary.get("rerun_of") != args.rerun_of:
        raise SmokeError(f"Unexpected run summary: {summary}")


def _assert_next_actions_include_backlink(actions: dict[str, Any]) -> None:
    report = actions.get("report") if isinstance(actions.get("report"), dict) else {}
    items = report.get("items") if isinstance(report.get("items"), list) else []
    if not any(item.get("title") == "Create actionable backlink proposals" for item in items if isinstance(item, dict)):
        raise SmokeError(f"next-actions did not report the rerun lineage backlink proposal: {actions}")


def _existing_path(value: Any) -> Path:
    path = Path(str(value or "")).resolve()
    if not path.exists():
        raise SmokeError(f"Expected artifact path to exist: {value}")
    return path


def _assert_lineage_markdown(path: Path, rerun_of: str) -> None:
    text = path.read_text(encoding="utf-8")
    required = [
        f'rerun_of: "{rerun_of}"',
        "## Run Lineage",
        f"- Re-run of portal job `{rerun_of}`.",
        f"- 포털 작업 `{rerun_of}`의 재실행입니다.",
    ]
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise SmokeError(f"{path} is missing lineage fragments: {missing}")


def _approve_first_backlink_checklist(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text.replace("- [ ] Add [[", "- [x] Add [[", 1)
    if updated == text:
        raise SmokeError(f"No pending backlink checklist found in {path}")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
