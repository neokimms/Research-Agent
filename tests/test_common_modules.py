from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.common_modules import configure_common_modules
from research_agent.config import (
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.obsidian import ObsidianWriter
from research_agent.secrets import resolve_gemini_api_key, resolve_openai_api_key, select_llm_provider


COMMON_MODULE_SRC = Path(__file__).resolve().parents[2] / "Common Module" / "src"


class CommonModuleIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        if not COMMON_MODULE_SRC.exists():
            self.skipTest("Common Module source path is not available")

    def test_detects_common_modules(self) -> None:
        status = configure_common_modules(COMMON_MODULE_SRC)

        self.assertTrue(status.llm_key_manager)
        self.assertTrue(status.obsidian_connector)

    def test_llm_key_manager_rejects_placeholder_openai_key(self) -> None:
        settings = _settings_with_common(Path(tempfile.gettempdir()))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "your_api_key"}, clear=False):
            self.assertIsNone(resolve_openai_api_key(settings))

    def test_auto_provider_falls_back_to_gemini_key(self) -> None:
        settings = _settings_with_common(Path(tempfile.gettempdir()))
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
            self.assertEqual(resolve_gemini_api_key(settings), "gemini-test-key")
            selection = select_llm_provider(settings)

        self.assertEqual(selection.provider, "gemini")
        self.assertTrue(selection.available)

    def test_obsidian_writer_uses_common_connector_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            writer = ObsidianWriter(
                ObsidianSettings(vault_path=Path(temp)),
                common_module_path=COMMON_MODULE_SRC,
            )
            writer.ensure_structure()
            written = writer.write_note("00_Inbox", "common-module-test.md", "# Hello")

            self.assertTrue(writer.using_common_connector)
            self.assertTrue(written.exists())
            self.assertIn("# Hello", written.read_text(encoding="utf-8"))


def _settings_with_common(vault_path: Path) -> Settings:
    return Settings(
        app=AppSettings(),
        obsidian=ObsidianSettings(vault_path=vault_path),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=True, module_path=COMMON_MODULE_SRC),
    )


if __name__ == "__main__":
    unittest.main()
