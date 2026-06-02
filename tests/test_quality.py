from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import QualityGateSettings
from research_agent.models import EvidenceBundle, EvidenceClaim, SourceRecord
from research_agent.quality import FAIL, PASS, evaluate_quality_gates


class QualityGateTests(unittest.TestCase):
    def test_quality_gates_pass_for_traceable_run(self) -> None:
        gates = evaluate_quality_gates(
            QualityGateSettings(min_official_sources=2),
            sources=[
                SourceRecord("Official A", "https://docs.example.com/a", "official-docs"),
                SourceRecord("Official B", "https://docs.example.com/b", "official-docs"),
            ],
            evidence=EvidenceBundle(
                claims=[
                    EvidenceClaim(
                        claim_id="E001",
                        source_id="S001",
                        claim="Official docs support the baseline.",
                        evidence="Docs say so.",
                        source_title="Official A",
                        source_url="https://docs.example.com/a",
                        source_type="official-docs",
                        confidence="high",
                        category="baseline",
                    )
                ]
            ),
            blueprint_markdown="# Blueprint\n\n## Still Uncertain\n\n- Confirm production limits.",
            checked_at="2026-05-31",
            evidence_path="/vault/50_Evidence-Ledger/run.md",
        )

        self.assertTrue(gates)
        self.assertTrue(all(gate.status == PASS for gate in gates))

    def test_quality_gates_fail_for_missing_traceability(self) -> None:
        gates = evaluate_quality_gates(
            QualityGateSettings(min_official_sources=2),
            sources=[
                SourceRecord("Official A", "https://docs.example.com/a", "official-docs"),
                SourceRecord("Paper without URL", "", "papers"),
            ],
            evidence=EvidenceBundle(claims=[]),
            blueprint_markdown="# Blueprint\n\n## Evidence\n\n- E001",
            checked_at="",
            evidence_path="",
        )

        statuses = {gate.name: gate.status for gate in gates}
        self.assertEqual(statuses["min official sources"], FAIL)
        self.assertEqual(statuses["source urls"], FAIL)
        self.assertEqual(statuses["checked_at"], FAIL)
        self.assertEqual(statuses["evidence ledger"], FAIL)
        self.assertEqual(statuses["uncertainty section"], FAIL)


if __name__ == "__main__":
    unittest.main()
