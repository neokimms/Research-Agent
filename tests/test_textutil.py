from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.textutil import yaml_scalar


class TextUtilTests(unittest.TestCase):
    def test_yaml_scalar_escapes_yaml_sensitive_strings(self) -> None:
        self.assertEqual(
            yaml_scalar('topic: [agent]\n"quoted" \\ path'),
            '"topic: [agent]\\n\\"quoted\\" \\\\ path"',
        )

    def test_yaml_scalar_keeps_primitive_types(self) -> None:
        self.assertEqual(yaml_scalar(True), "true")
        self.assertEqual(yaml_scalar(3), "3")
        self.assertEqual(yaml_scalar(1.234), "1.23")
        self.assertEqual(yaml_scalar(None), "")


if __name__ == "__main__":
    unittest.main()
