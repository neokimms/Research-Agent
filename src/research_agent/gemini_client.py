from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .retry import RetryConfig, retry_call


logger = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeminiGenerateClient:
    api_key: str
    default_model: str
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: int = 120
    max_retries: int = 3
    retry_initial_delay_seconds: float = 0.5

    def generate(
        self,
        *,
        input_text: str,
        instructions: str = "",
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_model = urllib.parse.quote(model or self.default_model, safe="")
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": input_text}],
                }
            ]
        }
        if instructions:
            payload["systemInstruction"] = {"parts": [{"text": instructions}]}
        if tools:
            payload["tools"] = tools
        if response_schema:
            payload["generationConfig"] = {
                "responseFormat": {
                    "text": {
                        "mimeType": "application/json",
                        "schema": response_schema,
                    }
                }
            }

        body = json.dumps(payload).encode("utf-8")

        try:
            return retry_call(
                lambda: self._post(selected_model, body),
                label="gemini generate request",
                logger=logger,
                config=RetryConfig(
                    attempts=self.max_retries,
                    initial_delay_seconds=self.retry_initial_delay_seconds,
                ),
            )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise GeminiError(f"Gemini API error {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise GeminiError(f"Gemini API request failed: {exc.reason}") from exc

    def _post(self, selected_model: str, body: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/models/{selected_model}:generateContent",
            data=body,
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def gemini_output_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in response.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content", {})
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "\n".join(chunks).strip()
