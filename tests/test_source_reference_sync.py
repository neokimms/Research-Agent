from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.cli import main
from research_agent.config import (
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.source_reference_sync import render_source_reference_sync_result, sync_source_references


class SourceReferenceSyncTests(unittest.TestCase):
    def test_dry_run_detects_downstream_reference_updates_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            ledger = vault / "50_Evidence-Ledger" / "evidence.md"
            before = ledger.read_text(encoding="utf-8")

            result = sync_source_references(_settings(vault), dry_run=True)
            rendered = render_source_reference_sync_result(result)
            after = ledger.read_text(encoding="utf-8")

        self.assertEqual(after, before)
        self.assertEqual(len(result.updated_paths), 2)
        self.assertIn("Source Reference Sync Dry Run", rendered)
        self.assertIn("Would update notes: 2", rendered)
        self.assertIn("Replacements: 2", rendered)

    def test_apply_syncs_evidence_ledger_and_service_blueprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            result = sync_source_references(_settings(vault), dry_run=False)
            ledger = (vault / "50_Evidence-Ledger" / "evidence.md").read_text(encoding="utf-8")
            blueprint = (vault / "30_Service-Blueprints" / "blueprint.md").read_text(encoding="utf-8")

        self.assertEqual(len(result.updated_paths), 2)
        self.assertIn("Fresh claim from exact official docs.", ledger)
        self.assertIn("https://docs.example.com/exact/page", ledger)
        self.assertIn("official-docs: Fresh citable evidence from exact official docs.", ledger)
        self.assertNotIn("https://docs.example.com/ | official-docs", ledger)
        self.assertIn("[Exact Docs Page](https://docs.example.com/exact/page)", blueprint)
        self.assertNotIn("[Seed Docs](https://docs.example.com/)", blueprint)

    def test_in_sync_notes_do_not_count_as_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            sync_source_references(_settings(vault), dry_run=False)

            result = sync_source_references(_settings(vault), dry_run=True)

        self.assertEqual(result.updated_paths, [])
        self.assertEqual(result.replacements, [])

    def test_cli_defaults_to_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            config = root / "config.toml"
            config.write_text(
                f"""[obsidian]
vault_path = "{vault.as_posix()}"

[common_modules]
enabled = false
""",
                encoding="utf-8",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["--config", str(config), "sync-source-references"])

        self.assertEqual(code, 0)
        self.assertIn("Source Reference Sync Dry Run", output.getvalue())
        self.assertIn("Would update notes: 2", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_fixture(vault: Path) -> None:
    source = vault / "10_Sources" / "official-docs" / "source.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
type: source-note
topic: "Agentic RAG"
source_id: "S001"
source_type: "official-docs"
source_url: "https://docs.example.com/exact/page"
canonical_url: "https://docs.example.com/exact/page"
title: "Exact Docs Page"
checked_at: "2026-06-01"
status: draft
generated_by: research-agent
---
# Exact Docs Page

## Important Claims

- E001 (medium, official-docs): Fresh claim from exact official docs.

## Citable Evidence

- Source: https://docs.example.com/exact/page
- E001: Fresh citable evidence from exact official docs.
""",
        encoding="utf-8",
    )

    ledger = vault / "50_Evidence-Ledger" / "evidence.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        """---
type: evidence-ledger
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Evidence Ledger

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | Stale seed claim. | https://docs.example.com/ | official-docs | 2026-05-31 | low | official-docs: stale evidence |
""",
        encoding="utf-8",
    )

    blueprint = vault / "30_Service-Blueprints" / "blueprint.md"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    blueprint.write_text(
        """---
type: service-blueprint
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Blueprint

## Evidence

- [Seed Docs](https://docs.example.com/) (official-docs)
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
