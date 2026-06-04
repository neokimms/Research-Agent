from __future__ import annotations

import importlib.util
import io
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "portal_ui_smoke.py"
SPEC = importlib.util.spec_from_file_location("portal_ui_smoke", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load UI smoke script: {SCRIPT_PATH}")
portal_ui_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal_ui_smoke)


class PortalUISmokeScriptTests(unittest.TestCase):
    def test_parse_args_defaults_to_local_portals(self) -> None:
        args = portal_ui_smoke._parse_args([])

        self.assertEqual(args.research_url, portal_ui_smoke.DEFAULT_RESEARCH_URL)
        self.assertEqual(args.pm_url, portal_ui_smoke.DEFAULT_PM_URL)
        self.assertEqual(args.timeout, 10.0)

    def test_parse_args_rejects_non_positive_timeout(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                portal_ui_smoke._parse_args(["--timeout", "0"])

    def test_token_resolution_prefers_explicit_value_then_env(self) -> None:
        env_name = "RESEARCH_AGENT_UI_SMOKE_TEST_TOKEN"
        previous = os.environ.get(env_name)
        try:
            os.environ[env_name] = "from-env"
            self.assertEqual(portal_ui_smoke._resolve_token(" explicit ", env_name), "explicit")
            self.assertEqual(portal_ui_smoke._resolve_token(None, env_name), "from-env")
            os.environ[env_name] = " "
            self.assertIsNone(portal_ui_smoke._resolve_token(None, env_name))
        finally:
            if previous is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous

    def test_research_portal_assets_are_validated(self) -> None:
        html = """
        <section>Research Workflow <div id="workflowTrack"></div></section>
        <details><strong id="systemSummary"></strong></details>
        <button id="refreshButton">상태 갱신</button><span id="refreshFeedback"></span>
        <section class="review-board layout-wide"></section>
        <form id="runForm">
          <label>리서치 질문 / 목표</label>
          <textarea id="topicInput"></textarea>
          <select id="providerInput"></select>
          <div id="presetButtons"></div>
          <select id="depthInput"></select>
          <input id="dryRunInput">
          <input id="offlineInput">
          <input id="bilingualInput">
          <div id="resultOutput"></div>
          <div id="reviewActionList"></div>
          <div id="progressSteps"></div>
          <div id="jobList"></div>
          <strong id="jobStoreStatus"></strong>
          <div id="actionList"></div>
          <a href="/guide">가이드</a>
        </form>
        """
        guide = """
        <h1>리서치 에이전트 포털 가이드</h1>
        <h2>가장 안전한 실행 순서</h2>
        <h3>리서치 전략</h3>
        <p>Quality Gate</p>
        <h2>Research Agent Portal과 PM Portal의 차이</h2>
        """
        js = """
        async function submitRun() {}
        function applyPreset() {}
        function renderWorkflow() {}
        function setRefreshState() {}
        function renderJobResult() {}
        function renderMarkdown() {}
        function renderReviewActions() {}
        function startPolling() {}
        async function refreshJobs() {}
        async function refreshJobStoreHealth() {}
        function renderNextActions() {}
        """
        seen: list[tuple[str, str | None, float]] = []

        def fake_fetch(url: str, *, bearer_token: str | None = None, timeout_seconds: float = 10.0) -> str:
            seen.append((url, bearer_token, timeout_seconds))
            if url.endswith("/assets/portal.js"):
                return js
            return guide if url.endswith("/guide") else html

        with patch.object(portal_ui_smoke, "_fetch_text", side_effect=fake_fetch):
            portal_ui_smoke._validate_research_portal_assets(
                "http://research.local/",
                bearer_token="token",
                timeout_seconds=3.0,
            )

        self.assertEqual(
            seen,
            [
                ("http://research.local/", "token", 3.0),
                ("http://research.local/guide", "token", 3.0),
                ("http://research.local/assets/portal.js", "token", 3.0),
            ],
        )

    def test_pm_portal_assets_are_validated(self) -> None:
        html = """
          <form id="scenario-form">
          <select id="scenario-select"></select>
          <select id="runtime-provider"></select>
          <button data-research-preset="dry_run">리서치 드라이런</button>
          <button data-research-preset="live">리서치 실사용</button>
          <a href="#research">리서치</a>
          <section id="research"><h2>리서치 실행</h2></section>
          <strong id="research-job-store-status"></strong>
          <select id="research-job-status-filter"></select>
          <div id="research-job-list"></div>
          <strong id="metric-job-store"></strong>
          <tbody id="jobs-body"></tbody>
        </form>
        """
        js = """
        async function runScenario() {}
        function runtimeOptions() {}
        function applyResearchPreset() {}
        function renderResearchJobs() {}
        function renderResearchJobStore() {}
        async function rerunResearchJob(jobId) { return { rerun_of: jobId }; }
        async function loadJobStoreHealth() {}
        """
        seen: list[tuple[str, str | None, float]] = []

        def fake_fetch(url: str, *, bearer_token: str | None = None, timeout_seconds: float = 10.0) -> str:
            seen.append((url, bearer_token, timeout_seconds))
            return js if url.endswith("/assets/portal.js") else html

        with patch.object(portal_ui_smoke, "_fetch_text", side_effect=fake_fetch):
            portal_ui_smoke._validate_pm_portal_assets(
                "http://pm.local/",
                bearer_token="token",
                timeout_seconds=3.0,
            )

        self.assertEqual(
            seen,
            [
                ("http://pm.local/", "token", 3.0),
                ("http://pm.local/assets/portal.js", "token", 3.0),
            ],
        )

    def test_missing_fragments_are_reported(self) -> None:
        with self.assertRaises(portal_ui_smoke.UISmokeError) as context:
            portal_ui_smoke._assert_contains_all("only one", ["only one", "missing"], label="asset")

        self.assertIn("missing", str(context.exception))


if __name__ == "__main__":
    unittest.main()
