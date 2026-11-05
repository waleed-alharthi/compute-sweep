"""Item-page check: open the offer a buyer would click before it reaches
the board.

The search card and the title are all the legitimacy gate ever saw. That
let through two things a buyer spots in seconds on the item page:
  - the card quotes the CHEAPEST option of a multi-SKU listing (DHgate's
    "128GB 2TB" mini PC at $1,888 is the 64GB no-SSD option; the 128GB one
    is $3,155), and
  - a brand-new store with zero sales selling a DGX Spark 30% under the
    street ("0 sold", 88 followers, a different seller's name in the title).

So for marketplace rows that would be shown, the page is opened (Camoufox)
and:
  - a delisted page drops the row;
  - if the options carry memory sizes, the option matching the title's
    memory is selected and ITS price replaces the card price; no matching
    option drops the row;
  - an unproven seller (AliExpress item sold 0, DHgate store < 10 orders,
    Alibaba store rating from < 10 buyers) priced under the family's median
    $/GB is dropped - too good, from nobody.
Parsers are pure functions over HTML so they are tested on real captures.
"""
from __future__ import annotations

import re
import statistics
import time

SOURCES = ("aliexpress", "dhgate", "alibaba")
FRESH_S = 3 * 86400        # a check holds this long while the card price holds
MIN_DHGATE_ORDERS = 10
MIN_ALIBABA_RATINGS = 10

_GB = re.compile(r"(\d{2,3})\s*G(?:B)?\b", re.I)


