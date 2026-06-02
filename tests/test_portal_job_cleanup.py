from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.cli import main
from research_agent.config import AppSettings, ObsidianSettings, OpenAISettings, QualityGateSettings, Settings, SourceSettings
from research_agent.portal_api import (
    ResearchPortalAPIAdapter,
    cleanup_portal_job_store,
    render_portal_job_cleanup_result,
)


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
    )


class PortalJobCleanupTests(unittest.TestCase):
    def test_cleanup_previews_and_applies_age_based_terminal_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "jobs.json"
            _write_jobs(
                store,
                [
                    _job("old-completed", "completed", "2026-01-01T10:00:00+09:00"),
                    _job("recent-failed", "failed", "2026-05-30T10:00:00+09:00"),
                    _job("old-running", "running", "2026-01-01T10:00:00+09:00"),
                ],
            )

            preview = cleanup_portal_job_store(
                store,
                retention_days=30,
                dry_run=True,
                now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
            stored_after_preview = _stored_job_ids(store)

            applied = cleanup_portal_job_store(
                store,
                retention_days=30,
                dry_run=False,
                now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )
            stored_after_apply = _stored_job_ids(store)

        self.assertEqual([item.job_id for item in preview.removed_jobs], ["old-completed"])
        self.assertEqual(stored_after_preview, ["old-completed", "recent-failed", "old-running"])
        self.assertEqual([item.job_id for item in applied.removed_jobs], ["old-completed"])
        self.assertEqual(stored_after_apply, ["recent-failed", "old-running"])

    def test_cleanup_prunes_terminal_jobs_beyond_retention_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "jobs.json"
            _write_jobs(
                store,
                [
                    _job("oldest", "completed", "2026-05-01T10:00:00+09:00"),
                    _job("middle", "failed", "2026-05-15T10:00:00+09:00"),
                    _job("newest", "interrupted", "2026-05-30T10:00:00+09:00"),
                    _job("active", "queued", "2026-04-01T10:00:00+09:00"),
                ],
            )

            result = cleanup_portal_job_store(store, retention_limit=2, dry_run=False)
            stored_after_apply = _stored_job_ids(store)

        self.assertEqual([item.job_id for item in result.removed_jobs], ["oldest"])
        self.assertEqual(stored_after_apply, ["middle", "newest", "active"])

    def test_cleanup_result_rendering_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "jobs.json"
            _write_jobs(store, [_job("old", "completed", "2000-01-01T10:00:00+09:00")])
            result = cleanup_portal_job_store(
                store,
                retention_days=30,
                dry_run=True,
                now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

        rendered = render_portal_job_cleanup_result(result)
        self.assertIn("Portal Job Store Cleanup", rendered)
        self.assertIn("Mode: dry-run", rendered)
        self.assertIn("old [completed]", rendered)
        self.assertIn("Re-run with --apply", rendered)

    def test_adapter_applies_configured_retention_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            store = Path(temp) / "jobs.json"
            _write_jobs(
                store,
                [
                    _job("old", "completed", "2026-01-01T10:00:00+09:00"),
                    _job("new", "completed", "2026-05-30T10:00:00+09:00"),
                ],
            )

            adapter = ResearchPortalAPIAdapter(
                _settings(vault),
                job_store_path=store,
                job_retention_limit=1,
            )
            try:
                jobs = adapter.handle_request("/jobs").json()["jobs"]
            finally:
                adapter.close(wait=False)
            stored_after_startup = _stored_job_ids(store)

        self.assertEqual([job["job_id"] for job in jobs], ["new"])
        self.assertEqual(stored_after_startup, ["new"])

    def test_cli_portal_job_cleanup_defaults_to_preview_and_apply_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            vault.mkdir()
            store = root / "jobs.json"
            config = root / "config.toml"
            config.write_text(f'[obsidian]\nvault_path = "{vault.as_posix()}"\n', encoding="utf-8")
            _write_jobs(store, [_job("old", "completed", "2000-01-01T10:00:00+09:00")])

            preview_output = io.StringIO()
            with redirect_stdout(preview_output):
                preview_code = main(
                    [
                        "--config",
                        str(config),
                        "portal-job-cleanup",
                        "--job-store-path",
                        str(store),
                        "--retention-days",
                        "30",
                    ]
                )
            ids_after_preview = _stored_job_ids(store)

            apply_output = io.StringIO()
            with redirect_stdout(apply_output):
                apply_code = main(
                    [
                        "--config",
                        str(config),
                        "portal-job-cleanup",
                        "--job-store-path",
                        str(store),
                        "--retention-days",
                        "30",
                        "--apply",
                    ]
                )
            ids_after_apply = _stored_job_ids(store)

        self.assertEqual(preview_code, 0)
        self.assertEqual(apply_code, 0)
        self.assertIn("Mode: dry-run", preview_output.getvalue())
        self.assertEqual(ids_after_preview, ["old"])
        self.assertEqual(ids_after_apply, [])


def _write_jobs(path: Path, jobs: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"generated_at": "2026-06-01T10:00:00+09:00", "total_jobs": len(jobs), "jobs": jobs}, indent=2),
        encoding="utf-8",
    )


def _stored_job_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [str(job["job_id"]) for job in payload["jobs"]]


def _job(job_id: str, status: str, timestamp: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "topic_preview": f"topic {job_id}",
        "status": status,
        "created_at": timestamp,
        "started_at": timestamp,
        "finished_at": timestamp if status not in {"queued", "running"} else None,
        "mode": "dry_run",
        "provider": "gemini",
        "offline": True,
        "dry_run": True,
        "run_id": job_id if status == "completed" else None,
        "summary": {"type": "dry_run"} if status == "completed" else None,
    }


if __name__ == "__main__":
    unittest.main()
