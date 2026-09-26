"""The fetch ladder: plain HTTP -> Browserless Chrome -> give up.

Every wall is a classification problem, not a verdict: get() reports which
rung answered so evidence level travels with the bytes. The SERP-snippet
route is not here: snippets carry no page body, so adapters that fall back
to search-index data (dubizzle) mark their own evidence when they do it.
"""
from __future__ import annotations

import json
import urllib.request

from .config import BROWSERLESS, UA

# rung names, best evidence first
DIRECT = "direct"
BROWSER = "browser"


class FetchError(Exception):
    pass


def _direct(url: str, timeout: float = 25.0, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"user-agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return body.decode("utf-8", "replace")


def _browserless(url: str, timeout_ms: int = 25000) -> str:
    payload = {"url": url, "gotoOptions": {"timeout": timeout_ms,
                                           "waitUntil": "domcontentloaded"}}
    req = urllib.request.Request(
        f"{BROWSERLESS}/content", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_ms / 1000 + 15) as resp:
        return resp.read().decode("utf-8", "replace")


def get(url: str, allow_browser: bool = True,
        headers: dict | None = None) -> tuple[str, str]:
    """Return (body, rung). Raises FetchError when no rung answered."""
    try:
        return _direct(url, headers=headers), DIRECT
    except Exception as direct_exc:
        if not allow_browser:
            raise FetchError(f"{url}: {direct_exc}") from direct_exc
    try:
        return _browserless(url), BROWSER
    except Exception as bl_exc:
        raise FetchError(f"{url}: direct and browser both failed") from bl_exc
