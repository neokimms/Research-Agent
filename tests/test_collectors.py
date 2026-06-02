from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.collectors import (
    collect_official_doc_sources,
    collect_paper_sources,
    deduplicate_sources,
    search_arxiv,
    search_crossref,
    search_openalex,
    search_semantic_scholar,
)
from research_agent.config import SourceSettings
from research_agent.models import SourceRecord


class CollectorTests(unittest.TestCase):
    def test_official_docs_falls_back_without_api_key(self) -> None:
        records = collect_official_doc_sources(
            "agentic RAG",
            SourceSettings(official_doc_domains=["developers.openai.com"]),
            api_key=None,
            model="gpt-5.4-mini",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].url, "https://developers.openai.com/")
        self.assertEqual(records[0].source_provider, "seed")
        self.assertEqual(records[0].canonical_url, "https://developers.openai.com/")

    def test_official_docs_uses_openai_web_search_and_filters_domains(self) -> None:
        calls = {}

        class FakeClient:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def create(self, **kwargs):
                calls["create"] = kwargs
                return {
                    "output_text": """
[
  {
    "title": "Agents SDK",
    "url": "https://developers.openai.com/api/docs/guides/agents",
    "summary": "Official Agents SDK guide.",
    "domain": "developers.openai.com"
  },
  {
    "title": "Unofficial",
    "url": "https://example.com/blog",
    "summary": "Ignore me.",
    "domain": "example.com"
  }
]
"""
                }

        with patch("research_agent.collectors.OpenAIResponsesClient", FakeClient):
            records = collect_official_doc_sources(
                "OpenAI Agents SDK",
                SourceSettings(official_doc_domains=["developers.openai.com"]),
                api_key="sk-test-123456",
                model="gpt-5.4-mini",
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Agents SDK")
        self.assertEqual(records[0].source_type, "official-docs")
        self.assertEqual(records[0].source_provider, "openai-web-search")
        self.assertGreater(records[0].source_score, 0.8)
        self.assertEqual(calls["create"]["tools"][0]["type"], "web_search")
        self.assertEqual(calls["create"]["tools"][0]["filters"]["allowed_domains"], ["developers.openai.com"])
        self.assertEqual(calls["create"]["tool_choice"], "required")

    def test_official_docs_uses_gemini_google_search_and_filters_domains(self) -> None:
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
[
  {
    "title": "LangGraph",
    "url": "https://docs.langchain.com/oss/python/langgraph/overview",
    "summary": "Official LangGraph overview.",
    "domain": "docs.langchain.com"
  },
  {
    "title": "Unofficial",
    "url": "https://example.com/blog",
    "summary": "Ignore me.",
    "domain": "example.com"
  }
]
"""
                                    }
                                ]
                            }
                        }
                    ]
                }

        with patch("research_agent.collectors.GeminiGenerateClient", FakeClient):
            records = collect_official_doc_sources(
                "LangGraph",
                SourceSettings(official_doc_domains=["docs.langchain.com"]),
                api_key="gemini-test",
                model="gemini-2.5-flash",
                provider="gemini",
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].url, "https://docs.langchain.com/oss/python/langgraph/overview")
        self.assertEqual(records[0].source_provider, "gemini-google-search")
        self.assertEqual(calls["generate"]["tools"], [{"google_search": {}}])

    def test_deduplicate_sources_prefers_doi_identity(self) -> None:
        records = deduplicate_sources(
            [
                SourceRecord(
                    title="Paper from Crossref",
                    url="https://doi.org/10.1145/example",
                    source_type="papers",
                    summary="Short.",
                    doi="10.1145/example",
                    source_provider="crossref",
                ),
                SourceRecord(
                    title="Paper from OpenAlex",
                    url="https://doi.org/10.1145/EXAMPLE",
                    source_type="papers",
                    summary="Longer metadata record.",
                    doi="10.1145/EXAMPLE",
                    source_provider="openalex",
                    authors=["A. Author"],
                ),
            ]
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].doi, "10.1145/example")
        self.assertEqual(records[0].source_provider, "openalex")

    def test_paper_collectors_normalize_citation_metadata(self) -> None:
        arxiv_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2401.12345v2</id>
    <title>Arxiv Paper</title>
    <summary>Summary.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <updated>2024-01-02T00:00:00Z</updated>
    <author><name>Alice</name></author>
  </entry>
</feed>
"""
        crossref_json = """
{
  "message": {
    "items": [
      {
        "title": ["Crossref Paper"],
        "DOI": "10.1145/3368089.3409742",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "published-online": {"date-parts": [[2024, 1, 3]]}
      }
    ]
  }
}
"""
        openalex_json = """
{
  "results": [
    {
      "display_name": "OpenAlex Paper",
      "doi": "https://doi.org/10.48550/arxiv.2401.12345",
      "publication_date": "2024-01-04",
      "authorships": [{"author": {"display_name": "Grace Hopper"}}]
    }
  ]
}
"""

        def fake_get_text(url, *, timeout_seconds):
            if "arxiv" in url:
                return arxiv_xml
            if "crossref" in url:
                return crossref_json
            if "openalex" in url:
                return openalex_json
            raise AssertionError(url)

        with patch("research_agent.collectors._get_text", fake_get_text):
            arxiv_record = search_arxiv("topic", limit=1)[0]
            crossref_record = search_crossref("topic", limit=1)[0]
            openalex_record = search_openalex("topic", limit=1)[0]

        self.assertEqual(arxiv_record.arxiv_id, "2401.12345v2")
        self.assertEqual(arxiv_record.canonical_url, "https://arxiv.org/abs/2401.12345v2")
        self.assertEqual(arxiv_record.source_provider, "arxiv")
        self.assertEqual(crossref_record.doi, "10.1145/3368089.3409742")
        self.assertEqual(crossref_record.canonical_url, "https://doi.org/10.1145/3368089.3409742")
        self.assertEqual(crossref_record.source_provider, "crossref")
        self.assertEqual(openalex_record.doi, "10.48550/arxiv.2401.12345")
        self.assertEqual(openalex_record.source_provider, "openalex")

    def test_semantic_scholar_collector_normalizes_metadata(self) -> None:
        response_json = """
{
  "data": [
    {
      "title": "Semantic Scholar Paper",
      "url": "https://www.semanticscholar.org/paper/example",
      "abstract": "A relevant paper.",
      "authors": [{"name": "Leslie Lamport"}],
      "publicationDate": "2024-02-03",
      "externalIds": {
        "DOI": "10.1145/1234567",
        "ArXiv": "2402.12345"
      }
    }
  ]
}
"""

        with patch("research_agent.collectors._get_text", return_value=response_json):
            record = search_semantic_scholar("topic", limit=1)[0]

        self.assertEqual(record.source_provider, "semantic-scholar")
        self.assertEqual(record.doi, "10.1145/1234567")
        self.assertEqual(record.arxiv_id, "2402.12345")
        self.assertEqual(record.canonical_url, "https://doi.org/10.1145/1234567")
        self.assertEqual(record.authors, ["Leslie Lamport"])

    def test_paper_collector_failures_become_warnings_not_sources(self) -> None:
        warnings = []

        with patch("research_agent.collectors.search_crossref", side_effect=RuntimeError("rate limited")):
            records = collect_paper_sources("topic", ["crossref", "unknown"], limit_each=1, warnings=warnings)

        self.assertEqual(records, [])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(warnings[0].category, "paper collector")
        self.assertEqual(warnings[0].source, "crossref")
        self.assertIn("rate limited", warnings[0].detail)
        self.assertEqual(warnings[1].source, "unknown")


if __name__ == "__main__":
    unittest.main()
