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
from research_agent.paper_downstream import (
    apply_paper_downstream_proposals,
    build_paper_downstream_proposals,
    render_paper_downstream_apply_result,
    render_paper_downstream_proposals,
    write_paper_downstream_proposals,
)


class PaperDownstreamTests(unittest.TestCase):
    def test_builds_candidates_for_paper_claim_missing_downstream_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = build_paper_downstream_proposals(_settings(vault))
            rendered = render_paper_downstream_proposals(result)

        self.assertEqual(result.paper_sources, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "D001")
        self.assertEqual(result.candidates[0].pending_targets, ["evidence-ledger", "service-blueprint", "topic-map"])
        self.assertIn("Downstream candidates: 1", rendered)
        self.assertIn("Add E002 from S002", rendered)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            write_result = write_paper_downstream_proposals(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "paper-downstream-proposals"', text)
        self.assertIn("- [ ] D001 Add E002 from S002", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"source_id": "S002"', text)
        self.assertIn('"evidence_ledger": "50_Evidence-Ledger/evidence.md"', text)

    def test_apply_checked_candidate_updates_ledger_blueprint_and_topic_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            proposal = _write_downstream_proposal(vault, checked=True)

            result = apply_paper_downstream_proposals(
                _settings(vault),
                dry_run=False,
                applied_at="2026-06-01T10:00:00+09:00",
            )
            ledger = (vault / "50_Evidence-Ledger" / "evidence.md").read_text(encoding="utf-8")
            blueprint = (vault / "30_Service-Blueprints" / "blueprint.md").read_text(encoding="utf-8")
            topic_map = (vault / "20_Taxonomy" / "topic-map.md").read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_paper_downstream_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 3)
        self.assertIn("| E002 | Paper claim. | https://doi.org/10.1145/example | papers | 2026-06-01 | medium | papers: Paper evidence. |", ledger)
        self.assertIn("- E002: [Example Paper](https://doi.org/10.1145/example) (papers; [[10_Sources/papers/paper|paper]])", blueprint)
        self.assertIn("- [[10_Sources/papers/paper|paper]]", topic_map)
        self.assertIn("- E002: Paper evidence.", topic_map)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 3", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            _write_downstream_proposal(vault, checked=True)
            ledger = vault / "50_Evidence-Ledger" / "evidence.md"
            before = ledger.read_text(encoding="utf-8")

            result = apply_paper_downstream_proposals(_settings(vault), dry_run=True)
            after = ledger.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 3)

    def test_no_candidates_when_downstream_notes_already_reference_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            _write_downstream_proposal(vault, checked=True)
            apply_paper_downstream_proposals(_settings(vault), dry_run=False)

            result = build_paper_downstream_proposals(_settings(vault))

        self.assertEqual(result.candidates, [])

    def test_cli_apply_downstream_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            _write_downstream_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-paper-downstream", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Paper Downstream Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 3", output.getvalue())


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
    source = vault / "10_Sources" / "papers" / "paper.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
type: source-note
topic: "Agentic RAG"
source_id: "S002"
source_type: "papers"
source_url: "https://doi.org/10.1145/example"
canonical_url: "https://doi.org/10.1145/example"
title: "Example Paper"
checked_at: "2026-06-01"
status: draft
generated_by: research-agent
---
# Example Paper

## Important Claims

- E002 (medium, papers): Paper claim.

## Citable Evidence

- Source: https://doi.org/10.1145/example
- E002: Paper evidence.
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
| E001 | Existing claim. | https://docs.example.com/ | official-docs | 2026-06-01 | medium | official-docs: Existing evidence. |
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

- [Docs](https://docs.example.com/) (official-docs)

## Still Uncertain

- Papers need review.
""",
        encoding="utf-8",
    )

    topic_map = vault / "20_Taxonomy" / "topic-map.md"
    topic_map.parent.mkdir(parents=True, exist_ok=True)
    topic_map.write_text(
        """---
type: topic-map
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Topic Map

## Source Notes

- [[10_Sources/official-docs/source|source]]

## Claim Index

- E001: Existing evidence. ([[50_Evidence-Ledger/evidence|evidence]])

## Suggested Backlinks

- Link reviewed source notes.
""",
        encoding="utf-8",
    )


def _write_downstream_proposal(vault: Path, *, checked: bool) -> Path:
    path = vault / "60_Runs" / "2026-06-01_paper-downstream-proposals.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    path.write_text(
        f"""---
type: "paper-downstream-proposals"
status: "draft"
proposal_state: "proposed"
generated_by: "research-agent"
---
# Paper Downstream Proposals

## Proposals

- [{state}] D001 Add E002 from S002 [Example Paper](https://doi.org/10.1145/example) to evidence-ledger, service-blueprint, topic-map

## Candidate Records

```json
[
  {{
    "proposal_id": "D001",
    "pending_targets": ["evidence-ledger", "service-blueprint", "topic-map"],
    "source": {{
      "path": "10_Sources/papers/paper.md",
      "topic": "Agentic RAG",
      "source_id": "S002",
      "source_type": "papers",
      "title": "Example Paper",
      "source_url": "https://doi.org/10.1145/example",
      "canonical_url": "https://doi.org/10.1145/example",
      "checked_at": "2026-06-01"
    }},
    "claim": {{
      "claim_id": "E002",
      "claim": "Paper claim.",
      "evidence": "Paper evidence.",
      "confidence": "medium",
      "category": "papers"
    }},
    "targets": {{
      "evidence_ledger": "50_Evidence-Ledger/evidence.md",
      "service_blueprint": "30_Service-Blueprints/blueprint.md",
      "topic_map": "20_Taxonomy/topic-map.md"
    }}
  }}
]
```
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
