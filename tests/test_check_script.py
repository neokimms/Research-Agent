from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check.py"
SPEC = importlib.util.spec_from_file_location("research_agent_check", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load check script: {SCRIPT_PATH}")
research_agent_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research_agent_check)


class CheckScriptTests(unittest.TestCase):
    def test_unit_test_command_uses_unittest_discover_and_src_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = research_agent_check._parse_args(["--research-root", str(root), "--python", "/py"])
            label, command, env = research_agent_check._unit_test_command(args)

        self.assertEqual(label, "unit tests")
        self.assertEqual(command[:4], ["/py", "-m", "unittest", "discover"])
        self.assertIn("-s", command)
        self.assertIn("tests", command)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_quick_unit_test_command_runs_core_modules_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = research_agent_check._parse_args(["--research-root", str(root), "--python", "/py", "--quick"])
            label, command, env = research_agent_check._unit_test_command(args)

        self.assertEqual(label, "quick unit tests")
        self.assertEqual(command[:3], ["/py", "-m", "unittest"])
        self.assertEqual(command[3:], research_agent_check.QUICK_TEST_MODULES)
        self.assertNotIn("discover", command)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_ci_unit_test_command_uses_discover_without_portal_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = research_agent_check._parse_args(["--research-root", str(root), "--python", "/py", "--ci"])
            label, command, env = research_agent_check._unit_test_command(args)

        self.assertEqual(label, "ci unit tests")
        self.assertEqual(command[:4], ["/py", "-m", "unittest", "discover"])
        self.assertIn("-s", command)
        self.assertIn("tests", command)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_quick_rejects_portal_smoke(self) -> None:
        for flag in ("--include-portal-smoke", "--include-rerun-lineage-smoke"):
            with self.subTest(flag=flag):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        research_agent_check._parse_args(["--quick", flag])

    def test_ci_rejects_quick_and_portal_smoke(self) -> None:
        for flags in (
            ["--ci", "--quick"],
            ["--ci", "--include-portal-smoke"],
            ["--ci", "--include-rerun-lineage-smoke"],
        ):
            with self.subTest(flags=flags):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        research_agent_check._parse_args(flags)

    def test_portal_smoke_command_passes_auth_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "research"
            vault = Path(temp) / "vault"
            portal = Path(temp) / "ai"
            args = research_agent_check._parse_args(
                [
                    "--research-root",
                    str(root),
                    "--python",
                    "/py",
                    "--include-portal-smoke",
                    "--portal-smoke-auth",
                    "bearer",
                    "--portal-smoke-auto-port",
                    "--vault",
                    str(vault),
                    "--ai-portal-root",
                    str(portal),
                    "--research-port",
                    "8781",
                    "--pm-portal-port",
                    "8771",
                ]
            )
            label, command, env = research_agent_check._portal_smoke_command(args, root)

        self.assertEqual(label, "portal E2E smoke")
        self.assertEqual(command[0], "/py")
        self.assertIn(str(root / "scripts" / "portal_e2e_smoke.py"), command)
        self.assertIn("--auth", command)
        self.assertIn("bearer", command)
        self.assertIn("--auto-port", command)
        self.assertIn(str(vault), command)
        self.assertIn(str(portal), command)
        self.assertIn("8781", command)
        self.assertIn("8771", command)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_rerun_lineage_smoke_command_passes_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            args = research_agent_check._parse_args(
                [
                    "--research-root",
                    str(root),
                    "--python",
                    "/py",
                    "--include-rerun-lineage-smoke",
                    "--timeout",
                    "12",
                ]
            )
            label, command, env = research_agent_check._rerun_lineage_smoke_command(args, root)

        self.assertEqual(label, "rerun lineage smoke")
        self.assertEqual(command[0], "/py")
        self.assertIn(str(root / "scripts" / "rerun_lineage_smoke.py"), command)
        self.assertIn("--timeout", command)
        self.assertIn("12.0", command)
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], str(root / "src"))

    def test_parse_args_rejects_non_positive_timeout(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                research_agent_check._parse_args(["--timeout", "0"])


if __name__ == "__main__":
    unittest.main()
