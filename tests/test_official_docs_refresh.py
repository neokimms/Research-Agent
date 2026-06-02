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
from research_agent.official_docs_refresh import (
    apply_official_docs_refresh,
    build_official_docs_refresh,
    render_official_docs_refresh,
    render_official_docs_refresh_apply_result,
    write_official_docs_refresh_note,
)


class OfficialDocsRefreshTests(unittest.TestCase):
    def test_no_api_key_reports_seed_notes_without_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {}, clear=True):
                result = build_official_docs_refresh(_settings(vault))
                rendered = render_official_docs_refresh(result)

        self.assertFalse(result.provider_available)
        self.assertEqual(len(result.seed_notes), 1)
        self.assertEqual(result.proposals, [])
        self.assertIn("exact official docs URL collection was skipped", rendered)

    def test_builds_exact_url_proposal_from_collector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                with patch(
                    "research_agent.official_docs_refresh.collect_official_doc_sources",
                    return_value=[
                        SourceRecord(
                            title="Agents SDK Guide",
                            url="https://developers.openai.com/api/docs/guides/agents",
                            source_type="official-docs",
                            summary="Official guide.",
                            source_provider="openai-web-search",
                            source_score=0.88,
                        )
                    ],
                ):
                    result = build_official_docs_refresh(_settings(vault))

        self.assertTrue(result.provider_available)
        self.assertEqual(len(result.proposals), 1)
        self.assertEqual(result.proposals[0].seed.relative_path, "10_Sources/seed.md")
        self.assertEqual(result.proposals[0].candidate.url, "https://developers.openai.com/api/docs/guides/agents")

    def test_write_proposal_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_seed_source(vault)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-123456"}, clear=True):
                with patch(
                    "research_agent.official_docs_refresh.collect_official_doc_sources",
                    return_value=[
                        SourceRecord(
                            title="Agents SDK Guide",
                            url="https://developers.openai.com/api/docs/guides/agents",
                            source_type="official-docs",
                            summary="Official guide.",
                            source_provider="openai-web-search",
                            source_score=0.88,
                        )
                    ],
                ):
                    write_result = write_official_docs_refresh_note(_settings(vault))
                    text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "official-docs-refresh"', text)
        self.assertIn('proposal_state: "proposed"', text)
        self.assertIn("Replace [[10_Sources/seed|seed]]", text)
        self.assertIn("https://developers.openai.com/api/docs/guides/agents", text)

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
official_doc_domains = ["developers.openai.com"]
""",
                encoding="utf-8",
            )

            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["--config", str(config), "official-docs-refresh"])

        self.assertEqual(code, 0)
        self.assertIn("Official Docs Refresh", output.getvalue())
        self.assertIn("Seed official docs notes: 1", output.getvalue())

    def test_apply_checked_proposal_updates_source_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_seed_source(vault, with_body=True)
            proposal = _write_refresh_proposal(vault, checked=True)

            dry_run = apply_official_docs_refresh(vault, dry_run=True, applied_at="2026-05-31T00:00:00+09:00")
            before = source.read_text(encoding="utf-8")
            result = apply_official_docs_refresh(vault, dry_run=False, applied_at="2026-05-31T00:00:00+09:00")
            after = source.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_official_docs_refresh_apply_result(result)

        self.assertEqual(len(dry_run.approved_items), 1)
        self.assertIn('source_url: "https://developers.openai.com/api/docs/guides/agents"', after)
        self.assertIn('canonical_url: "https://developers.openai.com/api/docs/guides/agents"', after)
        self.assertIn('source_provider: "openai-web-search"', after)
        self.assertIn('source_score: "0.88"', after)
        self.assertIn("- Source: https://developers.openai.com/api/docs/guides/agents", after)
        self.assertNotIn("This may be a seed domain", after)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 1", rendered)
        self.assertNotEqual(after, before)

    def test_apply_ignores_unchecked_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_seed_source(vault)
            _write_refresh_proposal(vault, checked=False)

            result = apply_official_docs_refresh(vault, dry_run=False)
            after = source.read_text(encoding="utf-8")

        self.assertEqual(result.approved_items, [])
        self.assertIn('source_url: "https://developers.openai.com/"', after)

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
                code = main(["--config", str(config), "apply-official-docs-refresh", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Official Docs Refresh Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 1", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(official_doc_domains=["developers.openai.com"]),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_seed_source(vault: Path, *, with_body: bool = False) -> Path:
    path = vault / "10_Sources" / "seed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = """# Seed

## Citable Evidence

- Source: https://developers.openai.com/

## Citation Metadata

- Canonical URL: https://developers.openai.com/
- Provider: seed
- Source Score: 0.60

## Limits And Cautions

- This may be a seed domain rather than an exact documentation page.
""" if with_body else "# Seed\n"
    path.write_text(
        """---
type: source-note
topic: "OpenAI Agents SDK"
source_id: "S001"
source_type: "official-docs"
source_provider: "seed"
source_url: "https://developers.openai.com/"
canonical_url: "https://developers.openai.com/"
title: "Official documentation candidate for OpenAI Agents SDK"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---
""" + body,
        encoding="utf-8",
    )
    return path


def _write_refresh_proposal(vault: Path, *, checked: bool) -> Path:
    path = vault / "60_Runs" / "2026-05-31_official-docs-refresh.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    path.write_text(
        f"""---
type: "official-docs-refresh"
status: "draft"
proposal_state: "proposed"
generated_by: "research-agent"
checked_at: "2026-05-31T00:00:00+09:00"
---
# Official Docs Refresh

## Proposals

- [{state}] Replace [[10_Sources/seed|seed]] seed URL `https://developers.openai.com/` with [Agents SDK Guide](https://developers.openai.com/api/docs/guides/agents) (provider: openai-web-search, score: 0.88)
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
