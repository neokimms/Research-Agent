from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.render import _translate_to_korean


class RenderTranslationTests(unittest.TestCase):
    def test_translates_claim_line_with_metadata(self) -> None:
        translated = _translate_to_korean(
            "- E001 (medium, official-docs): Seed official documentation source. Fetch or search this domain for exact evidence."
        )

        self.assertEqual(
            translated,
            "- E001 (중간, 공식 문서): Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
        )

    def test_translates_citable_evidence_line_with_claim_id(self) -> None:
        translated = _translate_to_korean(
            "- E001: Seed official documentation source. Fetch or search this domain for exact evidence."
        )

        self.assertEqual(
            translated,
            "- E001: Seed 공식 문서 출처입니다. 정확한 근거를 위해 이 도메인에서 문서를 가져오거나 검색하세요.",
        )

    def test_translates_source_url_line_without_review_marker(self) -> None:
        translated = _translate_to_korean("- Source: https://developers.openai.com/")

        self.assertEqual(translated, "- 출처: https://developers.openai.com/")
        self.assertNotIn("한국어 번역 검토 필요", translated)

    def test_translates_supported_category_inside_backticks(self) -> None:
        translated = _translate_to_korean("- Supports `official-docs` decisions.")

        self.assertEqual(translated, "- `공식 문서` 관련 결정을 뒷받침합니다.")

    def test_translates_workflow_code_block_without_review_markers(self) -> None:
        translated = _translate_to_korean(
            """```text
question
-> source collection
-> evidence ledger
-> structure classification
-> service blueprint draft
-> Obsidian review
```"""
        )

        self.assertEqual(
            translated,
            """```text
질문
-> 출처 수집
-> 근거 장부
-> 구조 분류
-> 실서비스 기본형 초안
-> Obsidian 검토
```""",
        )
        self.assertNotIn("한국어 번역 검토 필요", translated)

    def test_translates_refreshed_paper_claims_without_review_markers(self) -> None:
        openai_claim = (
            "By creating an OpenAI account and securing an API key, users can begin building customized AI assistants "
            "tailored to their unique goals—whether for personal productivity, lifestyle tasks, or business use."
        )
        langgraph_claim = (
            "LangGraph is a popular open source framework—created by LangChain—that helps developers use large language "
            "models (LLMs) to build sophisticated, stateful, and multi-actor applications."
        )

        self.assertNotIn("한국어 번역 검토 필요", _translate_to_korean(openai_claim))
        self.assertNotIn("open 출처", _translate_to_korean(langgraph_claim))
        self.assertIn("오픈소스 프레임워크", _translate_to_korean(langgraph_claim))


if __name__ == "__main__":
    unittest.main()
