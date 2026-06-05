from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_loads_toml_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            vault = Path(temp) / "vault"
            config.write_text(
                f"""
[app]
timezone = "Asia/Seoul"

[obsidian]
vault_path = "{vault}"
final_report_dir = "Reports"

[openai.models]
synthesis = "gpt-5.5"

[gemini.models]
synthesis = "gemini-2.5-flash"

[sources]
market_source_domains = ["sec.gov", "reuters.com"]

[report]
bilingual = false

[quality_gates]
block_vault_write_on_fail = true
fail_on_fallback_evidence = true
min_relevant_sources = 2
min_relevant_source_ratio = 0.5

[pipeline]
cleanup_partial_artifacts = false
""",
                encoding="utf-8",
            )
            settings = load_settings(config)
            self.assertEqual(settings.obsidian.vault_path, vault)
            self.assertEqual(settings.obsidian.final_report_dir, "Reports")
            self.assertEqual(settings.openai.models.synthesis, "gpt-5.5")
            self.assertEqual(settings.sources.market_source_domains, ["sec.gov", "reuters.com"])
            self.assertEqual(settings.llm.provider, "auto")
            self.assertEqual(settings.gemini.models.synthesis, "gemini-2.5-flash")
            self.assertFalse(settings.report.bilingual)
            self.assertTrue(settings.quality_gates.block_vault_write_on_fail)
            self.assertTrue(settings.quality_gates.fail_on_fallback_evidence)
            self.assertEqual(settings.quality_gates.min_relevant_sources, 2)
            self.assertEqual(settings.quality_gates.min_relevant_source_ratio, 0.5)
            self.assertFalse(settings.pipeline.cleanup_partial_artifacts)

    def test_rejects_invalid_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.toml"
            vault = Path(temp) / "vault"
            config.write_text(
                f"""
[app]
timezone = "Mars/Nope"

[obsidian]
vault_path = "{vault}"
""",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_settings(config)


if __name__ == "__main__":
    unittest.main()
