"""Tiny resilient HTTP-JSON helper (retries + timeout) shared by all API clients."""
from __future__ import annotations

from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "egig-paperbot/0.1 (research)"})


class HttpError(Exception):
    pass


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=0.4, max=4.0),
    retry=retry_if_exception_type((requests.RequestException, HttpError)),
    reraise=True,
)
def get_json(url: str, params: Optional[dict] = None, timeout: float = 8.0) -> Any:
    r = _SESSION.get(url, params=params, timeout=timeout)
    if r.status_code != 200:
        raise HttpError(f"HTTP {r.status_code} for {r.url}")
    return r.json()
