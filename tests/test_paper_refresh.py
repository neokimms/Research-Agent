from __future__ import annotations

import contextlib
import io
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
from research_agent.paper_refresh import (
    apply_paper_refresh,
    build_paper_refresh,
    render_paper_refresh,
    render_paper_refresh_apply_result,
    write_paper_refresh_note,
)


class PaperRefreshTests(unittest.TestCase):
    def test_builds_paper_candidates_for_explicit_topic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)

            with patch("research_agent.paper_refresh.collect_paper_sources", return_value=[_paper_record()]):
                result = build_paper_refresh(_settings(vault), topic="Agentic RAG")
                rendered = render_paper_refresh(result)

        self.assertEqual(result.topics, ["Agentic RAG"])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "P001")
        self.assertIn("Paper candidates: 1", rendered)
        self.assertIn("doi: `10.1145/example`", rendered)

    def test_builds_topics_from_generated_vault_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_generated_topic_note(vault)

            with patch("research_agent.paper_refresh.collect_paper_sources", return_value=[_paper_record()]):
                result = build_paper_refresh(_settings(vault))

        self.assertEqual(result.topics, ["Agentic RAG"])
        self.assertEqual(len(result.candidates), 1)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)

            with patch("research_agent.paper_refresh.collect_paper_sources", return_value=[_paper_record()]):
                write_result = write_paper_refresh_note(_settings(vault), topic="Agentic RAG")
                text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "paper-refresh"', text)
        self.assertIn("- [ ] P001 Add paper", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"doi": "10.1145/example"', text)

    def test_apply_checked_candidate_creates_paper_source_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_existing_source(vault)
            proposal = _write_paper_proposal(vault, checked=True)

            dry_run = apply_paper_refresh(_settings(vault), dry_run=True, applied_at="2026-06-01T10:00:00+09:00")
            result = apply_paper_refresh(_settings(vault), dry_run=False, applied_at="2026-06-01T10:00:00+09:00")
            proposal_text = proposal.read_text(encoding="utf-8")
            created = result.created_paths[0].read_text(encoding="utf-8")
            rendered = render_paper_refresh_apply_result(result)

        self.assertEqual(len(dry_run.approved_items), 1)
        self.assertEqual(len(result.created_paths), 1)
        self.assertIn('source_type: "papers"', created)
        self.assertIn('source_provider: "crossref"', created)
        self.assertIn('doi: "10.1145/example"', created)
        self.assertIn('source_id: "S002"', created)
        self.assertIn("E002", created)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Created notes: 1", rendered)

    def test_apply_skips_duplicate_paper_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_existing_paper(vault)
            _write_paper_proposal(vault, checked=True)

            result = apply_paper_refresh(_settings(vault), dry_run=False)

        self.assertEqual(result.created_paths, [])
        self.assertEqual(len(result.skipped_items), 1)
        self.assertEqual(result.skipped_items[0].reason, "paper source already exists")

    def test_cli_apply_refresh_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_existing_source(vault)
            _write_paper_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-paper-refresh", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Paper Refresh Apply Dry Run", output.getvalue())
        self.assertIn("Would create notes: 1", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(paper_sources=["crossref"]),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _paper_record() -> SourceRecord:
    return SourceRecord(
        title="Example Paper",
        url="https://doi.org/10.1145/example",
        canonical_url="https://doi.org/10.1145/example",
        source_type="papers",
        summary="A relevant paper for Agentic RAG.",
        authors=["Ada Lovelace"],
        published_at="2025",
        doi="10.1145/example",
        source_provider="crossref",
        source_score=0.95,
    )


def _write_generated_topic_note(vault: Path) -> Path:
    path = vault / "30_Service-Blueprints" / "blueprint.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
type: service-blueprint
topic: "Agentic RAG"
generated_by: research-agent
---
# Blueprint
""",
        encoding="utf-8",
    )
    return path


def _write_existing_source(vault: Path) -> Path:
    path = vault / "10_Sources" / "official-docs" / "source.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
type: source-note
source_id: "S001"
source_type: "official-docs"
source_url: "https://docs.example.com/"
generated_by: research-agent
---
# Source
""",
        encoding="utf-8",
    )
    return path


def _write_existing_paper(vault: Path) -> Path:
    path = vault / "10_Sources" / "papers" / "paper.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
type: source-note
source_id: "S002"
source_type: "papers"
source_url: "https://doi.org/10.1145/example"
canonical_url: "https://doi.org/10.1145/example"
doi: "10.1145/example"
generated_by: research-agent
---
# Paper
""",
        encoding="utf-8",
    )
    return path


def _write_paper_proposal(vault: Path, *, checked: bool) -> Path:
    path = vault / "60_Runs" / "2026-06-01_paper-refresh.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    path.write_text(
        f"""---
type: "paper-refresh"
status: "draft"
proposal_state: "proposed"
generated_by: "research-agent"
---
# Paper Refresh

## Proposals

- [{state}] P001 Add paper [Example Paper](https://doi.org/10.1145/example) (provider: crossref, score: 0.95, doi: `10.1145/example`)

## Candidate Records

```json
[
  {{
    "proposal_id": "P001",
    "topic": "Agentic RAG",
    "title": "Example Paper",
    "url": "https://doi.org/10.1145/example",
    "canonical_url": "https://doi.org/10.1145/example",
    "source_type": "papers",
    "summary": "A relevant paper for Agentic RAG.",
    "authors": ["Ada Lovelace"],
    "published_at": "2025",
    "updated_at": "",
    "doi": "10.1145/example",
    "arxiv_id": "",
    "source_provider": "crossref",
    "source_score": 0.95
  }}
]
```
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
