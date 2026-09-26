"""Page extraction: JSON-LD Products first, meta tags as fallback."""
from __future__ import annotations

import hashlib
import html as htmllib
import json
import re
from urllib.parse import urlsplit

from .model import Candidate, parse_price

LD_RE = re.compile(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", re.S | re.I)
META_RE = re.compile(r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I)


def _ld_blobs(page: str):
    for m in LD_RE.finditer(page):
        blob = htmllib.unescape(m.group(1)).strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        yield data


def walk_types(obj, wanted: set[str]):
    """Yield every dict in a JSON-LD graph whose @type is wanted."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            types = cur.get("@type")
            if types:
                types = types if isinstance(types, list) else [types]
                if any(t.lower() in wanted for t in types):
                    yield cur
            stack.extend(cur.values())


def _num(v):
    try:
        return float(re.sub(r"[^\d.]", "", str(v)))
    except (TypeError, ValueError):
        return None


def _offers(item) -> tuple[float, str] | tuple[None, None]:
    offers = item.get("offers")
    offers = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offers, dict):
        price = _num(offers.get("price") or offers.get("lowPrice"))
        cur = (offers.get("priceCurrency") or "").upper() or None
        if price:
            return price, cur
    price = _num(item.get("price"))
    if price:
        cur = (item.get("priceCurrency") or "").upper() or None
        return price, cur
    return None, None


CURRENCY_TOKEN = (r"(?:USD|EUR|OMR|SAR|AED|KWD|BHD|QAR|CNY|\u0024|\u20ac|\u00a3)"
                  r"\s?[\d][\d,]*(?:\.\d+)?|[\d][\d,]*(?:\.\d+)?\s?(?:USD|EUR|OMR|SAR|AED)")
CARD_RE = re.compile(
    r"<h[2-4][^>]*>(?:<[^>]+>)*([^<>]{3,80}?)(?:<[^>]+>)*</h[2-4]>"
    r"(.{0,600}?)(" + CURRENCY_TOKEN + r")", re.S | re.I)


def _card_scan(page: str, base_url, source, evidence, default_currency, country, out):
    """Retail pages with no structured data: <h4>name</h4> ... $price."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    for m in CARD_RE.finditer(text):
        title = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()
        price = parse_price(m.group(3), default_currency)
        if not price or len(title) < 3:
            continue
        key = base_url.split("#")[0] + "#" + title
        if key in out:
            continue
        cid = hashlib.sha1(key.encode()).hexdigest()[:12]
        out[key] = Candidate(
            source=source or urlsplit(base_url).netloc.replace("www.", ""),
            ext_id=cid, url=base_url, title=title,
            price=price[0], currency=price[1], country=country,
            evidence=evidence, meta={"mode": "card"})


def extract_page(page: str, base_url: str, source: str | None = None,
                 evidence: str = "direct", default_currency: str | None = None,
                 country: str = "") -> list[Candidate]:
    """Candidates found anywhere in one page. Deduped by url per page."""
    host = urlsplit(base_url).netloc.replace("www.", "")
    out: dict[str, Candidate] = {}

    def add(url, title, price, cur, condition="", stock="", region=""):
        if not title or price is None:
            return
        url = url or base_url
        key = url.rstrip("/")
        if key in out:
            return
        cid = hashlib.sha1(key.encode()).hexdigest()[:12]
        out[key] = Candidate(
            source=source or host, ext_id=cid, url=key,
            title=re.sub(r"\s+", " ", htmllib.unescape(title)).strip()[:200],
            price=float(price), currency=(cur or default_currency or "").upper() or None,
            region=region, country=country, condition=condition, stock=stock,
            evidence=evidence, meta={"host": host})

    for data in _ld_blobs(page):
        for item in walk_types(data, {"product"}):
            price, cur = _offers(item)
            offers = item.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            availability = str(offers.get("availability", "")) if isinstance(offers, dict) else ""
            add(item.get("url") or base_url, item.get("name"),
                price, cur,
                condition="used" if re.search(r"used|second.?hand|مستعمل", str(item.get("description", "")), re.I) else "",
                stock="in_stock" if "InStock" in availability else "",
                region=str(item.get("availableRegion") or item.get("region") or ""))

    if not out:  # meta-tag fallback, structured only: a price with no
                   # anywhere-said-so currency is not a price we can rank
        metas = {m.group(1).lower(): m.group(2) for m in META_RE.finditer(page)}
        title = metas.get("og:title") or metas.get("twitter:title")
        cur = (metas.get("product:price:currency")
               or metas.get("og:price:currency") or "").upper() or None
        price = None
        for k in ("product:price:amount", "og:price:amount", "price"):
            if metas.get(k):
                pp = parse_price(metas[k], cur)
                if pp:
                    price, cur = pp
                    break
        if title and price is not None:
            add(base_url, title, price, cur)

    if not out:
        _card_scan(page, base_url, source, evidence, default_currency, country, out)

    return list(out.values())
