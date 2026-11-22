"""Shared HTTP client: browser-like headers, timeouts, retries with backoff, and polite rate limiting."""

from __future__ import annotations

import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
DEFAULT_TIMEOUT = 25


class SourceUnavailable(RuntimeError):
    """A data source could not be reached or returned unusable data."""


def make_session(min_interval: float = 0.4) -> RateLimitedSession:
    session = RateLimitedSession(min_interval)
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,text/csv,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    return session


class RateLimitedSession(requests.Session):
    def __init__(self, min_interval: float) -> None:
        super().__init__()
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        return super().request(method, url, *args, **kwargs)
