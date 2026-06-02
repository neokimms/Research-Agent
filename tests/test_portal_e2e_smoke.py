from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "portal_e2e_smoke.py"
SPEC = importlib.util.spec_from_file_location("portal_e2e_smoke", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load smoke script: {SCRIPT_PATH}")
portal_e2e_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal_e2e_smoke)


class PortalE2ESmokeScriptTests(unittest.TestCase):
    def test_parse_args_defaults_to_safe_offline_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = portal_e2e_smoke._parse_args(
                [
                    "--research-root",
                    str(root),
                    "--ai-portal-root",
                    str(root),
                    "--vault",
                    str(root),
                ]
            )

        self.assertEqual(args.provider, "gemini")
        self.assertTrue(args.offline)
        self.assertEqual(args.max_papers_per_source, 1)
        self.assertEqual(args.auth, "none")
        self.assertFalse(args.auto_port)
        self.assertEqual(args.research_port, 8780)
        self.assertEqual(args.pm_portal_port, 8770)

    def test_parse_args_online_keeps_dry_run_but_allows_collectors(self) -> None:
        args = portal_e2e_smoke._parse_args(["--online", "--provider", "openai", "--max-papers-per-source", "3"])

        self.assertFalse(args.offline)
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.max_papers_per_source, 3)

    def test_parse_args_rejects_non_positive_paper_count(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                portal_e2e_smoke._parse_args(["--max-papers-per-source", "0"])

    def test_bearer_auth_token_uses_env_or_generates_ephemeral_value(self) -> None:
        env_name = "RESEARCH_AGENT_TEST_SMOKE_TOKEN"
        previous = os.environ.get(env_name)
        try:
            os.environ[env_name] = "from-env"
            args = portal_e2e_smoke._parse_args(["--auth", "bearer", "--auth-token-env", env_name])
            self.assertEqual(portal_e2e_smoke._resolve_auth_token(args), "from-env")

            del os.environ[env_name]
            generated = portal_e2e_smoke._resolve_auth_token(args)
            self.assertIsInstance(generated, str)
            self.assertGreater(len(generated), 16)

            no_auth = portal_e2e_smoke._parse_args(["--auth", "none", "--auth-token-env", env_name])
            self.assertIsNone(portal_e2e_smoke._resolve_auth_token(no_auth))
        finally:
            if previous is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous

    def test_project_root_validation_checks_expected_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src" / "research_agent").mkdir(parents=True)
            portal_e2e_smoke._assert_project_root(root, "research_agent")

            with self.assertRaises(portal_e2e_smoke.SmokeError):
                portal_e2e_smoke._assert_project_root(root, "supervisor_graph_hybrid")

    def test_auto_port_replaces_busy_or_duplicate_ports(self) -> None:
        busy_port = 8780
        args = portal_e2e_smoke._parse_args(
            [
                "--auto-port",
                "--research-port",
                str(busy_port),
                "--pm-portal-port",
                str(busy_port),
            ]
        )

        with (
            patch.object(portal_e2e_smoke, "_is_port_free", return_value=False),
            patch.object(portal_e2e_smoke, "_pick_free_port", side_effect=[51001, 51002]),
        ):
            portal_e2e_smoke._assign_auto_ports(args)

        self.assertNotEqual(args.research_port, busy_port)
        self.assertNotEqual(args.pm_portal_port, busy_port)
        self.assertNotEqual(args.research_port, args.pm_portal_port)
        self.assertEqual(args.research_port, 51001)
        self.assertEqual(args.pm_portal_port, 51002)

    def test_assert_port_free_reports_busy_port(self) -> None:
        with patch.object(portal_e2e_smoke, "_is_port_free", return_value=False):
            with self.assertRaises(portal_e2e_smoke.SmokeError):
                portal_e2e_smoke._assert_port_free("127.0.0.1", 8780)

    def test_runtime_options_are_checked_from_pm_portal_response(self) -> None:
        expected = {"provider": "gemini", "offline": True, "dry_run": True, "max_papers_per_source": 1}
        created = {"portal": {"runtime_options": dict(expected)}}

        portal_e2e_smoke._assert_runtime_options(created, expected)

        created["portal"]["runtime_options"]["provider"] = "auto"
        with self.assertRaises(portal_e2e_smoke.SmokeError):
            portal_e2e_smoke._assert_runtime_options(created, expected)

    def test_job_store_health_contract_is_checked(self) -> None:
        portal_e2e_smoke._assert_job_store_health(
            {
                "total_jobs": 1,
                "cleanup_preview": {
                    "prune_candidates": 0,
                },
            }
        )

        with self.assertRaises(portal_e2e_smoke.SmokeError):
            portal_e2e_smoke._assert_job_store_health({"total_jobs": 1})

        with self.assertRaises(portal_e2e_smoke.SmokeError):
            portal_e2e_smoke._assert_job_store_health({"total_jobs": "1", "cleanup_preview": {"prune_candidates": 0}})

    def test_pm_research_presets_are_verified_from_static_assets(self) -> None:
        html = """
        <form id="scenario-form">
          <select id="runtime-provider">
            <option value="gemini" selected>gemini</option>
          </select>
          <input id="runtime-max-papers" type="number" value="1">
          <input id="runtime-offline" type="checkbox" checked>
          <input id="runtime-dry-run" type="checkbox" checked>
          <button data-research-preset="dry_run">리서치 드라이런</button>
          <button data-research-preset="live">리서치 실사용</button>
          <a href="#research">리서치</a>
          <section id="research"><h2>리서치 실행</h2></section>
          <strong id="research-job-store-status"></strong>
          <select id="research-job-status-filter"></select>
          <div id="research-job-list"></div>
        </form>
        """
        js = """
        function applyResearchPreset(name) {
          const presets = {
            dry_run: { provider: "gemini", offline: true, dry_run: true, max_papers_per_source: 1 },
            live: { provider: "auto", offline: false, dry_run: false, max_papers_per_source: 2 }
          };
          document.querySelectorAll("[data-research-preset]");
        }
        function renderResearchJobs() {}
        function renderResearchJobStore() {}
        async function rerunResearchJob(jobId) { return { rerun_of: jobId }; }
        """
        seen: list[tuple[str, str | None]] = []

        def fake_request_text(url: str, *, bearer_token: str | None = None) -> str:
            seen.append((url, bearer_token))
            return js if url.endswith("/assets/portal.js") else html

        with patch.object(portal_e2e_smoke, "_request_text", side_effect=fake_request_text):
            portal_e2e_smoke._verify_pm_research_presets("http://pm.local/", bearer_token="token")

        self.assertEqual(
            seen,
            [
                ("http://pm.local/", "token"),
                ("http://pm.local/assets/portal.js", "token"),
            ],
        )

    def test_research_portal_static_is_verified_from_assets(self) -> None:
        html = """
        <h1>리서치 에이전트 포털</h1>
        <form id="runForm">
          <select id="providerInput"></select>
          <strong id="jobStoreStatus"></strong>
          <section><h2>후속 작업</h2><div id="actionList"></div></section>
        </form>
        """
        js = """
        async function submitRun() {}
        async function refreshJobStoreHealth() {}
        function renderNextActions() {}
        """
        seen: list[tuple[str, str | None]] = []

        def fake_request_text(url: str, *, bearer_token: str | None = None) -> str:
            seen.append((url, bearer_token))
            return js if url.endswith("/assets/portal.js") else html

        with patch.object(portal_e2e_smoke, "_request_text", side_effect=fake_request_text):
            portal_e2e_smoke._verify_research_portal_static("http://research.local/", bearer_token="token")

        self.assertEqual(
            seen,
            [
                ("http://research.local/", "token"),
                ("http://research.local/assets/portal.js", "token"),
            ],
        )

    def test_pm_research_preset_verification_reports_missing_fragments(self) -> None:
        with patch.object(portal_e2e_smoke, "_request_text", return_value=""):
            with self.assertRaises(portal_e2e_smoke.SmokeError) as context:
                portal_e2e_smoke._verify_pm_research_presets("http://pm.local")

        self.assertIn("Research preset fragments", str(context.exception))

    def test_planned_artifacts_supports_top_level_and_summary_paths(self) -> None:
        top_level = {"paths": {"planned_artifacts": ["/tmp/a.md", Path("/tmp/b.md")]}}
        fallback = {"summary": {"paths": {"planned_artifacts": ["/tmp/c.md"]}}}
        missing = {"summary": {"paths": {}}}

        self.assertEqual(portal_e2e_smoke._planned_artifacts(top_level), ["/tmp/a.md", "/tmp/b.md"])
        self.assertEqual(portal_e2e_smoke._planned_artifacts(fallback), ["/tmp/c.md"])
        self.assertEqual(portal_e2e_smoke._planned_artifacts(missing), [])

    def test_bearer_auth_commands_configure_server_token_envs(self) -> None:
        args = portal_e2e_smoke._parse_args(["--auth", "bearer"])
        research_command = portal_e2e_smoke._research_agent_command(
            args,
            vault=Path("/vault"),
            job_store_path=Path("/tmp/jobs.json"),
            auth_token="secret",
        )
        pm_command = portal_e2e_smoke._pm_portal_command(args, auth_token="secret")

        self.assertIn("--auth", research_command)
        self.assertIn("bearer", research_command)
        self.assertIn(portal_e2e_smoke.RESEARCH_AGENT_TOKEN_ENV, research_command)
        self.assertIn("--pm-portal-auth", pm_command)
        self.assertIn(portal_e2e_smoke.PM_PORTAL_TOKEN_ENV, pm_command)
        self.assertIn(portal_e2e_smoke.RUNTIME_API_TOKEN_ENV, pm_command)

    def test_no_auth_commands_do_not_include_bearer_flags(self) -> None:
        args = portal_e2e_smoke._parse_args([])
        research_command = portal_e2e_smoke._research_agent_command(
            args,
            vault=Path("/vault"),
            job_store_path=Path("/tmp/jobs.json"),
            auth_token=None,
        )
        pm_command = portal_e2e_smoke._pm_portal_command(args, auth_token=None)

        self.assertNotIn("--auth", research_command)
        self.assertNotIn("--pm-portal-auth", pm_command)

    def test_process_exit_diagnostics_include_exit_code_and_output(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "print('startup failed'); raise SystemExit(7)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        proc.wait(timeout=5)

        with self.assertRaises(portal_e2e_smoke.SmokeError) as context:
            portal_e2e_smoke._raise_if_process_exited([("fake-service", proc)])

        message = str(context.exception)
        self.assertIn("fake-service exited", message)
        self.assertIn("exit code 7", message)
        self.assertIn("startup failed", message)

    def test_wait_json_reports_process_exit_before_timeout(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "print('cannot bind port'); raise SystemExit(48)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        proc.wait(timeout=5)

        with self.assertRaises(portal_e2e_smoke.SmokeError) as context:
            portal_e2e_smoke._wait_json(
                "http://127.0.0.1:1/health",
                timeout_seconds=5,
                processes=[("api", proc)],
            )

        message = str(context.exception)
        self.assertIn("api exited", message)
        self.assertIn("cannot bind port", message)


if __name__ == "__main__":
    unittest.main()
