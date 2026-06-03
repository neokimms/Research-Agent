from __future__ import annotations

import logging
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar


T = TypeVar("T")

RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RetryConfig:
    attempts: int = 3
    initial_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0
    backoff_factor: float = 2.0


def retry_call(
    operation: Callable[[], T],
    *,
    label: str,
    logger: logging.Logger,
    config: RetryConfig = RetryConfig(),
    retryable: Callable[[Exception], bool] | None = None,
) -> T:
    attempts = max(1, config.attempts)
    delay = max(0.0, config.initial_delay_seconds)
    should_retry = retryable or is_retryable_exception
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts or not should_retry(exc):
                raise
            logger.warning(
                "%s failed; retrying",
                label,
                extra={
                    "stage": "retry",
                    "attempt": attempt,
                    "max_attempts": attempts,
                    "delay_seconds": delay,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            if delay > 0:
                time.sleep(delay)
            delay = min(config.max_delay_seconds, delay * config.backoff_factor if delay else config.initial_delay_seconds)
    raise RuntimeError(f"{label} retry loop exhausted")


def is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_STATUS
    return isinstance(exc, (urllib.error.URLError, TimeoutError))
