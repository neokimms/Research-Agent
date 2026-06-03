from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.gemini_client import GeminiGenerateClient, gemini_output_text


class GeminiClientTests(unittest.TestCase):
    def test_output_text_reads_candidate_parts(self) -> None:
        text = gemini_output_text(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Hello"}, {"text": " world"}]}}
                ]
            }
        )

        self.assertEqual(text, "Hello\n world")

    def test_generate_builds_rest_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = GeminiGenerateClient(api_key="gemini-test", default_model="gemini-2.5-flash", timeout_seconds=7)
        with patch("urllib.request.urlopen", fake_urlopen):
            response = client.generate(
                input_text="Hi",
                instructions="Be brief.",
                tools=[{"google_search": {}}],
                response_schema={"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]},
            )

        self.assertIn("/models/gemini-2.5-flash:generateContent", captured["url"])
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["headers"]["X-goog-api-key"], "gemini-test")
        self.assertEqual(captured["body"]["tools"], [{"google_search": {}}])
        self.assertEqual(
            captured["body"]["generationConfig"]["responseFormat"]["text"]["mimeType"],
            "application/json",
        )
        self.assertEqual(gemini_output_text(response), "OK")

    def test_generate_retries_transient_http_errors(self) -> None:
        calls = {"count": 0}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}'

        def fake_urlopen(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b"rate limited"))
            return FakeResponse()

        client = GeminiGenerateClient(
            api_key="gemini-test",
            default_model="gemini-2.5-flash",
            max_retries=2,
            retry_initial_delay_seconds=0,
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            response = client.generate(input_text="Hi")

        self.assertEqual(calls["count"], 2)
        self.assertEqual(gemini_output_text(response), "OK")


if __name__ == "__main__":
    unittest.main()
