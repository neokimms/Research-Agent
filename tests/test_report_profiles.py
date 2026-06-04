from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.report_profiles import get_report_profile, normalize_research_type, render_required_sections_for_prompt


class ReportProfileTests(unittest.TestCase):
    def test_normalizes_common_research_type_aliases(self) -> None:
        self.assertEqual(normalize_research_type("papers"), "paper")
        self.assertEqual(normalize_research_type("paper-analysis"), "paper")
        self.assertEqual(normalize_research_type("market research"), "market")
        self.assertEqual(normalize_research_type("official-docs"), "architecture")
        self.assertEqual(normalize_research_type("unknown"), "architecture")

    def test_paper_profile_prompt_sections_are_research_specific(self) -> None:
        profile = get_report_profile("paper")
        outline = render_required_sections_for_prompt(profile)

        self.assertEqual(profile.report_title, "Paper Analysis Report")
        self.assertIn("Paper Corpus", outline)
        self.assertIn("Methodology Comparison", outline)
        self.assertIn("Reproducibility Notes", outline)
        self.assertNotIn("Implementation Order", outline)

    def test_market_profile_prompt_sections_are_market_specific(self) -> None:
        profile = get_report_profile("market")
        outline = render_required_sections_for_prompt(profile)

        self.assertEqual(profile.report_title, "Market Research Report")
        self.assertIn("Market Landscape", outline)
        self.assertIn("Vendor And Product Map", outline)
        self.assertIn("Opportunity Hypotheses", outline)
        self.assertNotIn("Verification", outline)


if __name__ == "__main__":
    unittest.main()
