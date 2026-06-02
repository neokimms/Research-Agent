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
from research_agent.run_cleanup import (
    apply_run_cleanup,
    build_run_cleanup,
    build_run_cleanup_queue,
    render_run_cleanup,
    render_run_cleanup_apply_result,
    render_run_cleanup_note,
    write_run_cleanup_note,
)


class RunCleanupTests(unittest.TestCase):
    def test_builds_archive_candidates_for_completed_proposals_and_old_clean_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = build_run_cleanup(_settings(vault))
            rendered = render_run_cleanup(result)

        self.assertEqual(result.run_notes_scanned, 5)
        self.assertEqual(len(result.candidates), 3)
        reasons = [candidate.reason for candidate in result.candidates]
        self.assertIn("completed proposal lifecycle: applied", reasons)
        self.assertTrue(any(reason.startswith("superseded by newer clean source-audit") for reason in reasons))
        self.assertTrue(any(reason.startswith("superseded by newer clean bilingual-audit") for reason in reasons))
        self.assertIn("Archive candidates: 3", rendered)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            write_result = write_run_cleanup_note(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "run-cleanup"', text)
        self.assertIn("- [ ] A001 Archive", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"note_type": "blueprint-refresh"', text)

    def test_apply_checked_candidates_marks_archived(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            proposal_source = _write_fixture(vault)
            cleanup_note = _write_run_cleanup_proposal(vault, checked=True)

            result = apply_run_cleanup(
                _settings(vault),
                dry_run=False,
                archived_at="2026-06-01T10:00:00+09:00",
            )
            source_text = proposal_source.read_text(encoding="utf-8")
            cleanup_text = cleanup_note.read_text(encoding="utf-8")
            rendered = render_run_cleanup_apply_result(result)

        self.assertEqual(len(result.approved_items), 3)
        self.assertEqual(len(result.updated_paths), 3)
        self.assertIn('status: "archived"', source_text)
        self.assertIn('archived_by: "research-agent"', source_text)
        self.assertIn('archive_reason: "completed proposal lifecycle: applied"', source_text)
        self.assertIn('proposal_state: "applied"', cleanup_text)
        self.assertIn("Archived notes: 3", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            proposal_source = _write_fixture(vault)
            _write_run_cleanup_proposal(vault, checked=True)
            before = proposal_source.read_text(encoding="utf-8")

            result = apply_run_cleanup(_settings(vault), dry_run=True)
            after = proposal_source.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 3)
        self.assertEqual(len(result.updated_paths), 3)

    def test_cli_apply_run_cleanup_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            _write_run_cleanup_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-run-cleanup", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Run Cleanup Apply Dry Run", output.getvalue())
        self.assertIn("Would archive notes: 3", output.getvalue())

    def test_does_not_propose_run_cleanup_note_itself_after_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            _write_run_cleanup_proposal(vault, checked=True)
            apply_run_cleanup(_settings(vault), dry_run=False)

            result = build_run_cleanup(_settings(vault))

        self.assertEqual(result.candidates, [])

    def test_builds_archive_candidates_for_superseded_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            run_dir = vault / "60_Runs"
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_snapshot(run_dir / "2026-06-01_next-actions.md", "next-actions", "2026-06-01T10:00:00+09:00")
            _write_snapshot(run_dir / "2026-06-01_next-actions-2.md", "next-actions", "2026-06-01T11:00:00+09:00")
            _write_snapshot(run_dir / "2026-06-01_vault-health.md", "vault-health", "2026-06-01T10:00:00+09:00")
            _write_snapshot(run_dir / "2026-06-01_vault-health-2.md", "vault-health", "2026-06-01T11:00:00+09:00")

            result = build_run_cleanup(_settings(vault))

        self.assertEqual(len(result.candidates), 2)
        reasons = [candidate.reason for candidate in result.candidates]
        self.assertTrue(any(reason.startswith("superseded by newer next-actions snapshot") for reason in reasons))
        self.assertTrue(any(reason.startswith("superseded by newer vault-health snapshot") for reason in reasons))

    def test_build_run_cleanup_queue_summarizes_existing_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            proposal = _write_run_cleanup_proposal(vault, checked=False)
            proposal.write_text(
                proposal.read_text(encoding="utf-8").replace("- [ ] A001", "- [x] A001"),
                encoding="utf-8",
            )

            queue = build_run_cleanup_queue(vault)

        self.assertEqual([path.resolve() for path in queue.proposal_paths], [proposal.resolve()])
        self.assertEqual(queue.pending_candidates, 3)
        self.assertEqual(queue.checked_items, 1)


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
    run_dir = vault / "60_Runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    proposal = run_dir / "2026-06-01_blueprint-refresh.md"
    proposal.write_text(
        """---
type: blueprint-refresh
status: draft
proposal_state: applied
generated_by: research-agent
checked_at: "2026-06-01T10:00:00+09:00"
---
# Blueprint Refresh
""",
        encoding="utf-8",
    )
    (run_dir / "2026-06-01_source-audit.md").write_text(
        """---
type: source-audit
status: draft
generated_by: research-agent
checked_at: "2026-06-01T10:00:00+09:00"
audit_status: PASS
failure_count: 0
warning_count: 0
---
# Source Audit
""",
        encoding="utf-8",
    )
    (run_dir / "2026-06-01_source-audit-2.md").write_text(
        """---
type: source-audit
status: draft
generated_by: research-agent
checked_at: "2026-06-01T11:00:00+09:00"
audit_status: PASS
failure_count: 0
warning_count: 0
---
# Source Audit
""",
        encoding="utf-8",
    )
    (run_dir / "2026-06-01_bilingual-audit.md").write_text(
        """---
type: bilingual-audit
status: draft
generated_by: research-agent
checked_at: "2026-06-01T10:00:00+09:00"
audit_status: PASS
failure_count: 0
warning_count: 0
---
# Bilingual Audit
""",
        encoding="utf-8",
    )
    (run_dir / "2026-06-01_bilingual-audit-2.md").write_text(
        """---
type: bilingual-audit
status: draft
generated_by: research-agent
checked_at: "2026-06-01T11:00:00+09:00"
audit_status: PASS
failure_count: 0
warning_count: 0
---
# Bilingual Audit
""",
        encoding="utf-8",
    )
    return proposal


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


def _write_run_cleanup_proposal(vault: Path, *, checked: bool) -> Path:
    result = build_run_cleanup(_settings(vault))
    path = vault / "60_Runs" / "2026-06-01_run-cleanup.md"
    state = "x" if checked else " "
    text = render_run_cleanup_note(result, checked_at="2026-06-01T10:00:00+09:00")
    text = text.replace("- [ ] A001", f"- [{state}] A001")
    text = text.replace("- [ ] A002", f"- [{state}] A002")
    text = text.replace("- [ ] A003", f"- [{state}] A003")
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