def _text(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _num(s: str | None) -> float | None:
    """'1,234' / '5K+' / '1.2万' -> number."""
    if not s:
        return None
    m = re.search(r"([\d.,]+)\s*([KkMm万]?)", s)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return v * {"k": 1e3, "m": 1e6, "万": 1e4}.get(m.group(2).lower(), 1)


def _cls(html: str, name: str) -> str | None:
    m = re.search(r'class="[^"]*' + re.escape(name) + r'[^"]*"[^>]*>(.*?)</(?:span|div|a|b)',
                  html, re.S)
    return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else None


# ------------------------------------------------------------- parsers

def parse_aliexpress(html: str) -> dict:
    t = _text(html)
    if re.search(r"page you requested can not be found|لم يتم العثور على الصفحة", t, re.I):
        return {"dead": True}
    sold = _cls(html, "reviewer--sold")   # absent when the item never sold
    pos = re.search(r"([\d.]+)%\s*(?:تقييم إيجابي|positive)", t, re.I)
    fol = re.search(r"([\d,.]+[KkMm]?)\s*(?:متابعون|متابع|followers)", t, re.I)
    return {"dead": False,
            "sold": int(_num(sold) or 0),
            "store": _cls(html, "store-detail--storeName"),
            "positive": float(pos.group(1)) if pos else None,
            "followers": _num(fol.group(1)) if fol else None,
            "options": re.findall(r'class="sku-item--[^"]*"[^>]*title="([^"]+)"', html),
            "price": _cls(html, "price-default--current") or _cls(html, "price--current")}


def parse_dhgate(html: str) -> dict:
    t = _text(html)
    if not _dh_price(html) and re.search(
            r"(?:this item|product) (?:is no longer available|has been removed)", t, re.I):
        return {"dead": True}  # reviews can say it too; a live page has a price
    m = re.search(r"([\d.]+)%\s*Positive Feedback\s*([\d,]+)\s*Orders?", t, re.I)
    opts = re.findall(r'"attrValName\\?":\\?"([^"\\]+)', html)
    return {"dead": False,
            "positive": float(m.group(1)) if m else None,
            "orders": int(m.group(2).replace(",", "")) if m else 0,
            "options": list(dict.fromkeys(opts)),
            "price": _dh_price(html)}


def _dh_price(html: str) -> str | None:
    """The bold per-piece price; class names are hashed, so fall back to
    the first "US $x / Piece" in the text."""
    m = re.search(r'class="d7JTIMnD[^"]*">([^<]+)', html)
    if m:
        return m.group(1)
    m = re.search(r"(US \$[\d,]+(?:\.\d+)?)\s*/ Piece", _text(html))
    return m.group(1) if m else None


def parse_alibaba(html: str) -> dict:
    t = _text(html)
    # "This product is no longer available" also turns up in buyer reviews
    # of OTHER products on the store strip; a live page has its attributes
    if "Key attributes" not in t and re.search(
            r"product (?:is|has been) (?:no longer available|removed|offline)", t, re.I):
        return {"dead": True}
    r = re.search(r"([\d.]+)/5\s*\(\s*([\d,]+)\s*\)\s*Store rating", t)
    y = re.search(r"\b(\d{1,2})\s*yrs\b", t)
    return {"dead": False,
            "rating": float(r.group(1)) if r else None,
            "ratings": int(r.group(2).replace(",", "")) if r else 0,
            "years": int(y.group(1)) if y else None}


PARSERS = {"aliexpress": parse_aliexpress, "dhgate": parse_dhgate,
           "alibaba": parse_alibaba}


def option_for(options: list[str], gb: int) -> tuple[list[str], bool]:
    """Options naming the title's memory (US plug first), and whether the
    card price is ambiguous: the options name a memory size other than the
    title's (several sizes, or only a smaller one). Storage sizes (TB, or GB >= 256 next to
    SSD) are not memory."""
    sizes = {}
    for o in options:
        mem = [int(g) for g in _GB.findall(re.sub(r"\d+\s*TB|\d+\s*G?B?\s*SSD", "", o, flags=re.I))
               if 16 <= int(g) <= 512]
        sizes[o] = mem
    distinct = {m for v in sizes.values() for m in v}
    if not distinct or distinct == {gb}:
        return [], False  # no memory in the options, or only the title's
    hits = [o for o, v in sizes.items() if gb in v]
    hits.sort(key=lambda o: (not o.upper().startswith("US"), o))
    return hits, True


def proven(source: str, f: dict) -> bool:
    if source == "aliexpress":
        return f.get("sold", 0) > 0
    if source == "dhgate":
        return f.get("orders", 0) >= MIN_DHGATE_ORDERS
    if source == "alibaba":
        return f.get("ratings", 0) >= MIN_ALIBABA_RATINGS
    return True


def judge(source: str, f: dict, per_gb: float | None, median: float | None,
          option_usd: float | None, option_needed: bool) -> tuple[bool, str]:
    if f.get("dead"):
        return False, "delisted"
    if option_needed and not option_usd:
        return False, "no option with the title's memory; card price is another SKU"
    if not proven(source, f) and per_gb and median and per_gb < median:
        who = {"aliexpress": f"{f.get('sold', 0)} sold",
               "dhgate": f"store {f.get('orders', 0)} orders",
               "alibaba": f"store rated by {f.get('ratings', 0)}"}[source]
        return False, f"unproven seller ({who}) under the family median ${median:.0f}/GB"
    return True, "page ok"


# ------------------------------------------------------------- browser side

def _money_usd(store, s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"(US\s*\$|\$|OMR|AED|SAR|EUR|€|£)\s*([\d,]+(?:\.\d+)?)", s)
    if not m:
        return None
    sym = m.group(1)
    cur = "USD" if "$" in sym else {"€": "EUR", "£": "GBP"}.get(sym, sym)
    return store.to_usd(float(m.group(2).replace(",", "")), cur)


def _select(page, source: str, option: str) -> str | None:
    """Pick an option on the live page; return the price it shows."""
    if source == "dhgate":
        page.evaluate("""() => { const b = document.querySelector(
            '[spm-c="sku"] div[style*="cursor"]'); if (b) b.click(); }""")
        page.wait_for_timeout(700)
        page.evaluate("""(o) => { const li = document.querySelector(
            '[spm-c="sku"] li[spm-index="' + o + '"]'); if (li) li.click(); }""", option)
    else:
        page.evaluate("""(o) => { const el = [...document.querySelectorAll(
            '[class*="sku-item--"]')].find(e => e.getAttribute('title') === o);
            if (el) el.click(); }""", option)
    page.wait_for_timeout(2500)
    f = PARSERS[source](page.content())
    return f.get("price")


def check_row(store, page_factory, row, median: float | None) -> tuple[bool, str, float | None]:
    page = page_factory()
    try:
        page.goto(row["url"], timeout=60000, wait_until="load")
        page.wait_for_timeout(7000)
        f = PARSERS[row["source"]](page.content())
        option_usd, needed = None, False
        unit_gb = (row["vram"] or 0) // max(row["units"] or 1, 1)
        if not f.get("dead") and f.get("options"):
            hits, needed = option_for(f["options"], unit_gb)
            if needed and hits:
                option_usd = _money_usd(store, _select(page, row["source"], hits[0]))
    finally:
        page.close()
    per_gb = row["landed_per_gb"]
    if option_usd and row["usd"]:
        per_gb = per_gb * option_usd / row["usd"]
    ok, why = judge(row["source"], f, per_gb, median, option_usd, needed)
    note = why + "".join(f" {k}={f[k]}" for k in ("sold", "orders", "ratings", "store")
                         if f.get(k) not in (None, ""))
    return ok, note, option_usd


def medians(active) -> dict[str, float]:
    by = {}
    for r in active:
        if r["verdict"] == "yes" and r["landed_per_gb"]:
            by.setdefault(r["catalog_id"], []).append(r["landed_per_gb"])
    return {k: statistics.median(v) for k, v in by.items() if len(v) >= 3}


def run(store, board, page_factory, budget: int = 60) -> tuple[int, int]:
    """Check unchecked marketplace rows the board would show, re-deriving
    the board after each rejection (the next-cheapest row moves up), until
    it is all checked or the page budget is spent. Returns (ok, dropped)."""
    ok = bad = 0
    now = time.time()
    while budget > 0:
        active = store.active()
        med = medians(active)
        todo = [r for r in board(active)
                if r["source"] in SOURCES
                and not (r["page_ts"] and now - r["page_ts"] < FRESH_S)]
        if not todo:
            break
        for r in todo[:budget]:
            budget -= 1
            try:
                good, note, usd = check_row(store, page_factory, r, med.get(r["catalog_id"]))
            except Exception as exc:  # a page that will not load stays unchecked
                print(f"  [pagecheck] {r['key']}: {type(exc).__name__} {str(exc)[:80]}",
                      flush=True)
                store.db.execute("UPDATE listings SET page_ts=? WHERE key=?",
                                 (now - FRESH_S + 6 * 3600, r["key"]))  # retry in 6h
                store.db.commit()
                continue
            store.set_page(r["key"], good, note, usd)
            ok += good
            bad += not good
            if not good:
                print(f"  [pagecheck] drop {r['key']}: {note}", flush=True)
                break  # the board changed: re-derive it
    return ok, bad
