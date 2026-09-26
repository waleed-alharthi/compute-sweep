"""Source adapters. Each is a generator of Candidates; run.py drives them.

Politeness is deliberate: page budgets and sleeps keep one hour of crawling
per site to a few dozen requests, which is also what keeps the crawl alive.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from urllib.parse import urlencode

from .config import SEARXNG
from .extract import extract_page
from .fetch import FetchError, get
from .model import Candidate, parse_price

# ---------------------------------------------------------------- OpenSooq

OPENSOOQ_COUNTRIES = ("om", "sa", "kw", "bh", "qa", "ae")
OPENSOOQ_CATS = (
    "computers-and-laptops/graphic-cards",
    "computers-and-laptops/laptops-netbooks",
    "computers-and-laptops/desktop-pcs",
    "electronics-appliances/tvs-and-satellites",  # cheap; catches DGX boxes
)
CONDITION_RE = re.compile(r"\b(new|sealed|used|مستعمل|جديد)\b", re.I)


def _next_data(page: str):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
                  page, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _walk_products(obj):
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            t = cur.get("@type")
            if (t == "Product" or ("url" in cur and "price" in cur and "name" in cur)):
                yield cur
            stack.extend(cur.values())
        elif isinstance(cur, str) and cur.lstrip().startswith("{") and '"@type"' in cur:
            try:
                stack.append(json.loads(cur))  # double-encoded state blobs
            except json.JSONDecodeError:
                pass


def opensooq(pages: int = 2, sleep: float = 1.2):
    for country in OPENSOOQ_COUNTRIES:
        for cat in OPENSOOQ_CATS:
            if cat == "electronics-appliances/tvs-and-satellites" and country != "om":
                continue
            for page_no in range(1, pages + 1):
                url = (f"https://{country}.opensooq.com/en/{cat}"
                       + (f"?page={page_no}" if page_no > 1 else ""))
                try:
                    body, rung = get(url)
                except FetchError:
                    break
                state = _next_data(body)
                found = 0
                if state is not None:
                    for item in _walk_products(state):
                        price, cur = None, None
                        offers = item.get("offers")
                        offers = offers[0] if isinstance(offers, list) and offers else offers
                        if isinstance(offers, dict) and offers.get("price"):
                            price, cur = offers["price"], offers.get("priceCurrency")
                        elif item.get("price") not in (None, ""):
                            price, cur = item["price"], item.get("priceCurrency")
                        if price in (None, "") or not item.get("url"):
                            continue
                        desc = str(item.get("description") or "")
                        title = str(item.get("name") or "")
                        cond = CONDITION_RE.search(title + " " + desc[:120])
                        yield Candidate(
                            source="opensooq",
                            ext_id=str(item.get("sku") or
                                       re.search(r"\d+", item["url"]).group(0)
                                       if re.search(r"\d+", item["url"]) else price),
                            url=item["url"], title=title,
                            price=float(price),
                            currency=(cur or "USD").upper(),
                            region=str(item.get("region") or ""), country=country,
                            condition=("used" if cond and cond.group(1).lower()
                                       in ("used", "مستعمل") else
                                       "new" if cond else ""),
                            evidence=rung, meta={"cat": cat})
                        found += 1
                if not found:  # state route failed: fall back to page extraction
                    yield from extract_page(body, url, source="opensooq",
                                            evidence=rung, country=country)
                time.sleep(sleep)

# ---------------------------------------------------------------- Haraj

HARAJ_TERMS = ("haraj rtx 5090", "haraj rtx pro 6000", "haraj dgx spark",
               "haraj rtx 6000", "haraj tenstorrent")


def haraj(sleep: float = 3.0):
    """Haraj's own search is a JS app with no shareable results URL, so the
    item URLs come from the search index and each item page is rendered.
    A price counts only when the page or the snippet states it with a
    currency marker; unsnared "placeholder price" ads stay out."""
    money = re.compile(
        r"([\d][\d,.]{1,12})\s*(?:ريال|ر\.?\s?س|SAR)|"
        r"(?:ريال|ر\.?\s?س|SAR)\s*[:=]?\s*([\d][\d,.]{1,12})", re.I)
    deleted = re.compile(r"العرض محذوف|قديم|محذوف", re.I)
    seen: set[str] = set()
    for term in HARAJ_TERMS:
        for r in _searxng(term):
            url = r.get("url") or ""
            m = re.match(r"https://haraj\.com\.sa/(1\d{9})/", url)
            if not m or m.group(1) in seen:
                continue
            snippet = html.unescape((r.get("title") or "") + " " + (r.get("content") or ""))
            if deleted.search(snippet):  # the index says the ad is gone
                continue
            seen.add(m.group(1))
            title = (r.get("title") or "").strip()
            try:
                body, rung = get(url)
                tm = re.search(r'"@type":"Thing","name":"([^"]+)"', body)
                if tm:
                    title = tm.group(1)
            except FetchError:
                body, rung = "", "serp"
            pm = money.search(body or "") or money.search(snippet)
            amount = None
            if pm:
                pp = parse_price(pm.group(0), "SAR")
                if pp and pp[0] >= 100:
                    amount = pp
            if amount:
                yield Candidate(source="haraj", ext_id=m.group(1), url=url,
                                title=title or url, price=amount[0],
                                currency=amount[1], country="sa",
                                evidence=rung, meta={"term": term})
        time.sleep(sleep)

# ---------------------------------------------------------------- eBay

EBAY_TERMS = ("rtx 5090", "rtx pro 6000 blackwell", "rtx pro 5000 blackwell",
              "dgx spark", "tenstorrent", "jetson agx thor", "mac studio m5 max 128")


def ebay(sleep: float = 6.0):
    # search listing pages answer through the ladder; item pages do not
    for term in EBAY_TERMS:
        url = ("https://www.ebay.com/sch/i.html?" + urlencode(
            {"_nkw": term, "_sop": 15, "LH_PrefLoc": ""}) + "&_ipg=60")
        try:
            body, rung = get(url)
        except FetchError:
            continue
        pairs = re.findall(
            r'href="(https://www\.ebay\.com/itm/\d+)[^"]*"[^>]*>.{0,600}?'
            r'<span[^>]*(?:s-item__title|s-link__title)[^>]*>(?:<span[^>]*>[^<]*</span>)?'
            r'([^<]{8,150})</span>.{0,600}?s-item__price[^>]*>(?:<span[^>]*>)?([^<]+)',
            body, re.S)
        seen = set()
        for item_url, title, price_text in pairs:
            if item_url in seen:
                continue
            seen.add(item_url)
            pp = parse_price(price_text, "USD")
            if not pp:
                continue
            clean = re.sub(r"<[^>]+>", "", title)
            yield Candidate(source="ebay", ext_id=item_url.rsplit("/", 1)[-1],
                            url=item_url, title=clean, price=pp[0],
                            currency=pp[1], country="us",
                            condition="used" if re.search(r"used|pre-?own", clean, re.I) else "",
                            evidence=rung, meta={"term": term})
        time.sleep(sleep)

# ---------------------------------------------------------------- SearXNG:
# dubizzle (Imperva-walled, priced URL slugs) + the dark-alley discovery loop

DUBIZZLE_TERMS = ("rtx 5090 dubizzle", "rtx pro 6000 dubizzle",
                  "dgx spark dubizzle", "mac studio m5 max dubizzle")

DISCOVERY_TERMS = (
    "RTX 5090 for sale", "RTX PRO 6000 Blackwell buy", "RTX PRO 5000 Blackwell price",
    "DGX Spark in stock", "Asus Ascent GX10 buy", "Jetson AGX Thor price",
    "Tenstorrent QuietBox buy", "Tenstorrent Blackhole p150 price",
    "Atlas 350 Ascend 950PR buy", "nvfp4 workstation 96GB price",
    "4x RTX 5090 workstation", "RTX PRO 6000 96GB workstation omr OR aed OR sar",
)

KNOWN_HOSTS = ("opensooq.com", "dubizzle.com", "haraj.com.sa", "ebay.", "amazon.",
               "newegg", "aliexpress", "alibaba", "wikipedia", "reddit", "youtube",
               "redditstatic", "x.com", "twitter", "facebook", "instagram",
               "huggingface", "github", "medium.com", "quora", "pinterest",
               "benchlm", "artificialanalysis", "tomshardware", "anandtech",
               "techpowerup", "videocardz", "theverge", "arstechnica", "cnet",
               "forbes", "ndtv", "gsmarena", "nvidia.com", "apple.com")


def _searxng(query: str, engine: str | None = None, pages: int = 1):
    for page_no in range(1, pages + 1):
        q = {"q": query, "format": "json", "pageno": page_no}
        if engine:
            q["engines"] = engine
        try:
            body, _ = get(SEARXNG + "/search?" + urlencode(q), allow_browser=False)
            data = json.loads(body)
        except Exception:
            return
        yield from data.get("results", [])


def dubizzle(sleep: float = 2.0):
    """Dubizzle pages are Imperva-gated everywhere, so this reads the search
    index instead of the site. Only an explicit AED/د.إ marker in the
    snippet counts as a price - the slug digits and model numbers (a title
    with "5090 32GB" in it priced itself at $50,9032 once) are not prices."""
    money = re.compile(
        r"(?:AED|DHS?|درهم|د\.?\s?إ)\s*[:=]?\s*([\d][\d,.]{1,12})"
        r"|([\d][\d,.]{1,12})\s*(?:AED|DHS?|درهم|د\.?\s?إ)", re.I)
    for term in DUBIZZLE_TERMS:
        for r in _searxng(term):
            url = r.get("url") or ""
            if "dubizzle.com" not in url or "/classified/" not in url:
                continue
            text = html.unescape((r.get("title") or "") + " | " + (r.get("content") or ""))
            m = money.search(text)
            if not m:
                continue
            pp = parse_price(m.group(0), "AED")
            if not pp or pp[0] < 100:
                continue
            yield Candidate(source="dubizzle",
                            ext_id=hashlib.sha1(url.encode()).hexdigest()[:12],
                            url=url, title=r.get("title") or "",
                            price=pp[0], currency=pp[1], country="ae",
                            evidence="serp", meta={"engine": "searxng"})
        time.sleep(sleep)


def discovery(max_new_pages: int = 25, sleep: float = 2.0, rotate: bool = True):
    """The dark alleys: search every catalog term, and for each hit on a
    domain we do not already own, load the page and extract any product."""
    try:
        day = int(time.time() // 86400)
    except Exception:
        day = 0
    terms = list(DISCOVERY_TERMS)
    if rotate:
        terms = terms[day % len(terms):] + terms[:day % len(terms)]
    tried: set[str] = set()
    loaded = 0
    for term in terms:
        for r in _searxng(term, pages=2):
            url = r.get("url") or ""
            host = re.sub(r"^www\.", "", url.split("/")[2] if "//" in url else "")
            if not host or any(k in host for k in KNOWN_HOSTS) or host in tried:
                continue
            tried.add(host)
            if loaded >= max_new_pages:
                return
            loaded += 1
            try:
                body, rung = get(url)
            except FetchError:
                continue
            got = extract_page(body, url, evidence=rung)
            yield from got
            time.sleep(sleep)

# ---------------------------------------------------------------- curated retail

RETAIL_PAGES = (
    ("tenstorrent", "https://tenstorrent.com/en/hardware/cards"),
    ("tenstorrent", "https://tenstorrent.com/hardware/tt-quietbox"),
    ("computahardware", "https://computahardware.com/brand/tenstorrent/"),
)


def retail():
    for source, url in RETAIL_PAGES:
        try:
            body, rung = get(url)
        except FetchError:
            continue
        yield from extract_page(body, url, source=source, evidence=rung)
        time.sleep(1.0)
