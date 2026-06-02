#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_VAULT = Path("/Users/minsungkim/Documents/Obsidian Vault")
DEFAULT_AI_PORTAL_ROOT = Path("/Users/minsungkim/Documents/AI Agent Archtecture")
QUICK_TEST_MODULES = [
    "tests.test_config",
    "tests.test_doctor",
    "tests.test_pipeline",
    "tests.test_quality",
    "tests.test_obsidian",
]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.research_root.resolve()

    commands = [_unit_test_command(args)]
    if args.include_rerun_lineage_smoke:
        commands.append(_rerun_lineage_smoke_command(args, root))
    if args.include_portal_smoke:
        commands.append(_portal_smoke_command(args, root))

    for label, command, env in commands:
        print(f"==> {label}", flush=True)
        result = subprocess.run(command, cwd=str(root), env=env)
        if result.returncode != 0:
            print(f"FAILED: {label} exited with {result.returncode}", file=sys.stderr)
            return result.returncode
    print("OK local checks completed", flush=True)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Research Agent local verification checks.")
    parser.add_argument("--research-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", default=sys.executable, help="Python 3.11+ executable used for checks.")
    parser.add_argument("--quick", action="store_true", help="Run a small core unit-test subset for fast local feedback.")
    parser.add_argument("--ci", action="store_true", help="Run CI-safe unit checks without portal smoke or external services.")
    parser.add_argument("--test-start-dir", default="tests")
    parser.add_argument("--test-pattern", default="test*.py")
    parser.add_argument("--include-portal-smoke", action="store_true")
    parser.add_argument("--include-rerun-lineage-smoke", action="store_true")
    parser.add_argument("--portal-smoke-auth", choices=["none", "bearer"], default="none")
    parser.add_argument("--portal-smoke-auto-port", action="store_true", help="Let portal smoke pick free ports when defaults are busy.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--ai-portal-root", type=Path, default=DEFAULT_AI_PORTAL_ROOT)
    parser.add_argument("--research-port", type=int, default=8780)
    parser.add_argument("--pm-portal-port", type=int, default=8770)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    if args.ci and args.quick:
        parser.error("--ci cannot be combined with --quick")
    if args.ci and args.include_portal_smoke:
        parser.error("--ci cannot be combined with --include-portal-smoke")
    if args.ci and args.include_rerun_lineage_smoke:
        parser.error("--ci cannot be combined with --include-rerun-lineage-smoke")
    if args.quick and args.include_portal_smoke:
        parser.error("--quick cannot be combined with --include-portal-smoke")
    if args.quick and args.include_rerun_lineage_smoke:
        parser.error("--quick cannot be combined with --include-rerun-lineage-smoke")
    return args


def _pythonpath_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    return env


def _unit_test_command(args: argparse.Namespace) -> tuple[str, list[str], dict[str, str]]:
    root = args.research_root.resolve()
    if args.quick:
        return "quick unit tests", [args.python, "-m", "unittest", *QUICK_TEST_MODULES], _pythonpath_env(root)
    command = [args.python, "-m", "unittest", "discover", "-s", args.test_start_dir, "-p", args.test_pattern]
    if args.ci:
        return "ci unit tests", command, _pythonpath_env(root)
    return "unit tests", command, _pythonpath_env(root)


def _rerun_lineage_smoke_command(args: argparse.Namespace, root: Path) -> tuple[str, list[str], dict[str, str]]:
    command = [
        args.python,
        str(root / "scripts" / "rerun_lineage_smoke.py"),
        "--timeout",
        str(args.timeout),
    ]
    return "rerun lineage smoke", command, _pythonpath_env(root)


def _portal_smoke_command(args: argparse.Namespace, root: Path) -> tuple[str, list[str], dict[str, str]]:
    command = [
        args.python,
        str(root / "scripts" / "portal_e2e_smoke.py"),
        "--research-root",
        str(root),
        "--vault",
        str(args.vault),
        "--ai-portal-root",
        str(args.ai_portal_root),
        "--research-port",
        str(args.research_port),
        "--pm-portal-port",
        str(args.pm_portal_port),
        "--auth",
        args.portal_smoke_auth,
        "--timeout",
        str(args.timeout),
    ]
    if args.portal_smoke_auto_port:
        command.append("--auto-port")
    return "portal E2E smoke", command, _pythonpath_env(root)


if __name__ == "__main__":
    raise SystemExit(main())
