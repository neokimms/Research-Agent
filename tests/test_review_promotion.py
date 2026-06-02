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
from research_agent.review_promotion import (
    apply_review_promotion,
    build_review_promotion,
    render_review_promotion,
    render_review_promotion_apply_result,
    render_review_promotion_note,
    write_review_promotion_note,
)


class ReviewPromotionTests(unittest.TestCase):
    def test_builds_candidates_and_skips_stale_evidence_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = build_review_promotion(_settings(vault))
            rendered = render_review_promotion(result)

        self.assertEqual(result.notes_scanned, 4)
        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(len(result.skipped_items), 1)
        self.assertIn("Promotion candidates: 3", rendered)
        self.assertIn("stale paper verification text remains", rendered)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            write_result = write_review_promotion_note(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "review-promotion"', text)
        self.assertIn("- [ ] R001 Promote", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"note_type": "source-note"', text)

    def test_apply_checked_candidate_promotes_to_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_fixture(vault)
            proposal = _write_review_promotion_proposal(vault, checked=True)

            result = apply_review_promotion(
                _settings(vault),
                dry_run=False,
                reviewed_at="2026-06-01T10:00:00+09:00",
            )
            source_text = source.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_review_promotion_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)
        self.assertIn('status: "reviewed"', source_text)
        self.assertIn('reviewed_by: "research-agent"', source_text)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 1", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            source = _write_fixture(vault)
            _write_review_promotion_proposal(vault, checked=True)
            before = source.read_text(encoding="utf-8")

            result = apply_review_promotion(_settings(vault), dry_run=True)
            after = source.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)

    def test_cli_apply_review_promotion_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            _write_review_promotion_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-review-promotion", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Review Promotion Apply Dry Run", output.getvalue())
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


def _write_fixture(vault: Path) -> Path:
    source = vault / "10_Sources" / "official-docs" / "source.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
type: source-note
topic: "Agentic RAG"
source_id: "S001"
source_type: "official-docs"
source_provider: "openai-web-search"
source_url: "https://docs.example.com/exact"
canonical_url: "https://docs.example.com/exact"
source_score: 0.88
title: "Exact Docs"
checked_at: "2026-06-01"
confidence: medium
status: draft
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

- Source: https://docs.example.com/exact
- E001 원본: Official source.
  - E001 한국어 번역: 공식 출처입니다.
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
language: bilingual
original_language: en
translation_language: ko
---
# Topic Map: Agentic RAG

## Core Notes

- Blueprint: [[30_Service-Blueprints/blueprint|blueprint]]

## Source Notes

- [[10_Sources/official-docs/source|source]]

## Claim Index

- **원본:** E001: Official source.
  - **한국어 번역:** E001: 공식 출처입니다.

## Suggested Backlinks

**원본**

- Link reviewed source notes.

**한국어 번역**

- 검토된 출처 노트를 연결합니다.
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
language: bilingual
original_language: en
translation_language: ko
---
# Agentic RAG Service Blueprint

## One-Line Conclusion

**원본**

Use evidence.

**한국어 번역**

근거를 사용합니다.

## When To Use

- Evidence matters.

## Structure Classification

- Evidence-led workflow

## Recommended Baseline

```text
question -> evidence
```

## Implementation Order

1. Review evidence.

## Operational Risks

- Weak evidence.

## Verification

- Check evidence.

## Evidence

- [Exact Docs](https://docs.example.com/exact) (official-docs)

## Still Uncertain

- None.

## Related Notes
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
language: bilingual
original_language: en
translation_language: ko
---
# Evidence Ledger: Agentic RAG

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | Official source. | https://docs.example.com/exact | official-docs | 2026-06-01 | medium | official-docs: Official source. |
| E002 | Paper source. | https://doi.org/10.example/paper | papers | 2026-06-01 | medium | papers: Paper source. |

## Needs Verification

**원본**

- No paper sources were collected in this run; add paper metadata in a follow-up research pass if papers are required.

**한국어 번역**

- 이 실행에서는 논문 출처가 수집되지 않았으므로, 논문 근거가 필요하면 후속 research pass에서 논문 메타데이터를 추가합니다.
""",
        encoding="utf-8",
    )
    return source


def _write_review_promotion_proposal(vault: Path, *, checked: bool) -> Path:
    result = build_review_promotion(_settings(vault))
    path = vault / "60_Runs" / "2026-06-01_review-promotion.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    text = render_review_promotion_note(result, checked_at="2026-06-01T10:00:00+09:00").replace("- [ ] R001", f"- [{state}] R001")
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
