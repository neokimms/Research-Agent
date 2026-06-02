from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.evidence import extract_evidence, fallback_evidence, parse_evidence_output
from research_agent.models import SourceRecord


class EvidenceTests(unittest.TestCase):
    def test_fallback_evidence_creates_claims_from_sources(self) -> None:
        bundle = fallback_evidence(
            "agentic RAG",
            [
                SourceRecord(
                    title="Official guide",
                    url="https://example.com/guide",
                    source_type="official-docs",
                    summary="Agents can use tools.",
                )
            ],
        )

        self.assertEqual(bundle.extraction_mode, "fallback")
        self.assertEqual(bundle.claims[0].claim_id, "E001")
        self.assertEqual(bundle.claims[0].source_id, "S001")
        self.assertEqual(bundle.claims[0].confidence, "medium")

    def test_parse_evidence_output_validates_claim_shape(self) -> None:
        bundle = parse_evidence_output(
            """
{
  "claims": [
    {
      "claim_id": "E101",
      "source_id": "S001",
      "claim": "LangGraph supports durable workflows.",
      "evidence": "Durable execution is documented.",
      "source_title": "LangGraph Overview",
      "source_url": "https://docs.langchain.com/oss/python/langgraph/overview",
      "source_type": "official-docs",
      "confidence": "high",
      "category": "orchestration"
    }
  ],
  "conflicts": [],
  "needs_verification": ["Check latest page update date."]
}
""",
            sources=[
                SourceRecord(
                    title="LangGraph Overview",
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    source_type="official-docs",
                )
            ],
        )

        self.assertEqual(bundle.extraction_mode, "structured-json")
        self.assertEqual(bundle.claims[0].claim_id, "E101")
        self.assertEqual(bundle.claims[0].confidence, "high")
        self.assertEqual(bundle.needs_verification, ["Check latest page update date."])

    def test_extract_evidence_uses_structured_outputs_schema(self) -> None:
        calls = {}

        class FakeClient:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def create(self, **kwargs):
                calls["create"] = kwargs
                return {
                    "output_text": """
{
  "claims": [
    {
      "claim_id": "E001",
      "source_id": "S001",
      "claim": "The official page describes agent orchestration.",
      "evidence": "The source summary says orchestration.",
      "source_title": "Agents",
      "source_url": "https://developers.openai.com/api/docs/guides/agents",
      "source_type": "official-docs",
      "confidence": "high",
      "category": "orchestration"
    }
  ],
  "conflicts": [],
  "needs_verification": []
}
"""
                }

        with patch("research_agent.evidence.OpenAIResponsesClient", FakeClient):
            bundle = extract_evidence(
                "agents",
                [
                    SourceRecord(
                        title="Agents",
                        url="https://developers.openai.com/api/docs/guides/agents",
                        source_type="official-docs",
                        summary="The source summary says orchestration.",
                    )
                ],
                api_key="sk-test-123456",
                model="gpt-5.4-mini",
                offline=False,
            )

        self.assertEqual(bundle.extraction_mode, "structured-json")
        self.assertEqual(calls["create"]["text_format"]["type"], "json_schema")
        self.assertTrue(calls["create"]["text_format"]["strict"])
        self.assertEqual(bundle.claims[0].category, "orchestration")

    def test_extract_evidence_uses_gemini_response_schema(self) -> None:
        calls = {}

        class FakeClient:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def generate(self, **kwargs):
                calls["generate"] = kwargs
                return {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": """
{
  "claims": [
    {
      "claim_id": "E001",
      "source_id": "S001",
      "claim": "Gemini extracted this claim.",
      "evidence": "The source summary says extraction.",
      "source_title": "Gemini Docs",
      "source_url": "https://ai.google.dev/gemini-api/docs/structured-output",
      "source_type": "official-docs",
      "confidence": "high",
      "category": "extraction"
    }
  ],
  "conflicts": [],
  "needs_verification": []
}
"""
                                    }
                                ]
                            }
                        }
                    ]
                }

        with patch("research_agent.evidence.GeminiGenerateClient", FakeClient):
            bundle = extract_evidence(
                "gemini",
                [
                    SourceRecord(
                        title="Gemini Docs",
                        url="https://ai.google.dev/gemini-api/docs/structured-output",
                        source_type="official-docs",
                        summary="The source summary says extraction.",
                    )
                ],
                api_key="gemini-test",
                model="gemini-2.5-flash",
                provider="gemini",
                offline=False,
            )

        self.assertEqual(bundle.extraction_mode, "structured-json")
        self.assertEqual(calls["generate"]["response_schema"]["type"], "object")
        self.assertEqual(bundle.claims[0].category, "extraction")


if __name__ == "__main__":
    unittest.main()
