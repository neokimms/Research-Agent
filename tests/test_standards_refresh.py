from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from research_agent.models import SourceRecord
from research_agent.standards_refresh import (
    apply_standards_refresh,
    build_standards_refresh,
    render_standards_refresh,
    render_standards_refresh_apply_result,
    write_standards_refresh_note,
)


class StandardsRefreshTests(unittest.TestCase):
    def test_no_api_key_reports_seed_notes_without_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {}, clear=True):
                result = build_standards_refresh(_settings(vault))
                rendered = render_standards_refresh(result)

        self.assertFalse(result.provider_available)
        self.assertEqual(len(result.seed_notes), 1)
        self.assertEqual(result.proposals, [])
        self.assertIn("exact standards URL collection was skipped", rendered)

    def test_builds_exact_url_proposal_from_collector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                with patch(
                    "research_agent.standards_refresh.collect_standard_sources",
                    return_value=[
                        SourceRecord(
                            title="NIST AI RMF 1.0",
                            url="https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10",
                            source_type="standards",
                            summary="Official NIST publication.",
                            source_provider="openai-web-search",
                            source_score=0.91,
                        )
                    ],
                ):
                    result = build_standards_refresh(_settings(vault))

        self.assertTrue(result.provider_available)
        self.assertEqual(len(result.proposals), 1)
        self.assertEqual(result.proposals[0].seed.relative_path, "10_Sources/standard.md")
        self.assertEqual(
            result.proposals[0].candidate.url,
            "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10",
        )

    def test_write_proposal_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                with patch(
                    "research_agent.standards_refresh.collect_standard_sources",
                    return_value=[
                        SourceRecord(
                            title="NIST AI RMF 1.0",
                            url="https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10",
                            source_type="standards",
                            summary="Official NIST publication.",
                            source_provider="openai-web-search",
                            source_score=0.91,
                        )
                    ],
                ):
                    write_result = write_standards_refresh_note(_settings(vault))
                    text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "standards-refresh"', text)
        self.assertIn('proposal_state: "proposed"', text)
        self.assertIn("Replace [[10_Sources/standard|standard]]", text)
        self.assertIn("https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10", text)

    def test_cli_prints_refresh_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_seed_source(vault)
            config = root / "config.toml"
            config.write_text(
                f"""[obsidian]
vault_path = "{vault.as_posix()}"

[common_modules]
enabled = false

[sources]
standards_domains = ["nist.gov"]
""",
                encoding="utf-8",
            )

            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["--config", str(config), "--env-file", str(root / "missing.env"), "standards-refresh"])

        self.assertEqual(code, 0)
        self.assertIn("Standards Refresh", output.getvalue())
        self.assertIn("Seed standards notes: 1", output.getvalue())

    def test_apply_checked_proposal_updates_source_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_seed_source(vault, with_body=True)
            proposal = _write_refresh_proposal(vault, checked=True)

            dry_run = apply_standards_refresh(vault, dry_run=True, applied_at="2026-06-01T00:00:00+09:00")
            before = source.read_text(encoding="utf-8")
            result = apply_standards_refresh(vault, dry_run=False, applied_at="2026-06-01T00:00:00+09:00")
            after = source.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_standards_refresh_apply_result(result)

        self.assertEqual(len(dry_run.approved_items), 1)
        self.assertIn('source_url: "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10"', after)
        self.assertIn('canonical_url: "https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10"', after)
        self.assertIn('source_provider: "openai-web-search"', after)
        self.assertIn('source_score: "0.91"', after)
        self.assertIn("- Source: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10", after)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 1", rendered)
        self.assertNotEqual(after, before)

    def test_apply_ignores_unchecked_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_seed_source(vault)
            _write_refresh_proposal(vault, checked=False)

            result = apply_standards_refresh(vault, dry_run=False)
            after = source.read_text(encoding="utf-8")

        self.assertEqual(result.approved_items, [])
        self.assertIn('source_url: "https://nist.gov/"', after)

    def test_cli_apply_refresh_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_seed_source(vault)
            _write_refresh_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-standards-refresh", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Standards Refresh Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 1", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(standards_domains=["nist.gov"]),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_seed_source(vault: Path, *, with_body: bool = False) -> Path:
    path = vault / "10_Sources" / "standard.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = """# Standard

## Citable Evidence

- Source: https://nist.gov/

## Citation Metadata

- Canonical URL: https://nist.gov/
- Provider: seed
- Source Score: 0.60
""" if with_body else "# Standard\n"
    path.write_text(
        """---
type: source-note
topic: "Agent Governance"
source_id: "S005"
source_type: "standards"
source_provider: "seed"
source_url: "https://nist.gov/"
canonical_url: "https://nist.gov/"
title: "Standards candidate for Agent Governance"
checked_at: "2026-06-01"
status: draft
generated_by: research-agent
---
""" + body,
        encoding="utf-8",
    )
    return path


def _write_refresh_proposal(vault: Path, *, checked: bool) -> Path:
    path = vault / "60_Runs" / "2026-06-01_standards-refresh.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    path.write_text(
        f"""---
type: "standards-refresh"
status: "draft"
proposal_state: "proposed"
generated_by: "research-agent"
checked_at: "2026-06-01T00:00:00+09:00"
---
# Standards Refresh

## Proposals

- [{state}] Replace [[10_Sources/standard|standard]] seed URL `https://nist.gov/` with [NIST AI RMF 1.0](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) (provider: openai-web-search, score: 0.91)
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
