"""Polite HTTP client for the KEIRIN.JP internal JSON API.

All endpoints live behind a single URL (``/pc/json``) and are selected with the
``type`` query parameter (e.g. ``JSJ057``). See ``docs/keirin-jp-api.md``.

robots.txt on keirin.jp does not allow this path for crawlers, so the client
is deliberately conservative: one request at a time, a minimum interval
between requests, an identifying User-Agent, few retries, and no retry at all
on client errors such as 403.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

BASE_URL = "https://keirin.jp/pc/json"
USER_AGENT = (
    "keirin-platform/0.1 (personal use; +https://github.com/keirin-platform/keirin-platform)"
)

log = logging.getLogger(__name__)


class KeirinApiError(RuntimeError):
    """Raised when the API cannot be reached or returns something unusable."""


class KeirinClient:
    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff: float = 5.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.request_count = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> KeirinClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, type_: str, **params: str) -> dict[str, Any]:
        """Call one endpoint and return the decoded JSON object."""
        query = {"type": type_, **params}
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            try:
                resp = self._http.get(BASE_URL, params=query)
            except httpx.TransportError as e:
                error: str = f"{type(e).__name__}: {e}"
            else:
                self.request_count += 1
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        error = "response is not JSON"
                    else:
                        if isinstance(body, dict):
                            return body
                        error = "response is not a JSON object"
                elif resp.status_code == 429 or resp.status_code >= 500:
                    error = f"HTTP {resp.status_code}"
                else:
                    # 403 etc. likely means we are blocked; stop immediately.
                    raise KeirinApiError(f"{type_}: HTTP {resp.status_code}")
            if attempt == self._max_retries:
                raise KeirinApiError(f"{type_}: {error} (gave up after {attempt} attempts)")
            wait = self._backoff * 2 ** (attempt - 1)
            log.warning("%s: %s, retrying in %.0fs", type_, error, wait)
            self._sleep(wait)
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            remaining = self._min_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last_request_at = now
