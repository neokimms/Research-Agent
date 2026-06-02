from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.citations import doi_url, extract_arxiv_id, normalize_doi, normalize_source_record, source_identity_key
from research_agent.models import SourceRecord


class CitationTests(unittest.TestCase):
    def test_normalizes_doi_values_and_urls(self) -> None:
        self.assertEqual(normalize_doi("https://doi.org/10.1145/3368089.3409742."), "10.1145/3368089.3409742")
        self.assertEqual(doi_url("DOI:10.1145/3368089.3409742"), "https://doi.org/10.1145/3368089.3409742")

    def test_extracts_arxiv_ids_from_abs_and_pdf_urls(self) -> None:
        self.assertEqual(extract_arxiv_id("https://arxiv.org/abs/2401.12345v2"), "2401.12345v2")
        self.assertEqual(extract_arxiv_id("https://arxiv.org/pdf/2401.12345.pdf"), "2401.12345")

    def test_normalized_source_gets_canonical_identity_and_score(self) -> None:
        record = normalize_source_record(
            SourceRecord(
                title="Paper",
                url="https://doi.org/10.48550/example",
                source_type="papers",
            ),
            provider="crossref",
        )

        self.assertEqual(record.doi, "10.48550/example")
        self.assertEqual(record.canonical_url, "https://doi.org/10.48550/example")
        self.assertEqual(record.source_provider, "crossref")
        self.assertGreater(record.source_score, 0.9)
        self.assertEqual(source_identity_key(record), "doi:10.48550/example")


if __name__ == "__main__":
    unittest.main()
