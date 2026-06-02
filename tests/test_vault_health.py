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
from research_agent.vault_health import OK, WARN, build_vault_health, render_vault_health


class VaultHealthTests(unittest.TestCase):
    def test_healthy_vault_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_healthy_fixture(vault)

            report = build_vault_health(_settings(vault))
            rendered = render_vault_health(report)

        self.assertEqual(report.status, OK)
        self.assertEqual(report.reviewed_core_notes, 4)
        self.assertEqual(report.draft_core_notes, 0)
        self.assertEqual(report.backlink_candidates, 0)
        self.assertEqual(report.run_cleanup_candidates, 0)
        self.assertIn("Overall status: OK", rendered)
        self.assertIn("[OK] source audit", rendered)

    def test_pending_backlink_checklist_reports_warn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_healthy_fixture(vault)
            source.write_text(
                source.read_text(encoding="utf-8")
                + "\n## Backlink Proposals\n\n- [ ] Add [[Targets/ledger|ledger]] (score 3): pending\n",
                encoding="utf-8",
            )

            report = build_vault_health(_settings(vault))

        self.assertEqual(report.status, WARN)
        self.assertEqual(report.pending_backlinks, 1)
        self.assertTrue(any(check.name == "backlink checklist" and check.status == WARN for check in report.checks))

    def test_cli_vault_health_write_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_healthy_fixture(vault)
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
                code = main(["--config", str(config), "vault-health", "--write-note"])

            notes = sorted((vault / "60_Runs").glob("*_vault-health.md"))
            note_text = notes[0].read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(len(notes), 1)
        self.assertIn('type: "vault-health"', note_text)
        self.assertIn("Vault health note written:", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_healthy_fixture(vault: Path) -> Path:
    source = vault / "10_Sources" / "official-docs" / "source.md"
    ledger = vault / "50_Evidence-Ledger" / "evidence.md"
    blueprint = vault / "30_Service-Blueprints" / "blueprint.md"
    topic_map = vault / "20_Taxonomy" / "topic-map.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    topic_map.parent.mkdir(parents=True, exist_ok=True)

    source.write_text(
        """---
type: source-note
topic: "Agentic RAG"
source_id: "S001"
source_type: "official-docs"
source_provider: "fixture"
source_url: "https://docs.example.com/exact"
canonical_url: "https://docs.example.com/exact"
source_score: 0.9
title: "Exact Docs"
checked_at: "2026-06-01"
confidence: medium
status: reviewed
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---
# Exact Docs

## Core Summary

**원본**

Official source.

**한국어 번역**

공식 출처입니다.

## Important Claims

- **원본:** E001 (medium, official-docs): Official source.
  - **한국어 번역:** E001 (중간, 공식 문서): 공식 출처입니다.

## Citable Evidence

- E001: Official source.

## Related Notes

- [[50_Evidence-Ledger/evidence|evidence]]
- [[30_Service-Blueprints/blueprint|blueprint]]
""",
        encoding="utf-8",
    )

    ledger.write_text(
        """---
type: evidence-ledger
topic: "Agentic RAG"
status: reviewed
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---
# Evidence Ledger: Agentic RAG

**한국어 번역**

근거 장부입니다.

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | Official source. | https://docs.example.com/exact | official-docs | 2026-06-01 | medium | official-docs: Official source. |

## Related Notes

- [[30_Service-Blueprints/blueprint|blueprint]]
""",
        encoding="utf-8",
    )

    blueprint.write_text(
        """---
type: service-blueprint
topic: "Agentic RAG"
status: reviewed
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---
# Agentic RAG Service Blueprint

## Evidence

**원본**

- [Exact Docs](https://docs.example.com/exact)

**한국어 번역**

- [Exact Docs](https://docs.example.com/exact)

## Related Notes

- [[50_Evidence-Ledger/evidence|evidence]]
""",
        encoding="utf-8",
    )

    topic_map.write_text(
        """---
type: topic-map
topic: "Agentic RAG"
status: reviewed
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---
# Topic Map: Agentic RAG

## Core Notes

- [[30_Service-Blueprints/blueprint|blueprint]]
- [[50_Evidence-Ledger/evidence|evidence]]

## Source Notes

- [[10_Sources/official-docs/source|source]]

## Claim Index

**원본**

- E001: Official source.

**한국어 번역**

- E001: 공식 출처입니다.
""",
        encoding="utf-8",
    )
    return source


if __name__ == "__main__":
    unittest.main()
