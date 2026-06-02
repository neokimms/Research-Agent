from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import AppSettings, ObsidianSettings, OpenAISettings, QualityGateSettings, Settings, SourceSettings
from research_agent.portal_api import ResearchPortalAPIAdapter


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(api_key_env="RESEARCH_AGENT_MISSING_KEY"),
        sources=SourceSettings(
            official_doc_domains=["developers.openai.com"],
            standards_domains=["nist.gov"],
            paper_sources=[],
        ),
        quality_gates=QualityGateSettings(),
    )


class PortalAPITests(unittest.TestCase):
    def test_static_portal_assets_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            adapter = ResearchPortalAPIAdapter(
                _settings(Path(temp)),
                job_store_path=Path(temp) / "jobs.json",
            )
            try:
                html = adapter.handle_request("/")
                css = adapter.handle_request("/assets/portal.css")
                js = adapter.handle_request("/assets/portal.js")
            finally:
                adapter.close(wait=False)

        self.assertEqual(html.status, 200)
        self.assertIn("text/html", html.content_type)
        self.assertIn("리서치 에이전트 포털".encode("utf-8"), html.body)
        self.assertIn("작업 저장소".encode("utf-8"), html.body)
        self.assertIn(b'id="runForm"', html.body)
        self.assertIn(b'id="providerInput"', html.body)
        self.assertIn(b'id="jobStoreStatus"', html.body)
        self.assertIn(b'id="actionList"', html.body)
        self.assertEqual(css.status, 200)
        self.assertIn("text/css", css.content_type)
        self.assertIn(b".status-grid", css.body)
        self.assertIn(b".action-list", css.body)
        self.assertEqual(js.status, 200)
        self.assertIn("application/javascript", js.content_type)
        self.assertIn(b"submitRun", js.body)
        self.assertIn(b"renderNextActions", js.body)
        self.assertIn(b"refreshJobStoreHealth", js.body)

    def test_health_is_public_and_reports_service_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            adapter = ResearchPortalAPIAdapter(
                _settings(Path(temp)),
                auth_mode="bearer",
                bearer_token="secret",
                job_store_path=Path(temp) / "jobs.json",
            )
            try:
                response = adapter.handle_request("/health")
                payload = response.json()
            finally:
                adapter.close(wait=False)

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "research-agent")
        self.assertEqual(payload["auth_mode"], "bearer")

    def test_bearer_auth_protects_non_health_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            adapter = ResearchPortalAPIAdapter(
                _settings(Path(temp)),
                auth_mode="bearer",
                bearer_token="secret",
                job_store_path=Path(temp) / "jobs.json",
            )
            try:
                rejected = adapter.handle_request("/jobs")
                accepted = adapter.handle_request("/jobs", headers={"Authorization": "Bearer secret"})
            finally:
                adapter.close(wait=False)

        self.assertEqual(rejected.status, 401)
        self.assertEqual(accepted.status, 200)

    def test_post_runs_accepts_ai_agent_architecture_objective_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            adapter = ResearchPortalAPIAdapter(
                _settings(vault),
                job_store_path=Path(temp) / "jobs.json",
                job_id_factory=lambda: "job-001",
            )
            try:
                response = adapter.handle_request(
                    "/runs",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                    body=json.dumps(
                        {
                            "objective": "agentic RAG 구조 분류",
                            "dry_run": True,
                            "offline": True,
                            "provider": "gemini",
                            "max_papers_per_source": 3,
                            "rerun_of": "failed-source",
                        }
                    ).encode("utf-8"),
                )
                queued = response.json()
                completed = adapter.wait_for_job("job-001", timeout_seconds=5.0)
                job_detail = adapter.handle_request("/jobs/job-001").json()
                run = adapter.handle_request("/runs/job-001").json()
            finally:
                adapter.close(wait=True)

        self.assertEqual(response.status, 202)
        self.assertEqual(queued["status_url"], "/jobs/job-001")
        self.assertEqual(queued["max_papers_per_source"], 3)
        self.assertEqual(queued["rerun_of"], "failed-source")
        self.assertNotIn("objective", queued)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["mode"], "dry_run")
        self.assertEqual(completed["provider"], "gemini")
        self.assertEqual(completed["max_papers_per_source"], 3)
        self.assertEqual(completed["rerun_of"], "failed-source")
        self.assertEqual(job_detail["objective"], "agentic RAG 구조 분류")
        self.assertEqual(job_detail["topic"], "agentic RAG 구조 분류")
        self.assertEqual(job_detail["rerun_of"], "failed-source")
        self.assertEqual(completed["summary"]["type"], "dry_run")
        self.assertEqual(completed["summary"]["rerun_of"], "failed-source")
        self.assertEqual(run["run_id"], "job-001")
        self.assertEqual(run["objective"], "agentic RAG 구조 분류")
        self.assertEqual(run["rerun_of"], "failed-source")
        self.assertTrue(run["paths"]["planned_artifacts"])

    def test_vault_health_and_next_actions_are_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            adapter = ResearchPortalAPIAdapter(_settings(vault), job_store_path=Path(temp) / "jobs.json")
            try:
                health = adapter.handle_request("/vault-health").json()
                actions = adapter.handle_request("/next-actions").json()
            finally:
                adapter.close(wait=False)

        self.assertIn(health["status"], {"OK", "WARN", "FAIL"})
        self.assertIn("report", health)
        self.assertIn("health_status", actions)
        self.assertIn("report", actions)

    def test_job_store_health_reports_counts_and_cleanup_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / "vault"
            vault.mkdir()
            store = Path(temp) / "jobs.json"
            _write_job_store(
                store,
                [
                    _job("old", "completed", "2026-05-01T10:00:00+09:00"),
                    _job("new", "failed", "2026-05-30T10:00:00+09:00"),
                ],
            )
            adapter = ResearchPortalAPIAdapter(_settings(vault), job_store_path=store)
            try:
                response = adapter.handle_request("/job-store-health?retention_days=0&retention_limit=1&max_removed=1")
                payload = response.json()
            finally:
                adapter.close(wait=False)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["total_jobs"], 2)
        self.assertEqual(payload["active_jobs"], 0)
        self.assertEqual(payload["terminal_jobs"], 2)
        self.assertEqual(payload["status_counts"], {"completed": 1, "failed": 1})
        self.assertFalse(payload["retention"]["auto_enabled"])
        self.assertEqual(payload["retention"]["preview_limit"], 1)
        self.assertEqual(payload["cleanup_preview"]["prune_candidates"], 1)
        self.assertEqual(payload["cleanup_preview"]["removed_jobs"][0]["job_id"], "old")


def _write_job_store(path: Path, jobs: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"generated_at": "2026-06-01T10:00:00+09:00", "total_jobs": len(jobs), "jobs": jobs}, indent=2),
        encoding="utf-8",
    )


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
