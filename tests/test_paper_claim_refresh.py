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
from research_agent.paper_claim_refresh import (
    apply_paper_claim_refresh,
    build_paper_claim_refresh,
    render_paper_claim_refresh,
    render_paper_claim_refresh_apply_result,
    write_paper_claim_refresh_note,
)


class PaperClaimRefreshTests(unittest.TestCase):
    def test_builds_candidate_for_generic_metadata_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_paper_source(vault)

            result = build_paper_claim_refresh(_settings(vault), fetch_metadata=False)
            rendered = render_paper_claim_refresh(result)

        self.assertEqual(result.paper_sources, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "C001")
        self.assertIn("Claim refresh candidates: 1", rendered)
        self.assertIn("Refresh E002 from S002", rendered)
        self.assertIn("2025 paper or book chapter", result.candidates[0].new_claim)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_paper_source(vault)

            write_result = write_paper_claim_refresh_note(_settings(vault), fetch_metadata=False)
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "paper-claim-refresh"', text)
        self.assertIn("- [ ] C001 Refresh E002 from S002", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"new_claim": "Example Paper is a 2025 paper or book chapter', text)

    def test_apply_checked_candidate_updates_source_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_paper_source(vault)
            proposal = _write_claim_refresh_proposal(vault, checked=True)

            result = apply_paper_claim_refresh(
                _settings(vault),
                dry_run=False,
                applied_at="2026-06-01T10:00:00+09:00",
            )
            text = source.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_paper_claim_refresh_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)
        self.assertIn('claim_refresh_provider: "local-paper-metadata"', text)
        self.assertIn("Example Paper is a 2025 paper or book chapter by Ada Lovelace", text)
        self.assertNotIn("**원본**\n\nCrossref metadata record.", text)
        self.assertIn("## Paper Claim Refresh History", text)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 1", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_paper_source(vault)
            _write_claim_refresh_proposal(vault, checked=True)
            before = source.read_text(encoding="utf-8")

            result = apply_paper_claim_refresh(_settings(vault), dry_run=True)
            after = source.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)

    def test_no_candidate_for_specific_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_paper_source(vault, claim="This paper evaluates agent orchestration tradeoffs.")

            result = build_paper_claim_refresh(_settings(vault), fetch_metadata=False)

        self.assertEqual(result.candidates, [])

    def test_cli_apply_claim_refresh_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_paper_source(vault)
            _write_claim_refresh_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-paper-claim-refresh", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Paper Claim Refresh Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 1", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_paper_source(vault: Path, *, claim: str = "Crossref metadata record.") -> Path:
    path = vault / "10_Sources" / "papers" / "paper.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
type: source-note
topic: "Agentic RAG"
source_id: "S002"
source_type: "papers"
source_provider: "crossref"
source_url: "https://doi.org/10.1145/example"
canonical_url: "https://doi.org/10.1145/example"
doi: "10.1145/example"
arxiv_id: ""
source_score: 0.95
title: "Example Paper"
authors:
  - "Ada Lovelace"
published_at: "2025"
updated_at: ""
checked_at: "2026-06-01"
confidence: medium
status: draft
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
---

# Example Paper

## Core Summary

**원본**

{claim}

**한국어 번역**

Crossref 메타데이터 record.

## Important Claims

- **원본:** E002 (medium, papers): {claim}
  - **한국어 번역:** E002 (중간, 논문): Crossref 메타데이터 record.

## Implementation Meaning

**원본**

- Supports `papers` decisions.

**한국어 번역**

- `논문` 관련 결정을 뒷받침합니다.

## Citable Evidence

- Source: https://doi.org/10.1145/example
- E002 원본: {claim}
  - E002 한국어 번역: Crossref 메타데이터 record.

## Citation Metadata

- Canonical URL: https://doi.org/10.1145/example
- DOI: 10.1145/example
- arXiv ID: Not captured.
- Provider: crossref
- Source Score: 0.95

## Limits And Cautions

**원본**

- This source note is agent-generated and needs review.

**한국어 번역**

- 이 출처 노트는 에이전트가 생성했으며 검토가 필요합니다.

## Related Notes
""",
        encoding="utf-8",
    )
    return path


def _write_claim_refresh_proposal(vault: Path, *, checked: bool) -> Path:
    path = vault / "60_Runs" / "2026-06-01_paper-claim-refresh.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    path.write_text(
        f"""---
type: "paper-claim-refresh"
status: "draft"
proposal_state: "proposed"
generated_by: "research-agent"
---
# Paper Claim Refresh

## Proposals

- [{state}] C001 Refresh E002 from S002 [Example Paper](https://doi.org/10.1145/example) (provider: local-paper-metadata)

## Candidate Records

```json
[
  {{
    "proposal_id": "C001",
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
      "claim": "Crossref metadata record.",
      "evidence": "Crossref metadata record.",
      "confidence": "medium",
      "category": "papers"
    }},
    "metadata": {{
      "doi": "10.1145/example",
      "arxiv_id": "",
      "authors": ["Ada Lovelace"],
      "published_at": "2025",
      "provider": "crossref"
    }},
    "new_summary": "Example Paper is a 2025 paper or book chapter by Ada Lovelace identified by DOI 10.1145/example that should be reviewed as paper evidence for `Agentic RAG`.",
    "new_claim": "Example Paper is a 2025 paper or book chapter by Ada Lovelace identified by DOI 10.1145/example that should be reviewed as paper evidence for `Agentic RAG`.",
    "new_evidence": "Example Paper is a 2025 paper or book chapter by Ada Lovelace identified by DOI 10.1145/example that should be reviewed as paper evidence for `Agentic RAG`.",
    "refresh_provider": "local-paper-metadata"
  }}
]
```
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
