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
from research_agent.verification_cleanup import (
    apply_verification_cleanup,
    build_verification_cleanup,
    render_verification_cleanup,
    render_verification_cleanup_apply_result,
    render_verification_cleanup_note,
    write_verification_cleanup_note,
)


class VerificationCleanupTests(unittest.TestCase):
    def test_builds_cleanup_candidates_for_stale_verification_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            result = build_verification_cleanup(_settings(vault))
            rendered = render_verification_cleanup(result)

        self.assertEqual(result.notes_scanned, 2)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.candidates[0].proposal_id, "V001")
        self.assertIn("Cleanup candidates: 2", rendered)
        self.assertIn("50_Evidence-Ledger/evidence.md", rendered)
        self.assertIn("30_Service-Blueprints/blueprint.md", rendered)

    def test_write_proposal_note_includes_candidate_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)

            write_result = write_verification_cleanup_note(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertIn('type: "verification-cleanup"', text)
        self.assertIn("- [ ] V001 Clean", text)
        self.assertIn("## Candidate Records", text)
        self.assertIn('"note_type": "service-blueprint"', text)

    def test_apply_checked_candidates_updates_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            ledger, blueprint = _write_fixture(vault)
            proposal = _write_verification_cleanup_proposal(vault, checked=True)

            result = apply_verification_cleanup(
                _settings(vault),
                dry_run=False,
                applied_at="2026-06-01T10:00:00+09:00",
            )
            ledger_text = ledger.read_text(encoding="utf-8")
            blueprint_text = blueprint.read_text(encoding="utf-8")
            proposal_text = proposal.read_text(encoding="utf-8")
            rendered = render_verification_cleanup_apply_result(result)

        self.assertEqual(len(result.approved_items), 2)
        self.assertEqual(len(result.updated_paths), 2)
        self.assertIn('verification_cleanup_provider: "deterministic-stale-text"', ledger_text)
        self.assertIn("Paper sources are now connected in this evidence ledger", ledger_text)
        self.assertIn("논문 출처가 이제 이 근거 장부에 연결되었습니다.", ledger_text)
        self.assertIn("Paper evidence is connected", blueprint_text)
        self.assertIn("논문 근거는 연결되었지만", blueprint_text)
        self.assertIn('proposal_state: "applied"', proposal_text)
        self.assertIn("Updated notes: 2", rendered)

    def test_apply_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            ledger, _blueprint = _write_fixture(vault)
            _write_verification_cleanup_proposal(vault, checked=True)
            before = ledger.read_text(encoding="utf-8")

            result = apply_verification_cleanup(_settings(vault), dry_run=True)
            after = ledger.read_text(encoding="utf-8")

        self.assertEqual(before, after)
        self.assertEqual(len(result.approved_items), 2)
        self.assertEqual(len(result.updated_paths), 2)

    def test_no_candidate_after_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_fixture(vault)
            _write_verification_cleanup_proposal(vault, checked=True)
            apply_verification_cleanup(_settings(vault), dry_run=False)

            result = build_verification_cleanup(_settings(vault))

        self.assertEqual(result.candidates, [])

    def test_cli_apply_verification_cleanup_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_fixture(vault)
            _write_verification_cleanup_proposal(vault, checked=True)
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
                code = main(["--config", str(config), "apply-verification-cleanup", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("Verification Text Cleanup Apply Dry Run", output.getvalue())
        self.assertIn("Would update notes: 2", output.getvalue())


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


def _write_fixture(vault: Path) -> tuple[Path, Path]:
    blueprint = vault / "30_Service-Blueprints" / "blueprint.md"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    blueprint.write_text(
        """---
type: service-blueprint
topic: "Agentic RAG"
status: draft
generated_by: research-agent
---
# Agentic RAG Service Blueprint

## Still Uncertain

**원본**

- Official documentation and standards pages are resolved; paper metadata still needs review.

**한국어 번역**

- 공식 문서와 표준 출처는 정확한 세부 페이지로 확정되었습니다. 논문 메타데이터는 아직 검토가 필요합니다.
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
# Evidence Ledger: Agentic RAG

| claim_id | claim | source | source_type | checked_at | confidence | note |
|---|---|---|---|---|---|---|
| E001 | Paper source. | https://doi.org/10.example/paper | papers | 2026-06-01 | medium | papers: Paper source. |

## Needs Verification

**원본**

- No paper sources were collected in this run; add paper metadata in a follow-up research pass if papers are required.

**한국어 번역**

- 이 실행에서는 논문 출처가 수집되지 않았으므로, 논문 근거가 필요하면 후속 research pass에서 논문 메타데이터를 추가합니다.
""",
        encoding="utf-8",
    )
    return ledger, blueprint


def _write_verification_cleanup_proposal(vault: Path, *, checked: bool) -> Path:
    result = build_verification_cleanup(_settings(vault))
    path = vault / "60_Runs" / "2026-06-01_verification-cleanup.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = "x" if checked else " "
    text = render_verification_cleanup_note(result, checked_at="2026-06-01T10:00:00+09:00")
    text = text.replace("- [ ] V001", f"- [{state}] V001").replace("- [ ] V002", f"- [{state}] V002")
    path.write_text(text, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
