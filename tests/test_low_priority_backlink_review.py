from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import (
    AppSettings,
    CommonModuleSettings,
    ObsidianSettings,
    OpenAISettings,
    QualityGateSettings,
    Settings,
    SourceSettings,
)
from research_agent.low_priority_backlink_review import (
    apply_low_priority_backlinks,
    build_low_priority_backlink_review,
    render_low_priority_backlink_apply_result,
    render_low_priority_backlink_review,
    write_low_priority_backlink_review_note,
)
from research_agent.vault_index import build_vault_index


class LowPriorityBacklinkReviewTests(unittest.TestCase):
    def test_builds_low_priority_signal_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_signal_fixture(vault)

            result = build_low_priority_backlink_review(_settings(vault), max_suggestions=10)
            rendered = render_low_priority_backlink_review(result)

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "L001")
        self.assertEqual(result.candidates[0].score, 2)
        self.assertIn("Low-priority signals: 1", rendered)
        self.assertIn("L001 Ignore", rendered)

    def test_write_note_includes_candidate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_signal_fixture(vault)

            result = write_low_priority_backlink_review_note(_settings(vault), max_suggestions=10)
            text = result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "low-priority-backlink-review"', text)
        self.assertIn('proposal_state: "proposed"', text)
        self.assertIn("## Ignore Checklist", text)
        self.assertIn('"proposal_id": "L001"', text)

    def test_apply_checked_ignore_suppresses_future_index_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_signal_fixture(vault)
            proposal = write_low_priority_backlink_review_note(_settings(vault), max_suggestions=10).note_path
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace("- [ ] L001 Ignore", "- [x] L001 Ignore"),
                encoding="utf-8",
            )

            result = apply_low_priority_backlinks(
                _settings(vault),
                dry_run=False,
                applied_at="2026-06-01T12:00:00+09:00",
            )
            proposal_text = proposal.read_text(encoding="utf-8")
            index = build_vault_index(vault, max_suggestions=10)
            rendered = render_low_priority_backlink_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual([path.resolve() for path in result.updated_paths], [proposal.resolve()])
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertEqual(index.suggestions, [])
        self.assertIn("Ignored signals: 1", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_signal_fixture(vault)
            proposal = write_low_priority_backlink_review_note(_settings(vault), max_suggestions=10).note_path
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace("- [ ] L001 Ignore", "- [x] L001 Ignore"),
                encoding="utf-8",
            )
            before = proposal.read_text(encoding="utf-8")

            result = apply_low_priority_backlinks(_settings(vault), dry_run=True)
            after = proposal.read_text(encoding="utf-8")
            index = build_vault_index(vault, max_suggestions=10)

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(index.suggestions), 1)


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_signal_fixture(vault: Path) -> None:
    run_dir = vault / "60_Runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "official-refresh.md").write_text(
        """---
type: official-docs-refresh
status: draft
generated_by: research-agent
---
# Manual Refresh Review
""",
        encoding="utf-8",
    )
    (run_dir / "standards-refresh.md").write_text(
        """---
type: standards-refresh
status: draft
generated_by: research-agent
---
# Standards Manual Review
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
