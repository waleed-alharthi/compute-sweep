"""AliExpress Open Platform (affiliate product search), gated on
~/.config/fp4sweep/aliexpress.json:
  {"app_key": "...", "app_secret": "...", "gateway": "https://api-sg.aliexpress.com/rest"}

The platform is TOP-lineage: one endpoint, always POST form-encoded, common
params (method/app_key/timestamp/v/format/sign_method) plus a sign over the
sorted params. gateway defaults have moved over the years, so the file can
pin one; the md5 and hmac variants are both accepted.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac as _hmac
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CONFIG_DIR, STATE

CREDS = CONFIG_DIR / "aliexpress.json"
DEFAULT_GATEWAY = "https://api-sg.aliexpress.com/rest"


class AliExpressError(Exception):
    pass


def available() -> bool:
    return CREDS.exists()


def _creds() -> dict:
    return json.loads(CREDS.read_text())


def _sign(params: dict, secret: str, method: str) -> str:
    ordered = "".join(f"{k}{params[k]}" for k in sorted(params))
    if method == "hmac":
        digest = _hmac.new(secret.encode(), ordered.encode(), hashlib.sha256)
        return digest.hexdigest().upper()
    return hashlib.md5(f"{secret}{ordered}{secret}".encode()).hexdigest().upper()


def execute(api_method: str, **biz) -> dict:
    cfg = _creds()
    secret = cfg["app_secret"]
    params = {
        "method": api_method,
        "app_key": cfg["app_key"],
        "timestamp": dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "v": "2.0",
        "format": "json",
        "sign_method": cfg.get("sign_method", "md5"),
        **{k: str(v) for k, v in biz.items() if v is not None},
    }
    params["sign"] = _sign(params, secret, params["sign_method"])
    req = urllib.request.Request(
        cfg.get("gateway", DEFAULT_GATEWAY),
        data=urllib.parse.urlencode(params).encode(),
        headers={"content-type": "application/x-www-form-urlencoded;charset=utf-8"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        out = json.loads(resp.read())
    err = out.get("error_response")
    if err:
        raise AliExpressError(f"{err.get('code')}: {str(err.get('msg'))[:120]}")
    return out


def _dig(node, key: str):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for v in node.values():
            found = _dig(v, key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _dig(v, key)
            if found is not None:
                return found
    return None


def product_query(keyword: str, pages: int = 1, page_size: int = 20,
                  currency: str = "USD"):
    for page in range(1, pages + 1):
        out = execute("aliexpress.affiliate.product.query",
                      keyword=keyword, current_page=page, page_size=page_size,
                      target_currency=currency, target_language="EN")
        results = _dig(out, "results")
        if results is None:
            return
        if isinstance(results, dict):
            results = results.get("aliexpress_adub_product_dto", []) or \
                list(results.values())
        for item in results or []:
            yield item
        time.sleep(0.5)
