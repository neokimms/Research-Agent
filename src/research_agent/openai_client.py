from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .retry import RetryConfig, retry_call


logger = logging.getLogger(__name__)


class OpenAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAIResponsesClient:
    api_key: str
    default_model: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 120
    max_retries: int = 3
    retry_initial_delay_seconds: float = 0.5

    def create(
        self,
        *,
        input_text: str,
        instructions: str,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        text_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "instructions": instructions,
            "input": input_text,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
        if text_format:
            payload["text"] = {"format": text_format}
        if reasoning_effort:
            payload["reasoning"] = {"effort": reasoning_effort}

        body = json.dumps(payload).encode("utf-8")

        try:
            return retry_call(
                lambda: self._post(body),
                label="openai responses request",
                logger=logger,
                config=RetryConfig(
                    attempts=self.max_retries,
                    initial_delay_seconds=self.retry_initial_delay_seconds,
                ),
            )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OpenAIError(f"OpenAI API error {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            raise OpenAIError(f"OpenAI API request failed: {exc.reason}") from exc

    def _post(self, body: bytes) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))


def output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()
