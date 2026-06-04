#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_VAULT = Path("/Users/minsungkim/Documents/Obsidian Vault")
DEFAULT_AI_PORTAL_ROOT = Path("/Users/minsungkim/Documents/AI Agent Archtecture")
DEFAULT_SMOKE_TOKEN_ENV = "RESEARCH_AGENT_PORTAL_E2E_TOKEN"
RESEARCH_AGENT_TOKEN_ENV = "RESEARCH_AGENT_PORTAL_TOKEN"
PM_PORTAL_TOKEN_ENV = "PM_PORTAL_TOKEN"
RUNTIME_API_TOKEN_ENV = "RUNTIME_API_TOKEN"


class SmokeError(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if sys.version_info < (3, 11):
        raise SmokeError("Use Python 3.11+ so both portal runtimes can import correctly.")

    research_root = args.research_root.resolve()
    ai_portal_root = args.ai_portal_root.resolve()
    vault = args.vault.expanduser().resolve()
    job_store_path = args.job_store_path or Path(f"/private/tmp/research-agent-e2e-{int(time.time())}.json")
    objective = args.objective or f"PM Portal to Research Agent E2E smoke test {time.strftime('%Y%m%d-%H%M%S')}"
    auth_token = _resolve_auth_token(args)
    expected_options = {
        "provider": args.provider,
        "offline": args.offline,
        "dry_run": True,
        "max_papers_per_source": args.max_papers_per_source,
    }

    _assert_project_root(research_root, "research_agent")
    _assert_project_root(ai_portal_root, "supervisor_graph_hybrid")
    if not vault.exists():
        raise SmokeError(f"Vault path does not exist: {vault}")
    if args.auto_port:
        _assign_auto_ports(args)
    _assert_port_free(args.host, args.research_port)
    _assert_port_free(args.host, args.pm_portal_port)

    processes: list[tuple[str, subprocess.Popen[str]]] = []
    try:
        research_proc = _start_research_agent_api(
            args=args,
            research_root=research_root,
            vault=vault,
            job_store_path=job_store_path,
            auth_token=auth_token,
        )
        processes.append(("research-agent-api", research_proc))
        research_health = _wait_json(
            f"http://{args.host}:{args.research_port}/health",
            timeout_seconds=args.timeout,
            processes=[("research-agent-api", research_proc)],
        )
        _verify_research_portal_static(
            f"http://{args.host}:{args.research_port}",
            bearer_token=auth_token,
        )

        pm_proc = _start_pm_portal(args=args, ai_portal_root=ai_portal_root, auth_token=auth_token)
        processes.append(("pm-portal", pm_proc))
        pm_health = _wait_json(
            f"http://{args.host}:{args.pm_portal_port}/health",
            timeout_seconds=args.timeout,
            processes=processes,
        )
        _verify_pm_research_presets(
            f"http://{args.host}:{args.pm_portal_port}",
            bearer_token=auth_token,
        )
        job_store_health = _request_json(
            f"http://{args.host}:{args.pm_portal_port}/api/job-store-health?retention_days=90&retention_limit=200",
            bearer_token=auth_token,
        )
        _assert_job_store_health(job_store_health)

        run_payload = {"objective": objective, **expected_options}
        created = _request_json(
            f"http://{args.host}:{args.pm_portal_port}/api/runs",
            method="POST",
            payload=run_payload,
            bearer_token=auth_token,
        )
        _assert_runtime_options(created, expected_options)
        job_id = str(created.get("job_id", "")).strip()
        if not job_id:
            raise SmokeError(f"PM Portal did not return a job_id: {created}")

        job = _poll_job(
            f"http://{args.host}:{args.pm_portal_port}/api/jobs/{job_id}",
            timeout_seconds=args.timeout,
            processes=processes,
            bearer_token=auth_token,
        )
        if job.get("status") != "completed":
            raise SmokeError(f"Job did not complete successfully: {job}")
        if job.get("provider") != args.provider:
            raise SmokeError(f"Provider was not preserved: expected {args.provider!r}, got {job.get('provider')!r}")
        if job.get("dry_run") is not True or job.get("offline") is not args.offline:
            raise SmokeError(f"Dry-run/offline flags were not preserved: {job}")

        summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
        if summary.get("type") != "dry_run":
            raise SmokeError(f"Expected dry_run summary, got: {summary}")

        planned_artifacts = _planned_artifacts(job)
        if not planned_artifacts:
            raise SmokeError("Dry-run job did not return planned artifacts.")
        existing_artifacts = [path for path in planned_artifacts if Path(path).exists()]
        if existing_artifacts:
            raise SmokeError("Dry-run planned artifacts already exist in the vault: " + ", ".join(existing_artifacts[:5]))

        run_id = str(job.get("run_id") or job_id)
        run_detail = _request_json(
            f"http://{args.host}:{args.pm_portal_port}/api/runs/{run_id}",
            bearer_token=auth_token,
        )
        if run_detail.get("status") != "completed":
            raise SmokeError(f"Run detail proxy did not return the completed run: {run_detail}")

        print("OK portal E2E smoke completed")
        print(f"Research Agent API: {research_health.get('service')} provider={research_health.get('provider')}")
        print(f"PM Portal runtime: {pm_health.get('runtime_base_url')}")
        print(f"job_id: {job_id}")
        print(f"run_id: {run_id}")
        print(f"planned_artifacts: {len(planned_artifacts)}")
        print("pm_presets: ok")
        print(f"job_store_prune_candidates: {job_store_health['cleanup_preview']['prune_candidates']}")
        print(f"auth: {args.auth}")
        print(f"ports: research={args.research_port} pm_portal={args.pm_portal_port}")
        print("vault_writes: none")
        return 0
    finally:
        for name, proc in reversed(processes):
            _stop_process(name, proc)
        if not args.keep_job_store:
            _remove_file(job_store_path)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a safe PM Portal -> Research Agent Portal API dry-run smoke test.",
    )
    parser.add_argument("--research-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ai-portal-root", type=Path, default=DEFAULT_AI_PORTAL_ROOT)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--python", default=sys.executable, help="Python 3.11+ executable used to start both servers.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--research-port", type=int, default=8780)
    parser.add_argument("--pm-portal-port", type=int, default=8770)
    parser.add_argument("--auto-port", action="store_true", help="Use free ports when requested portal ports are busy.")
    parser.add_argument("--job-store-path", type=Path)
    parser.add_argument("--keep-job-store", action="store_true")
    parser.add_argument("--objective")
    parser.add_argument("--provider", choices=["auto", "openai", "gemini"], default="gemini")
    parser.add_argument("--online", dest="offline", action="store_false", help="Allow online collectors during dry-run.")
    parser.add_argument("--max-papers-per-source", type=int, default=1)
    parser.add_argument("--auth", choices=["none", "bearer"], default="none", help="Enable bearer auth for both portal hops.")
    parser.add_argument(
        "--auth-token-env",
        default=DEFAULT_SMOKE_TOKEN_ENV,
        help="Environment variable containing the smoke bearer token. A token is generated when unset.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.set_defaults(offline=True)
    args = parser.parse_args(argv)
    if args.max_papers_per_source <= 0:
        parser.error("--max-papers-per-source must be greater than 0")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    return args


def _resolve_auth_token(args: argparse.Namespace) -> str | None:
    if args.auth == "none":
        return None
    token = os.environ.get(args.auth_token_env, "").strip() if args.auth_token_env else ""
    return token or secrets.token_urlsafe(24)


def _assert_project_root(root: Path, package_name: str) -> None:
    package_dir = root / "src" / package_name
    if not package_dir.exists():
        raise SmokeError(f"Expected project package at {package_dir}")


def _assign_auto_ports(args: argparse.Namespace) -> None:
    selected: set[int] = set()
    if _is_port_free(args.host, args.research_port):
        selected.add(args.research_port)
    else:
        args.research_port = _pick_free_port(args.host, selected)
        selected.add(args.research_port)

    if args.pm_portal_port not in selected and _is_port_free(args.host, args.pm_portal_port):
        selected.add(args.pm_portal_port)
        return
    args.pm_portal_port = _pick_free_port(args.host, selected)


def _assert_port_free(host: str, port: int) -> None:
    if not _is_port_free(host, port):
        raise SmokeError(f"Port is already in use: {host}:{port}")


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_free_port(host: str, excluded: set[int]) -> int:
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            port = int(sock.getsockname()[1])
        if port not in excluded:
            return port
    raise SmokeError("Unable to find a free local port for portal smoke.")


def _env_with_pythonpath(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    return env


def _start_research_agent_api(
    *,
    args: argparse.Namespace,
    research_root: Path,
    vault: Path,
    job_store_path: Path,
    auth_token: str | None,
) -> subprocess.Popen[str]:
    env = _env_with_pythonpath(research_root)
    if auth_token:
        env[RESEARCH_AGENT_TOKEN_ENV] = auth_token
    return _start_process(
        _research_agent_command(args, vault=vault, job_store_path=job_store_path, auth_token=auth_token),
        cwd=research_root,
        env=env,
    )


def _research_agent_command(
    args: argparse.Namespace,
    *,
    vault: Path,
    job_store_path: Path,
    auth_token: str | None,
) -> list[str]:
    command = [
        args.python,
        "-m",
        "research_agent",
        "--vault",
        str(vault),
        "serve-portal-api",
        "--host",
        args.host,
        "--port",
        str(args.research_port),
        "--job-store-path",
        str(job_store_path),
        "--max-workers",
        "1",
    ]
    if auth_token:
        command.extend(["--auth", "bearer", "--token-env", RESEARCH_AGENT_TOKEN_ENV])
    return command


def _start_pm_portal(
    *,
    args: argparse.Namespace,
    ai_portal_root: Path,
    auth_token: str | None,
) -> subprocess.Popen[str]:
    env = _env_with_pythonpath(ai_portal_root)
    if auth_token:
        env[PM_PORTAL_TOKEN_ENV] = auth_token
        env[RUNTIME_API_TOKEN_ENV] = auth_token
    return _start_process(
        _pm_portal_command(args, auth_token=auth_token),
        cwd=ai_portal_root,
        env=env,
    )


def _pm_portal_command(args: argparse.Namespace, *, auth_token: str | None) -> list[str]:
    command = [
        args.python,
        "-m",
        "supervisor_graph_hybrid",
        "--serve-pm-portal",
        "--pm-portal-host",
        args.host,
        "--pm-portal-port",
        str(args.pm_portal_port),
        "--pm-portal-runtime-url",
        f"http://{args.host}:{args.research_port}",
    ]
    if auth_token:
        command.extend(
            [
                "--pm-portal-auth",
                "bearer",
                "--pm-portal-token-env",
                PM_PORTAL_TOKEN_ENV,
                "--pm-portal-runtime-token-env",
                RUNTIME_API_TOKEN_ENV,
            ]
        )
    return command


def _start_process(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def _stop_process(name: str, proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    output = _process_output(proc)
    if output:
        print(f"[{name} output]")
        print(output.rstrip())


def _process_output(proc: subprocess.Popen[str]) -> str:
    if proc.stdout is None:
        return ""
    try:
        output = proc.stdout.read()
    except (OSError, ValueError):
        return ""
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
    return output or ""


def _raise_if_process_exited(processes: list[tuple[str, subprocess.Popen[str]]] | None) -> None:
    for name, proc in processes or []:
        exit_code = proc.poll()
        if exit_code is None:
            continue
        message = f"{name} exited before the smoke step completed (exit code {exit_code})."
        output = _process_output(proc).strip()
        if output:
            message = f"{message}\n[{name} output]\n{output}"
        raise SmokeError(message)


def _wait_json(
    url: str,
    *,
    timeout_seconds: float,
    processes: list[tuple[str, subprocess.Popen[str]]] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        _raise_if_process_exited(processes)
        try:
            return _request_json(url)
        except (SmokeError, urllib.error.URLError) as exc:
            last_error = exc
            _raise_if_process_exited(processes)
            time.sleep(0.25)
    raise SmokeError(f"Timed out waiting for {url}: {last_error}")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"{method} {url} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SmokeError(f"{method} {url} failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{method} {url} returned invalid JSON: {exc}") from exc


def _request_text(url: str, *, bearer_token: str | None = None) -> str:
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"GET {url} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SmokeError(f"GET {url} failed: {exc}") from exc


def _verify_research_portal_static(base_url: str, *, bearer_token: str | None = None) -> None:
    html = _request_text(f"{base_url.rstrip('/')}/", bearer_token=bearer_token)
    guide = _request_text(f"{base_url.rstrip('/')}/guide", bearer_token=bearer_token)
    js = _request_text(f"{base_url.rstrip('/')}/assets/portal.js", bearer_token=bearer_token)
    _assert_contains_all(
        html,
        [
            "리서치 에이전트 포털",
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
            'id="refreshFeedback"',
            'class="review-board layout-wide"',
            'id="providerInput"',
            'id="presetButtons"',
            'id="depthInput"',
            'id="bilingualInput"',
            'id="reviewActionList"',
            'id="progressSteps"',
            'id="jobStoreStatus"',
            'id="actionList"',
            'href="/guide"',
            "리서치 리뷰",
            "Vault 정비",
        ],
        label="Research Portal HTML",
    )
    _assert_contains_all(
        guide,
        [
            "리서치 에이전트 포털 가이드",
            "가장 안전한 실행 순서",
            "리서치 전략",
            "상황별 권장 옵션",
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
            "function renderReportLinks",
            "function renderMarkdown",
            "결과 보고서",
            "function renderReviewActions",
            "async function refreshJobStoreHealth",
            "function renderNextActions",
        ],
        label="Research Portal JavaScript",
    )


def _verify_pm_research_presets(base_url: str, *, bearer_token: str | None = None) -> None:
    html = _request_text(f"{base_url.rstrip('/')}/", bearer_token=bearer_token)
    js = _request_text(f"{base_url.rstrip('/')}/assets/portal.js", bearer_token=bearer_token)
    _assert_contains_all(
        html,
        [
            'id="scenario-form"',
            'id="runtime-provider"',
            '<option value="gemini" selected>gemini</option>',
            'id="runtime-max-papers"',
            'value="1"',
            'id="runtime-offline" type="checkbox" checked',
            'id="runtime-dry-run" type="checkbox" checked',
            'data-research-preset="dry_run"',
            "리서치 드라이런",
            'data-research-preset="live"',
            "리서치 실사용",
            'href="#research"',
            "리서치 실행",
            'id="research-job-store-status"',
            'id="research-job-status-filter"',
            'id="research-job-list"',
        ],
        label="PM Portal HTML",
    )
    _assert_contains_all(
        js,
        [
            "function applyResearchPreset",
            'dry_run: { provider: "gemini", offline: true, dry_run: true, max_papers_per_source: 1 }',
            'live: { provider: "auto", offline: false, dry_run: false, max_papers_per_source: 2 }',
            'document.querySelectorAll("[data-research-preset]")',
            "function renderResearchJobs",
            "function renderResearchJobStore",
            "async function rerunResearchJob",
            "rerun_of: jobId",
        ],
        label="PM Portal JavaScript",
    )


def _assert_contains_all(text: str, expected_fragments: list[str], *, label: str) -> None:
    missing = [fragment for fragment in expected_fragments if fragment not in text]
    if missing:
        raise SmokeError(f"{label} is missing expected Research preset fragments: {missing}")


def _poll_job(
    url: str,
    *,
    timeout_seconds: float,
    processes: list[tuple[str, subprocess.Popen[str]]] | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        _raise_if_process_exited(processes)
        try:
            payload = _request_json(url, bearer_token=bearer_token)
        except SmokeError as exc:
            last_error = exc
            _raise_if_process_exited(processes)
            time.sleep(0.5)
            continue
        status = payload.get("status")
        if status in {"completed", "failed", "cancelled", "interrupted"}:
            return payload
        time.sleep(0.5)
    detail = f": {last_error}" if last_error else ""
    raise SmokeError(f"Timed out waiting for job completion: {url}{detail}")


def _assert_runtime_options(created: dict[str, Any], expected_options: dict[str, Any]) -> None:
    portal = created.get("portal") if isinstance(created.get("portal"), dict) else {}
    options = portal.get("runtime_options") if isinstance(portal.get("runtime_options"), dict) else {}
    for key, expected in expected_options.items():
        if options.get(key) != expected:
            raise SmokeError(f"Runtime option {key!r} was not preserved: expected {expected!r}, got {options.get(key)!r}")


def _assert_job_store_health(payload: dict[str, Any]) -> None:
    preview = payload.get("cleanup_preview") if isinstance(payload.get("cleanup_preview"), dict) else None
    if preview is None:
        raise SmokeError(f"Job store health did not include cleanup_preview: {payload}")
    if not isinstance(payload.get("total_jobs"), int):
        raise SmokeError(f"Job store health did not include total_jobs count: {payload}")
    if not isinstance(preview.get("prune_candidates"), int):
        raise SmokeError(f"Job store health did not include prune_candidates count: {payload}")


def _planned_artifacts(job: dict[str, Any]) -> list[str]:
    paths = job.get("paths") if isinstance(job.get("paths"), dict) else {}
    planned = paths.get("planned_artifacts")
    if isinstance(planned, list):
        return [str(path) for path in planned]
    summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
    summary_paths = summary.get("paths") if isinstance(summary.get("paths"), dict) else {}
    summary_planned = summary_paths.get("planned_artifacts")
    if isinstance(summary_planned, list):
        return [str(path) for path in summary_planned]
    return []


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
