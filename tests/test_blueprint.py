from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.blueprint import REQUIRED_BLUEPRINT_SECTIONS, stabilize_service_blueprint


class BlueprintTests(unittest.TestCase):
    def test_stabilize_service_blueprint_adds_missing_sections(self) -> None:
        markdown = stabilize_service_blueprint("A short answer.", topic="Agentic RAG")

        self.assertIn("# Agentic RAG Service Blueprint", markdown)
        for section in REQUIRED_BLUEPRINT_SECTIONS:
            self.assertIn(f"## {section}", markdown)

    def test_stabilize_service_blueprint_preserves_frontmatter(self) -> None:
        markdown = stabilize_service_blueprint(
            "---\ntype: service-blueprint\n---\n# Existing\n\n## Evidence\n\n- E001",
            topic="Agentic RAG",
        )

        self.assertTrue(markdown.startswith("---\ntype: service-blueprint\n---"))
        self.assertIn("## Evidence", markdown)
        self.assertEqual(markdown.count("## Evidence"), 1)


if __name__ == "__main__":
    unittest.main()
