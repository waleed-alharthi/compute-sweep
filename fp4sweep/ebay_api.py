"""eBay Browse API (official, no walls) once ~/.config/fp4sweep/ebay.json
holds {"app_id": ..., "cert_id": ...} from the approved developer account.

The app token is an hour-long client-credential; we cache it in state and
re-mint 5 minutes before expiry. Rate ceiling is 5 req/s, we never get close.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import CONFIG_DIR, STATE

CREDS = CONFIG_DIR / "ebay.json"
TOKEN_PATH = STATE / "ebay_token.json"
GPU_CATEGORY = "27386"  # Graphics Cards

# "env": "sandbox" in ebay.json points at api.sandbox.ebay.com (test data
# only); production keys use the default. NOTE (verified live 2026-10-16):
# the token endpoint is /identity/v1/oauth2/token on BOTH envs - the older
# documented /client_token path returns Tomcat 404. Scope param is required
# on production, must be OMITTED on sandbox.
BASES = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}


class EbayAPIError(Exception):
    pass


def available() -> bool:
    return CREDS.exists()


def _env() -> str:
    try:
        return json.loads(CREDS.read_text()).get("env", "production")
    except (OSError, ValueError):
        return "production"


def _base() -> str:
    return BASES.get(_env(), BASES["production"])


def _creds() -> tuple[str, str]:
    d = json.loads(CREDS.read_text())
    return d["app_id"], d["cert_id"]


def _token() -> str:
    app_id, cert_id = _creds()
    env = _env()
    token_path = TOKEN_PATH if env == "production" else TOKEN_PATH.with_suffix(f".{env}.json")
    try:
        cached = json.loads(token_path.read_text())
        if cached.get("expiry", 0) > time.time() + 300:
            return cached["access_token"]
    except (OSError, ValueError, KeyError):
        pass
    params = {"grant_type": "client_credentials"}
    token_url = _base() + "/identity/v1/oauth2/token"
    if env == "production":  # sandbox REJECTS the scope param (invalid_scope)
        params["scope"] = f"{_base()}/oauth/api_scope"
    body = urllib.parse.urlencode(params).encode()
    basic = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    req = urllib.request.Request(token_url, data=body, headers={
        "content-type": "application/x-www-form-urlencoded",
        "authorization": f"Basic {basic}",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        tok = json.loads(resp.read())
    tok["expiry"] = time.time() + int(tok.get("expires_in", 3600))
    STATE.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(tok))
    return tok["access_token"]


def search(term: str, pages: int = 2, category: str | None = GPU_CATEGORY,
           limit: int = 100):
    """Yield raw itemSummary dicts for a term, cheapest first (incl shipping)."""
    tok = _token()
    url = _base() + "/buy/browse/v1/item_summary/search?" + urllib.parse.urlencode(
        {"q": term, "sort": "pricePlusShippingCostsAscending", "limit": limit,
         **({"categoryIds": category} if category else {})})
    headers = {"authorization": f"Bearer {tok}",
               "x-ebay-marketplace-id": "EBAY-US"}
    next_url = url
    for _ in range(pages):
        if not next_url:
            return
        req = urllib.request.Request(next_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise EbayAPIError(f"http {e.code}: {detail}") from None
        for item in data.get("itemSummaries", []):
            yield item
        next_url = (data.get("hrefs") or {}).get("next", {}).get("href")
        time.sleep(0.4)
