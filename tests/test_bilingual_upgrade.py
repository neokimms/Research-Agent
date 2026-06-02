from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.bilingual_upgrade import render_bilingual_upgrade_result, upgrade_bilingual_notes
from research_agent.cli import main


class BilingualUpgradeTests(unittest.TestCase):
    def test_dry_run_preserves_existing_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_generated_note(vault)
            before = note.read_text(encoding="utf-8")

            result = upgrade_bilingual_notes(vault, dry_run=True)
            after = note.read_text(encoding="utf-8")

        self.assertEqual(after, before)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.upgraded_paths), 1)
        self.assertEqual(result.translator_mode, "dictionary")

    def test_apply_adds_dictionary_translation_appendix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_generated_note(vault)

            result = upgrade_bilingual_notes(vault, dry_run=False)
            after = note.read_text(encoding="utf-8")
            rendered = render_bilingual_upgrade_result(result)

        self.assertIn('language: "bilingual"', after)
        self.assertIn('original_language: "en"', after)
        self.assertIn('translation_language: "ko"', after)
        self.assertIn('translation_mode: "dictionary"', after)
        self.assertIn("## Korean Translation Draft", after)
        self.assertIn("**원본**", after)
        self.assertIn("**한국어 번역**", after)
        self.assertIn("seed 도메인에만 의존하지 말고 정확한 공식 문서 페이지를 확인하세요.", after)
        self.assertIn("Updated notes: 1", rendered)

    def test_reviewed_notes_are_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_generated_note(vault, status="reviewed")

            result = upgrade_bilingual_notes(vault, dry_run=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(len(result.skipped_reviewed), 1)
        self.assertEqual(result.upgraded_paths, [])

    def test_existing_bilingual_notes_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_generated_note(
                vault,
                extra_frontmatter='language: bilingual\ntranslation_language: ko\n',
                body="# Source\n\n## Core Summary\n\n**한국어 번역**\n\n이미 번역됨\n",
            )

            result = upgrade_bilingual_notes(vault, dry_run=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.skipped_existing, 1)
        self.assertEqual(result.total_scanned, 1)

    def test_refresh_translation_rebuilds_existing_appendix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            note = _write_generated_note(
                vault,
                extra_frontmatter='language: bilingual\ntranslation_language: ko\ntranslation_mode: dictionary\n',
                body="""# Source

## Core Summary

Seed official documentation source. Fetch or search this domain for exact evidence.

## Citable Evidence

- Source: https://developers.openai.com/
- E001: Seed official documentation source. Fetch or search this domain for exact evidence.

## Korean Translation Draft

Translation mode: dictionary

### Core Summary

**원본**

Seed official documentation source. Fetch or search this domain for exact evidence.

**한국어 번역**

Seed 공식 문서 출처. Fetch or search this domain for exact 근거.
""",
            )

            result = upgrade_bilingual_notes(vault, dry_run=False, refresh_translation=True)
            after = note.read_text(encoding="utf-8")

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(len(result.upgraded_paths), 1)
        self.assertIn("refresh existing Korean translation draft", result.candidates[0].reason)
        self.assertEqual(after.count("## Korean Translation Draft"), 1)
        self.assertNotIn("Fetch or search this domain for exact 근거", after)
        self.assertIn("Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.", after)
        self.assertIn("- 출처: https://developers.openai.com/", after)
        self.assertIn("- E001: Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.", after)

    def test_refresh_translation_ignores_missing_appendix_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            _write_generated_note(vault)

            result = upgrade_bilingual_notes(vault, dry_run=True, refresh_translation=True)

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.upgraded_paths, [])

    def test_cli_uses_dictionary_when_no_api_key_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root / "vault"
            _write_generated_note(vault)
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
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["--config", str(config), "--env-file", str(root / "missing.env"), "upgrade-bilingual", "--max-notes", "1"])

        self.assertEqual(code, 0)
        self.assertIn("API key state: no supported key detected", output.getvalue())
        self.assertIn("Translator mode: dictionary", output.getvalue())


def _write_generated_note(
    vault: Path,
    *,
    status: str = "draft",
    extra_frontmatter: str = "",
    body: str | None = None,
) -> Path:
    path = vault / "10_Sources" / "example-source.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = body or """# Source

## Core Summary

Confirm exact official documentation pages instead of relying only on seed domains.
"""
    path.write_text(
        f"""---
type: source-note
topic: Agentic RAG
status: {status}
generated_by: research-agent
{extra_frontmatter}---
{content}
""",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
