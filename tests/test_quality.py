from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import QualityGateSettings
from research_agent.models import EvidenceBundle, EvidenceClaim, SourceRecord
from research_agent.quality import FAIL, PASS, evaluate_quality_gates, source_relevance_score


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

    def test_source_relevance_gate_rejects_unrelated_market_sources(self) -> None:
        topic = "스페이스 X 상장에 따른 경쟁사 동향 및 국내 주식 시장 변동성"
        gates = evaluate_quality_gates(
            QualityGateSettings(
                min_official_sources=0,
                min_relevant_sources=2,
                min_relevant_source_ratio=0.5,
                block_vault_write_on_fail=False,
            ),
            topic=topic,
            sources=[
                SourceRecord(
                    "Measuring Chinese Economic Uncertainty and Predictability of the Great China Stock Market Volatility",
                    "https://doi.org/10.38200/jfks.16.3.3",
                    "papers",
                    summary="Crossref metadata record.",
                    source_provider="crossref",
                ),
                SourceRecord(
                    "Standards or security framework candidate",
                    "https://nist.gov/",
                    "standards",
                    summary="Seed standards source.",
                    source_provider="seed",
                ),
            ],
            evidence=EvidenceBundle(
                claims=[
                    EvidenceClaim(
                        claim_id="E001",
                        source_id="S001",
                        claim="A generic stock volatility paper was collected.",
                        evidence="The paper metadata mentions stock volatility.",
                        source_title="Paper",
                        source_url="https://doi.org/10.38200/jfks.16.3.3",
                        source_type="papers",
                        confidence="medium",
                        category="market",
                    )
                ]
            ),
            blueprint_markdown="# Blueprint\n\n## Still Uncertain\n\n- Confirm market sources.",
            checked_at="2026-06-05",
            evidence_path="/vault/evidence.md",
        )

        statuses = {gate.name: gate.status for gate in gates}
        self.assertEqual(statuses["source relevance"], FAIL)

    def test_source_relevance_gate_accepts_direct_ipo_sources(self) -> None:
        topic = "스페이스 X 상장에 따른 경쟁사 동향 및 국내 주식 시장 변동성"
        relevant = SourceRecord(
            "SpaceX Form S-1 IPO filing under ticker SPCX",
            "https://www.sec.gov/Archives/edgar/data/1181412/000162828026036936/0001628280-26-036936-index.htm",
            "general-web",
            summary="SEC EDGAR filing page for SpaceX IPO registration and Nasdaq listing.",
            source_provider="openai-web-search",
        )
        news = SourceRecord(
            "SpaceX IPO market impact and competitors",
            "https://www.reuters.com/markets/deals/spacex-ipo-market-impact",
            "general-web",
            summary="Financial news source discussing SpaceX IPO, competitor positioning, and stock-market impact.",
            source_provider="openai-web-search",
        )
        gates = evaluate_quality_gates(
            QualityGateSettings(
                min_official_sources=0,
                min_relevant_sources=2,
                min_relevant_source_ratio=0.5,
            ),
            topic=topic,
            sources=[relevant, news],
            evidence=EvidenceBundle(
                claims=[
                    EvidenceClaim(
                        claim_id="E001",
                        source_id="S001",
                        claim="SpaceX filed IPO documents.",
                        evidence="SEC EDGAR has the S-1 filing detail page.",
                        source_title=relevant.title,
                        source_url=relevant.url,
                        source_type=relevant.source_type,
                        confidence="high",
                        category="filing",
                    )
                ],
                extraction_mode="structured",
            ),
            blueprint_markdown="# Blueprint\n\n## Still Uncertain\n\n- Confirm final pricing.",
            checked_at="2026-06-05",
            evidence_path="/vault/evidence.md",
        )

        statuses = {gate.name: gate.status for gate in gates}
        self.assertEqual(statuses["source relevance"], PASS)
        self.assertGreaterEqual(source_relevance_score(topic, relevant), 0.22)

    def test_fallback_evidence_gate_can_block_report_trust(self) -> None:
        gates = evaluate_quality_gates(
            QualityGateSettings(
                min_official_sources=0,
                fail_on_fallback_evidence=True,
            ),
            sources=[SourceRecord("Official A", "https://docs.example.com/a", "official-docs")],
            evidence=EvidenceBundle(
                claims=[
                    EvidenceClaim(
                        claim_id="E001",
                        source_id="S001",
                        claim="Fallback claim.",
                        evidence="Fallback evidence.",
                        source_title="Official A",
                        source_url="https://docs.example.com/a",
                        source_type="official-docs",
                        confidence="medium",
                        category="fallback",
                    )
                ],
                extraction_mode="fallback",
            ),
            blueprint_markdown="# Blueprint\n\n## Still Uncertain\n\n- Confirm.",
            checked_at="2026-06-05",
            evidence_path="/vault/evidence.md",
        )

        statuses = {gate.name: gate.status for gate in gates}
        self.assertEqual(statuses["structured evidence extraction"], FAIL)


if __name__ == "__main__":
    unittest.main()
