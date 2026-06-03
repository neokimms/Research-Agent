from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.openai_client import OpenAIResponsesClient, output_text


class OpenAIClientTests(unittest.TestCase):
    def test_create_builds_responses_payload(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"output_text":"OK"}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        client = OpenAIResponsesClient(api_key="sk-test", default_model="gpt-5.4-mini", timeout_seconds=7)
        with patch("urllib.request.urlopen", fake_urlopen):
            response = client.create(
                input_text="Hi",
                instructions="Be brief.",
                tools=[{"type": "web_search"}],
                tool_choice="required",
                reasoning_effort="medium",
            )

        self.assertTrue(captured["url"].endswith("/responses"))
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(captured["body"]["model"], "gpt-5.4-mini")
        self.assertEqual(captured["body"]["tools"], [{"type": "web_search"}])
        self.assertEqual(captured["body"]["tool_choice"], "required")
        self.assertEqual(captured["body"]["reasoning"], {"effort": "medium"})
        self.assertEqual(output_text(response), "OK")

    def test_create_retries_transient_http_errors(self) -> None:
        calls = {"count": 0}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"output_text":"OK"}'

        def fake_urlopen(request, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b"rate limited"))
            return FakeResponse()

        client = OpenAIResponsesClient(
            api_key="sk-test",
            default_model="gpt-5.4-mini",
            max_retries=2,
            retry_initial_delay_seconds=0,
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            response = client.create(input_text="Hi", instructions="Be brief.")

        self.assertEqual(calls["count"], 2)
        self.assertEqual(output_text(response), "OK")


if __name__ == "__main__":
    unittest.main()
