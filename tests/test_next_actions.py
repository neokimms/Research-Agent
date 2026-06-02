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
from research_agent.low_priority_backlink_review import write_low_priority_backlink_review_note
from research_agent.manual_orphan_review import write_manual_orphan_review_note
from research_agent.next_actions import build_next_actions, render_next_actions, write_next_actions_note
from research_agent.run_cleanup import render_run_cleanup_note, build_run_cleanup


class NextActionsTests(unittest.TestCase):
    def test_summarizes_manual_orphan_and_low_priority_backlink_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            report = build_next_actions(_settings(vault), max_suggestions=10)
            rendered = render_next_actions(report)

        titles = [item.title for item in report.items]
        self.assertIn("Create manual orphan proposal note", titles)
        self.assertIn("Create low-priority backlink proposal note", titles)
        self.assertIn("manual-orphan-proposals --write-note", rendered)
        self.assertIn("low-priority-backlink-proposals --write-note", rendered)

    def test_recommends_reviewing_existing_proposal_notes_before_creating_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            settings = _settings(vault)
            _write_fixture(vault)
            write_manual_orphan_review_note(settings)
            write_low_priority_backlink_review_note(settings, max_suggestions=10)

            report = build_next_actions(settings, max_suggestions=10)
            rendered = render_next_actions(report)

        titles = [item.title for item in report.items]
        self.assertIn("Review existing manual orphan proposal note", titles)
        self.assertIn("Review existing low-priority backlink proposal note", titles)
        self.assertIn("apply-manual-orphan-review --dry-run", rendered)
        self.assertIn("apply-low-priority-backlinks --dry-run", rendered)
        self.assertNotIn("manual-orphan-proposals --write-note", rendered)
        self.assertNotIn("low-priority-backlink-proposals --write-note", rendered)

    def test_write_next_actions_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = write_next_actions_note(_settings(vault), max_suggestions=10)
            text = result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "next-actions"', text)
        self.assertIn("## Actions", text)
        self.assertIn("action_count:", text)

    def test_recommends_rerun_lineage_backlink_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "20_Taxonomy").mkdir()
            (vault / "60_Runs").mkdir()
            (vault / "20_Taxonomy" / "agent-topic-map.md").write_text(
                """---
type: topic-map
topic: Agentic RAG
status: draft
generated_by: research-agent
rerun_of: failed-source
---
# Topic Map
""",
                encoding="utf-8",
            )
            (vault / "60_Runs" / "agent-run.md").write_text(
                """---
type: run-log
topic: Agentic RAG
status: draft
generated_by: research-agent
rerun_of: failed-source
---
# Run Log
""",
                encoding="utf-8",
            )

            report = build_next_actions(_settings(vault), max_suggestions=10)
            rendered = render_next_actions(report)

        self.assertIn("Create actionable backlink proposals", [item.title for item in report.items])
        self.assertIn("20_Taxonomy/agent-topic-map.md -> 60_Runs/agent-run.md", rendered)

    def test_recommends_reviewing_existing_run_cleanup_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            settings = _settings(vault)
            run_dir = vault / "60_Runs"
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_snapshot(run_dir / "2026-06-01_next-actions.md", "next-actions", "2026-06-01T10:00:00+09:00")
            _write_snapshot(run_dir / "2026-06-01_next-actions-2.md", "next-actions", "2026-06-01T11:00:00+09:00")
            proposal = run_dir / "2026-06-01_run-cleanup.md"
            proposal.write_text(
                render_run_cleanup_note(build_run_cleanup(settings), checked_at="2026-06-01T12:00:00+09:00"),
                encoding="utf-8",
            )

            report = build_next_actions(settings)
            rendered = render_next_actions(report)

        self.assertIn("Review existing run cleanup proposal note", [item.title for item in report.items])
        self.assertIn("apply-run-cleanup --dry-run", rendered)
        self.assertNotIn("run-cleanup-proposals --write-note", rendered)


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
    manual = vault / "PM" / "active.md"
    manual.parent.mkdir(parents=True, exist_ok=True)
    manual.write_text(
        """---
status: active
---
# Active PM Artifact
""",
        encoding="utf-8",
    )

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


def _write_snapshot(path: Path, note_type: str, checked_at: str) -> None:
    path.write_text(
        f"""---
type: {note_type}
status: draft
generated_by: research-agent
checked_at: "{checked_at}"
---
# {note_type}
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
