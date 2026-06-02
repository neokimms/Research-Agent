from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.cli import main
from research_agent.config import load_settings
from research_agent.source_audit import render_source_audit, run_source_audit, write_source_audit_note


class SourceAuditTests(unittest.TestCase):
    def test_passes_exact_official_doc_with_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_source(vault, _source_note(url="https://docs.example.com/agents/guide"))

            result = run_source_audit(vault)
            rendered = render_source_audit(result)

        self.assertTrue(result.passed)
        self.assertEqual(result.source_notes, 1)
        self.assertEqual(result.exact_official_docs, 1)
        self.assertEqual(result.warning_count, 0)
        self.assertIn("Status: PASS", rendered)
        self.assertIn("- None.", rendered)

    def test_warns_on_seed_official_doc_and_missing_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_source(
                vault,
                _source_note(
                    url="https://docs.example.com/",
                    claims="- No structured claims extracted yet.",
                    source_score="0.60",
                ),
            )

            result = run_source_audit(vault)

        self.assertTrue(result.passed)
        checks = {issue.check for issue in result.issues}
        self.assertIn("official_docs.exact_url", checks)
        self.assertIn("body.claims", checks)

    def test_warns_on_paper_missing_doi_and_arxiv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_source(
                vault,
                _source_note(
                    source_type="papers",
                    source_provider="crossref",
                    url="https://example.com/paper",
                    canonical_url="https://example.com/paper",
                    doi="",
                    arxiv_id="",
                    source_score="0.70",
                ),
            )

            result = run_source_audit(vault)

        self.assertTrue(result.passed)
        self.assertIn("paper.identity", {issue.check for issue in result.issues})

    def test_fails_missing_required_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_source(
                vault,
                """---
type: source-note
generated_by: research-agent
source_type:
source_url:
---
# Broken Source
""",
            )

            result = run_source_audit(vault)

        self.assertFalse(result.passed)
        checks = {issue.check for issue in result.issues}
        self.assertIn("frontmatter.source_id", checks)
        self.assertIn("frontmatter.source_type", checks)
        self.assertIn("frontmatter.source_url", checks)

    def test_target_paths_limit_source_audit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            clean = _write_source(vault, _source_note(url="https://docs.example.com/agents/guide"))
            _write_source(
                vault,
                """---
type: source-note
generated_by: research-agent
---
# Broken Source
""",
                name="broken.md",
            )

            result = run_source_audit(vault, target_paths=[clean])

        self.assertTrue(result.passed)
        self.assertEqual(result.source_notes, 1)
        self.assertEqual(result.failure_count, 0)

    def test_warns_on_stale_downstream_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_source(
                vault,
                _source_note(
                    url="https://docs.example.com/agents/guide",
                    claims="- E001 (medium, official-docs): Fresh exact docs claim.",
                ),
            )
            _write_evidence_ledger(vault)
            _write_service_blueprint(vault)

            result = run_source_audit(vault)
            rendered = render_source_audit(result)

        self.assertTrue(result.passed)
        self.assertEqual(result.evidence_ledgers, 1)
        self.assertEqual(result.service_blueprints, 1)
        self.assertEqual(result.stale_reference_count, 2)
        checks = {issue.check for issue in result.issues}
        self.assertIn("downstream.evidence-ledger", checks)
        self.assertIn("downstream.service-blueprint", checks)
        self.assertIn("Stale downstream references: 2", rendered)

    def test_cli_returns_failure_for_broken_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_source(
                vault,
                """---
type: source-note
generated_by: research-agent
---
# Broken Source
""",
            )
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
                code = main(["--config", str(config), "source-audit"])

        self.assertEqual(code, 1)
        self.assertIn("Status: FAIL", output.getvalue())

    def test_writes_source_audit_note_under_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_source(vault, _source_note(url="https://docs.example.com/"))
            config = root / "config.toml"
            config.write_text(
                f"""[obsidian]
vault_path = "{vault.as_posix()}"

[common_modules]
enabled = false
""",
                encoding="utf-8",
            )

            settings = load_settings(config)
            write_result = write_source_audit_note(
                settings,
                checked_at=datetime(2026, 5, 31, 9, 30, tzinfo=timezone.utc),
            )
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn("/60_Runs/", write_result.note_path.as_posix())
        self.assertIn('type: "source-audit"', text)
        self.assertIn('audit_status: "PASS"', text)
        self.assertIn("seed_official_docs: 1", text)
        self.assertIn("official-docs-refresh --write-note", text)

    def test_cli_writes_source_audit_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_source(vault, _source_note(url="https://docs.example.com/agents/guide"))
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
                code = main(["--config", str(config), "source-audit", "--write-note"])
            run_dir_exists = (vault / "60_Runs").exists()
            written_notes = list((vault / "60_Runs").glob("*_source-audit.md"))

        self.assertEqual(code, 0)
        self.assertTrue(run_dir_exists)
        self.assertEqual(len(written_notes), 1)
        self.assertIn("Audit note written:", output.getvalue())


def _write_source(vault: Path, text: str, *, name: str = "source.md") -> Path:
    path = vault / "10_Sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_evidence_ledger(vault: Path) -> Path:
    path = vault / "50_Evidence-Ledger" / "evidence.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
type: evidence-ledger
topic: Agentic RAG
generated_by: research-agent
---
# Evidence Ledger

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | Stale seed claim. | https://docs.example.com/ | official-docs | 2026-05-31 | low | official-docs: stale evidence |
""",
        encoding="utf-8",
    )
    return path


def _write_service_blueprint(vault: Path) -> Path:
    path = vault / "30_Service-Blueprints" / "blueprint.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
type: service-blueprint
topic: Agentic RAG
generated_by: research-agent
---
# Blueprint

## Evidence

- [Seed Docs](https://docs.example.com/) (official-docs)
""",
        encoding="utf-8",
    )
    return path


def _source_note(
    *,
    source_type: str = "official-docs",
    source_provider: str = "openai-web-search",
    source_id: str = "S001",
    url: str = "https://docs.example.com/agents/guide",
    canonical_url: str = "",
    doi: str = "",
    arxiv_id: str = "",
    source_score: str = "0.88",
    claims: str = "- **원본:** E001: Example claim.",
) -> str:
    canonical = canonical_url or url
    return f"""---
type: source-note
topic: Agentic RAG
source_id: "{source_id}"
source_type: "{source_type}"
source_provider: "{source_provider}"
source_url: "{url}"
canonical_url: "{canonical}"
doi: "{doi}"
arxiv_id: "{arxiv_id}"
source_score: "{source_score}"
checked_at: "2026-05-31"
status: draft
generated_by: research-agent
---
# Source

## Important Claims

{claims}
"""


if __name__ == "__main__":
    unittest.main()
