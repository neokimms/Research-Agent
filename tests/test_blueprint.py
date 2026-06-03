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
        self.assertIn("## Synthesis Coverage", markdown)
        self.assertIn("Stabilization default-filled sections requiring review", markdown)
        self.assertIn("안정화 단계에서 기본값으로 채워 검토가 필요한 섹션", markdown)

    def test_stabilize_service_blueprint_preserves_frontmatter(self) -> None:
        markdown = stabilize_service_blueprint(
            "---\ntype: service-blueprint\n---\n# Existing\n\n## Evidence\n\n- E001",
            topic="Agentic RAG",
        )

        self.assertTrue(markdown.startswith("---\ntype: service-blueprint\n---"))
        self.assertIn("## Evidence", markdown)
        self.assertEqual(markdown.count("## Evidence"), 1)

    def test_stabilize_service_blueprint_can_render_english_only_coverage(self) -> None:
        markdown = stabilize_service_blueprint("A short answer.", topic="Agentic RAG", bilingual=False)

        self.assertIn("## Synthesis Coverage", markdown)
        self.assertNotIn("**한국어 번역**", markdown)


if __name__ == "__main__":
    unittest.main()
