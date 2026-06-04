#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request


DEFAULT_RESEARCH_URL = "http://127.0.0.1:8780"
DEFAULT_PM_URL = "http://127.0.0.1:8770"
RESEARCH_TOKEN_ENV = "RESEARCH_AGENT_PORTAL_TOKEN"
PM_TOKEN_ENV = "PM_PORTAL_TOKEN"


class UISmokeError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_research_portal_assets(
        args.research_url,
        bearer_token=_resolve_token(args.research_token, RESEARCH_TOKEN_ENV),
        timeout_seconds=args.timeout,
    )
    _validate_pm_portal_assets(
        args.pm_url,
        bearer_token=_resolve_token(args.pm_token, PM_TOKEN_ENV),
        timeout_seconds=args.timeout,
    )
    print("OK portal UI static smoke completed")
    print("Research Portal UI: ok")
    print("PM Portal UI: ok")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate static UI assets for running Research Agent and PM Portal servers.",
    )
    parser.add_argument("--research-url", default=DEFAULT_RESEARCH_URL)
    parser.add_argument("--pm-url", default=DEFAULT_PM_URL)
    parser.add_argument("--research-token", help=f"Bearer token for Research Agent UI. Defaults to {RESEARCH_TOKEN_ENV}.")
    parser.add_argument("--pm-token", help=f"Bearer token for PM Portal UI. Defaults to {PM_TOKEN_ENV}.")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return args


def _resolve_token(explicit_token: str | None, env_name: str) -> str | None:
    token = explicit_token if explicit_token is not None else os.environ.get(env_name)
    token = (token or "").strip()
    return token or None


def _validate_research_portal_assets(
    base_url: str,
    *,
    bearer_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    base = base_url.rstrip("/")
    html = _fetch_text(f"{base}/", bearer_token=bearer_token, timeout_seconds=timeout_seconds)
    guide = _fetch_text(f"{base}/guide", bearer_token=bearer_token, timeout_seconds=timeout_seconds)
    js = _fetch_text(f"{base}/assets/portal.js", bearer_token=bearer_token, timeout_seconds=timeout_seconds)
    _assert_contains_all(
        html,
        [
            "Research Workflow",
            "리서치 질문 / 목표",
            "상태 갱신",
            "포털 접근 토큰",
            'id="runForm"',
            'id="workflowTrack"',
            'id="systemDrawerButton"',
            'id="systemDrawerBackdrop"',
            'id="systemDrawer"',
            'id="systemDrawerClose"',
            'id="systemSummary"',
            'id="tokenInput"',
            'id="reportModalBackdrop"',
            'id="reportModal"',
            'id="reportModalTabs"',
            'id="reportModalContent"',
            'id="refreshFeedback"',
            'class="review-board layout-wide"',
            'id="topicInput"',
            'id="providerInput"',
            'id="presetButtons"',
            'id="depthInput"',
            'id="dryRunInput"',
            'id="offlineInput"',
            'id="bilingualInput"',
            'id="resultOutput"',
            'id="reviewActionList"',
            'id="progressSteps"',
            'id="jobList"',
            'id="jobStoreStatus"',
            'id="actionList"',
            'href="/guide"',
        ],
        label="Research Portal HTML",
    )
    _assert_contains_all(
        guide,
        [
            "리서치 에이전트 포털 가이드",
            "가장 안전한 실행 순서",
            "리서치 전략",
            "Quality Gate",
            "Research Agent Portal과 PM Portal의 차이",
        ],
        label="Research Portal Guide",
    )
    _assert_contains_all(
        js,
        [
            "async function submitRun",
            "function applyPreset",
            "function renderWorkflow",
            "function setRefreshState",
            "function setSystemDrawer",
            "function renderJobResult",
            "function setReportModal",
            "function renderReportModal",
            "function renderReportLinks",
            "function renderMarkdown",
            "결과 보고서",
            "보고서 상세 보기",
            "function renderReviewActions",
            "function startPolling",
            "async function refreshJobs",
            "async function refreshJobStoreHealth",
            "function renderNextActions",
        ],
        label="Research Portal JavaScript",
    )


def _validate_pm_portal_assets(
    base_url: str,
    *,
    bearer_token: str | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    base = base_url.rstrip("/")
    html = _fetch_text(f"{base}/", bearer_token=bearer_token, timeout_seconds=timeout_seconds)
    js = _fetch_text(f"{base}/assets/portal.js", bearer_token=bearer_token, timeout_seconds=timeout_seconds)
    _assert_contains_all(
        html,
        [
            'id="scenario-form"',
            'id="scenario-select"',
            'id="runtime-provider"',
            'data-research-preset="dry_run"',
            "리서치 드라이런",
            'data-research-preset="live"',
            "리서치 실사용",
            'href="#research"',
            "리서치 실행",
            'id="research-job-store-status"',
            'id="research-job-status-filter"',
            'id="research-job-list"',
            'id="jobs-body"',
            'id="metric-job-store"',
        ],
        label="PM Portal HTML",
    )
    _assert_contains_all(
        js,
        [
            "async function runScenario",
            "function runtimeOptions",
            "function applyResearchPreset",
            "function renderResearchJobs",
            "function renderResearchJobStore",
            "async function rerunResearchJob",
            "rerun_of: jobId",
            "async function loadJobStoreHealth",
        ],
        label="PM Portal JavaScript",
    )


def _fetch_text(url: str, *, bearer_token: str | None = None, timeout_seconds: float = 10.0) -> str:
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise UISmokeError(f"GET {url} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UISmokeError(f"GET {url} failed: {exc}") from exc


def _assert_contains_all(text: str, expected_fragments: list[str], *, label: str) -> None:
    missing = [fragment for fragment in expected_fragments if fragment not in text]
    if missing:
        raise UISmokeError(f"{label} is missing expected fragments: {missing}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UISmokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
