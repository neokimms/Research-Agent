from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.bilingual_audit import render_bilingual_audit, run_bilingual_audit, write_bilingual_audit_note
from research_agent.bilingual_upgrade import upgrade_bilingual_notes
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


class BilingualAuditTests(unittest.TestCase):
    def test_passes_clean_bilingual_appendix_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_note(vault, _source_note())
            upgrade_bilingual_notes(vault, dry_run=False)

            result = run_bilingual_audit(vault)
            rendered = render_bilingual_audit(result)

        self.assertTrue(result.passed)
        self.assertEqual(result.generated_reports, 1)
        self.assertEqual(result.bilingual_notes, 1)
        self.assertEqual(result.appendix_notes, 1)
        self.assertIn("Status: PASS", rendered)
        self.assertIn("- None.", rendered)

    def test_fails_missing_bilingual_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_note(
                vault,
                """---
type: source-note
status: draft
generated_by: research-agent
---
# Source

## Core Summary

No translation here.
""",
            )

            result = run_bilingual_audit(vault)

        self.assertFalse(result.passed)
        checks = {issue.check for issue in result.issues}
        self.assertIn("frontmatter.language", checks)
        self.assertIn("frontmatter.translation_language", checks)
        self.assertIn("body.translation_block", checks)

    def test_warns_on_problem_marker_and_refresh_needed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_note(
                vault,
                _clean_note().replace(
                    "Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
                    "Seed 공식 문서 출처. Fetch or search this domain for exact 근거.",
                ),
            )

            result = run_bilingual_audit(vault)

        self.assertTrue(result.passed)
        checks = {issue.check for issue in result.issues}
        self.assertIn("body.translation_quality", checks)
        self.assertIn("body.refresh", checks)

    def test_skips_dictionary_refresh_warning_for_manual_translation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_note(
                vault,
                _clean_note()
                .replace("Translation mode: dictionary", "Translation mode: manual-official-docs-refresh")
                .replace(
                    "Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
                    "수동 검증된 공식 문서 번역입니다.",
                ),
            )

            result = run_bilingual_audit(vault)

        self.assertTrue(result.passed)
        self.assertNotIn("body.refresh", {issue.check for issue in result.issues})

    def test_target_paths_limit_audit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            clean = _write_note(vault, _source_note())
            upgrade_bilingual_notes(vault, dry_run=False)
            broken = vault / "10_Sources" / "broken.md"
            broken.write_text(
                """---
type: source-note
status: draft
generated_by: research-agent
---
# Broken
""",
                encoding="utf-8",
            )

            result = run_bilingual_audit(vault, target_paths=[clean])

        self.assertTrue(result.passed)
        self.assertEqual(result.generated_reports, 1)

    def test_cli_returns_failure_for_broken_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_note(
                vault,
                """---
type: source-note
status: draft
generated_by: research-agent
---
# Source
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
                code = main(["--config", str(config), "bilingual-audit"])

        self.assertEqual(code, 1)
        self.assertIn("Status: FAIL", output.getvalue())

    def test_write_bilingual_audit_note_records_result_in_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_note(vault, _source_note())
            upgrade_bilingual_notes(vault, dry_run=False)

            write_result = write_bilingual_audit_note(_settings(vault))
            text = write_result.note_path.read_text(encoding="utf-8")

        self.assertTrue(write_result.note_path.name.startswith("2026-"))
        self.assertIn("/60_Runs/", write_result.note_path.as_posix())
        self.assertIn('type: "bilingual-audit"', text)
        self.assertIn('audit_status: "PASS"', text)
        self.assertIn("| generated report notes scanned | 1 |", text)
        self.assertIn("- No bilingual follow-up required.", text)

    def test_cli_write_note_prints_written_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_note(vault, _source_note())
            upgrade_bilingual_notes(vault, dry_run=False)
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
                code = main(["--config", str(config), "bilingual-audit", "--write-note"])
            run_dir_exists = (vault / "60_Runs").exists()

        self.assertEqual(code, 0)
        self.assertIn("Audit note written:", output.getvalue())
        self.assertTrue(run_dir_exists)


def _write_note(vault: Path, text: str) -> Path:
    path = vault / "10_Sources" / "example.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _clean_note() -> str:
    return """---
type: source-note
status: draft
generated_by: research-agent
language: bilingual
original_language: en
translation_language: ko
translation_mode: dictionary
---
# Source

## Core Summary

Seed official documentation source. Fetch or search this domain for exact evidence.

## Korean Translation Draft

Translation mode: dictionary

### Core Summary

**원본**

Seed official documentation source. Fetch or search this domain for exact evidence.

**한국어 번역**

Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.
"""


def _source_note() -> str:
    return """---
type: source-note
status: draft
generated_by: research-agent
---
# Source

## Core Summary

Seed official documentation source. Fetch or search this domain for exact evidence.
"""


def _settings(vault: Path) -> Settings:
    return Settings(
        app=AppSettings(timezone="Asia/Seoul"),
        obsidian=ObsidianSettings(vault_path=vault),
        openai=OpenAISettings(),
        sources=SourceSettings(),
        quality_gates=QualityGateSettings(),
        common=CommonModuleSettings(enabled=False),
    )


if __name__ == "__main__":
    unittest.main()
