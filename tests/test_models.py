from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.models import MAX_SOURCE_TITLE_LENGTH, EvidenceClaim, SourceRecord


class ModelTests(unittest.TestCase):
    def test_evidence_claim_rejects_blank_claim_or_evidence(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceClaim(
                claim_id="E001",
                source_id="S001",
                claim="",
                evidence="Evidence",
                source_title="Source",
                source_url="https://example.com",
                source_type="official-docs",
                confidence="high",
                category="test",
            )
        with self.assertRaises(ValueError):
            EvidenceClaim(
                claim_id="E001",
                source_id="S001",
                claim="Claim",
                evidence=" ",
                source_title="Source",
                source_url="https://example.com",
                source_type="official-docs",
                confidence="high",
                category="test",
            )

    def test_source_record_rejects_overlong_title(self) -> None:
        with self.assertRaises(ValueError):
            SourceRecord("x" * (MAX_SOURCE_TITLE_LENGTH + 1), "https://example.com", "official-docs")


if __name__ == "__main__":
    unittest.main()
