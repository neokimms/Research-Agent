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
from research_agent.manual_orphan_review import (
    apply_manual_orphan_review,
    build_manual_orphan_review,
    render_manual_orphan_apply_result,
    render_manual_orphan_review,
    write_manual_orphan_review_note,
)
from research_agent.vault_index import build_vault_index


class ManualOrphanReviewTests(unittest.TestCase):
    def test_builds_manual_orphan_candidates_from_status_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_manual_note(vault / "PM" / "active.md", status="active", title="Active PM Artifact")
            (vault / "Reference").mkdir()
            (vault / "Reference" / "standalone.md").write_text("# Standalone\n", encoding="utf-8")

            result = build_manual_orphan_review(_settings(vault))
            rendered = render_manual_orphan_review(result)

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].proposal_id, "M001")
        self.assertEqual(result.candidates[0].title, "Active PM Artifact")
        self.assertIn("Manual orphan candidates: 1", rendered)
        self.assertIn("M001 Ignore", rendered)
        self.assertIn("M001 Archive", rendered)
        self.assertIn("M001 Link to [[TARGET_NOTE]]", rendered)

    def test_write_proposal_note_includes_candidate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_manual_note(vault / "PM" / "active.md", status="active", title="Active PM Artifact")

            result = write_manual_orphan_review_note(_settings(vault))
            text = result.note_path.read_text(encoding="utf-8")
            index = build_vault_index(vault)

        self.assertIn('type: "manual-orphan-review"', text)
        self.assertIn('proposal_state: "proposed"', text)
        self.assertIn("## Action Checklist", text)
        self.assertIn('"proposal_id": "M001"', text)
        self.assertEqual(len(index.orphan_manual_review_notes), 1)
        self.assertEqual(index.suggestions, [])

    def test_apply_ignore_marks_orphan_review_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_manual_note(vault / "PM" / "active.md", status="active", title="Active PM Artifact")
            proposal = write_manual_orphan_review_note(_settings(vault)).note_path
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace("- [ ] M001 Ignore", "- [x] M001 Ignore"),
                encoding="utf-8",
            )

            result = apply_manual_orphan_review(
                _settings(vault),
                dry_run=False,
                reviewed_at="2026-06-01T12:00:00+09:00",
            )
            text = note.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            index = build_vault_index(vault)

        self.assertEqual(len(result.approved_items), 1)
        self.assertIn('orphan_review: "ignored"', text)
        self.assertIn('orphan_reviewed_by: "research-agent"', text)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertEqual(index.orphan_manual_review_notes, [])

    def test_apply_link_adds_related_note_and_marks_linked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_manual_note(vault / "PM" / "active.md", status="active", title="Active PM Artifact")
            _write_manual_note(vault / "Projects" / "home.md", status="active", title="Project Home")
            proposal = write_manual_orphan_review_note(_settings(vault), max_proposals=1).note_path
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace(
                    "- [ ] M001 Link to [[TARGET_NOTE]]",
                    "- [x] M001 Link to [[Projects/home]]",
                ),
                encoding="utf-8",
            )

            result = apply_manual_orphan_review(
                _settings(vault),
                dry_run=False,
                reviewed_at="2026-06-01T12:00:00+09:00",
            )
            text = note.read_text(encoding="utf-8")
            rendered = render_manual_orphan_apply_result(result)

        self.assertEqual(len(result.approved_items), 1)
        self.assertIn('orphan_review: "linked"', text)
        self.assertIn("## Related Notes", text)
        self.assertIn("- [[Projects/home|home]]", text)
        self.assertIn("Updated notes: 1", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_manual_note(vault / "PM" / "active.md", status="active", title="Active PM Artifact")
            proposal = write_manual_orphan_review_note(_settings(vault)).note_path
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace("- [ ] M001 Archive", "- [x] M001 Archive"),
                encoding="utf-8",
            )
            before = note.read_text(encoding="utf-8")

            result = apply_manual_orphan_review(_settings(vault), dry_run=True)
            after = note.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 1)
        self.assertEqual(len(result.updated_paths), 1)


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_manual_note(path: Path, *, status: str, title: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
status: {status}
---
# {title}
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
