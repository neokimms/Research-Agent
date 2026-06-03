from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import ObsidianSettings
from research_agent.obsidian import ObsidianWriter, read_frontmatter_status, sanitize_filename


class ObsidianWriterTests(unittest.TestCase):
    def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename("Hello World.md"), "Hello-World.md")
        self.assertEqual(sanitize_filename("../bad/name"), "bad-name.md")

    def test_write_note_avoids_overwriting_reviewed_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            writer = ObsidianWriter(ObsidianSettings(vault_path=Path(temp)))
            writer.ensure_structure()
            first = writer.write_note(
                "30_Service-Blueprints",
                "test.md",
                "---\nstatus: reviewed\n---\n# Reviewed\n",
                allow_overwrite=True,
            )
            second = writer.write_note("30_Service-Blueprints", "test.md", "# Draft")
            self.assertEqual(first.name, "test.md")
            self.assertEqual(second.name, "test-2.md")
            self.assertIn("# Reviewed", first.read_text(encoding="utf-8"))

    def test_safe_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            writer = ObsidianWriter(ObsidianSettings(vault_path=Path(temp)))
            with self.assertRaises(ValueError):
                writer.safe_path("../escape")

    def test_read_frontmatter_status_handles_colon_in_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            note = Path(temp) / "note.md"
            note.write_text(
                '---\nstatus : "reviewed: final"\n---\n# Note\n',
                encoding="utf-8",
            )

            self.assertEqual(read_frontmatter_status(note), "reviewed: final")

    def test_write_note_protects_unknown_status_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            writer = ObsidianWriter(ObsidianSettings(vault_path=Path(temp)))
            writer.ensure_structure()
            first = writer.write_note(
                "30_Service-Blueprints",
                "test.md",
                "---\nstatus: reviwed\n---\n# Typo status\n",
                allow_overwrite=True,
            )
            second = writer.write_note(
                "30_Service-Blueprints",
                "test.md",
                "# Replacement",
                allow_overwrite=True,
            )

            self.assertEqual(first.name, "test.md")
            self.assertEqual(second.name, "test-2.md")
            self.assertIn("# Typo status", first.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
