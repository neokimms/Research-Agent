from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import (
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.doctor import FAIL, OK, WARN, run_doctor


class DoctorTests(unittest.TestCase):
    def test_doctor_checks_temp_vault_without_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = _settings(Path(temp), common_enabled=False, api_key_env="RESEARCH_AGENT_MISSING_KEY")
            with patch.dict(os.environ, {}, clear=True):
                report = run_doctor(
                    settings,
                    config_path=Path("config/research-agent.example.toml"),
                    env_file=Path(".env"),
                )

            statuses = {check.name: check.status for check in report.checks}
            self.assertEqual(statuses["config loaded"], OK)
            self.assertEqual(statuses["vault writable"], OK)
            self.assertEqual(statuses["reviewed note overwrite protection"], OK)
            self.assertEqual(statuses["openai api key"], WARN)
            self.assertEqual(statuses["gemini api key"], WARN)
            self.assertEqual(statuses["llm provider"], WARN)
            self.assertEqual(statuses["openai smoke"], WARN)
            self.assertFalse(report.has_failures)
            self.assertNotIn(".research-agent-doctor", {path.name for path in Path(temp).iterdir()})

    def test_doctor_fails_on_placeholder_vault_path(self) -> None:
        settings = _settings(Path("/absolute/path/to/Research"))
        report = run_doctor(
            settings,
            config_path=Path("config/research-agent.example.toml"),
            env_file=Path(".env"),
        )

        statuses = {check.name: check.status for check in report.checks}
        self.assertEqual(statuses["vault path configured"], FAIL)
        self.assertTrue(report.has_failures)

    def test_openai_smoke_fails_when_requested_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = _settings(Path(temp), common_enabled=False, api_key_env="RESEARCH_AGENT_MISSING_KEY")
            with patch.dict(os.environ, {}, clear=True):
                report = run_doctor(
                    settings,
                    config_path=Path("config/research-agent.example.toml"),
                    env_file=Path(".env"),
                    openai_smoke=True,
                )

        statuses = {check.name: check.status for check in report.checks}
        self.assertEqual(statuses["openai smoke"], FAIL)
        self.assertTrue(report.has_failures)

    def test_openai_smoke_success_uses_responses_client(self) -> None:
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def create(self, **kwargs):
                return {"output_text": "OK"}

        with tempfile.TemporaryDirectory() as temp:
            settings = _settings(Path(temp), common_enabled=False)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                with patch("research_agent.doctor.OpenAIResponsesClient", FakeClient):
                    report = run_doctor(
                        settings,
                        config_path=Path("config/research-agent.example.toml"),
                        env_file=Path(".env"),
                        openai_smoke=True,
                    )

        statuses = {check.name: check.status for check in report.checks}
        self.assertEqual(statuses["openai api key"], OK)
        self.assertEqual(statuses["openai smoke"], OK)
        self.assertFalse(report.has_failures)

    def test_gemini_smoke_success_uses_gemini_client(self) -> None:
        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def generate(self, **kwargs):
                return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}

        with tempfile.TemporaryDirectory() as temp:
            settings = _settings(Path(temp), common_enabled=False)
            with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
                with patch("research_agent.doctor.GeminiGenerateClient", FakeClient):
                    report = run_doctor(
                        settings,
                        config_path=Path("config/research-agent.example.toml"),
                        env_file=Path(".env"),
                        gemini_smoke=True,
                    )

        statuses = {check.name: check.status for check in report.checks}
        self.assertEqual(statuses["gemini api key"], OK)
        self.assertEqual(statuses["llm provider"], OK)
        self.assertEqual(statuses["gemini smoke"], OK)
        self.assertFalse(report.has_failures)


def _settings(
    vault_path: Path,
    *,
    common_enabled: bool = True,
    api_key_env: str = "OPENAI_API_KEY",
) -> Settings:
    return Settings(
        app=AppSettings(),
        obsidian=ObsidianSettings(vault_path=vault_path),
        openai=OpenAISettings(api_key_env=api_key_env),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=common_enabled, module_path=None),
    )


if __name__ == "__main__":
    unittest.main()
