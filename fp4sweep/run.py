"""fp4sweep: crawl the markets for >=24GB accelerators, score by landed $/VRAM GB in Oman.

usage:
  python3 -m fp4sweep.run crawl [--only ADAPTER[,ADAPTER]]
  python3 -m fp4sweep.run export
  python3 -m fp4sweep.run status
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import adapters
from .config import EXPORT_PATH
from . import verify
from .fetch import FetchError, _direct, close_stealth
from .store import Store, connect, load_catalog, refresh_fx

# Removed 2026-10: discovery (generic web pages - articles and store fronts
# ranked as listings), dubizzle (SERP snippets, no page behind them),
# craigslist + kijiji (local pickup only: nothing there can reach Oman).
ADAPTERS = {
    "opensooq": adapters.opensooq,
    "haraj": adapters.haraj,
    "ebay": adapters.ebay,
    "aliexpress_scrape": adapters.aliexpress_scrape,
    "aliexpress": adapters.aliexpress,
    "dhgate": adapters.dhgate,
    "alibaba": adapters.alibaba,
    "yahoojp": adapters.yahoojp,
    "kleinanzeigen": adapters.kleinanzeigen,
    "indiamart": adapters.indiamart,
    "marktplaats": adapters.marktplaats,
    "olx": adapters.olx,
    "amazon": adapters.amazon,
    "retail": adapters.retail,
}

def _delta7(series: list) -> float | None:
    """% change across the last 7 daily points, oldest non-null vs newest."""
    recent = [v for v in series[-7:] if v is not None]
    if len(recent) < 2:
        return None
    old, new = recent[0], recent[-1]
    if not old:
        return None
    return round((new - old) / old * 100, 1)


def _diverse(rows, n: int, per_key: int = 2) -> list:
    """The cheapest n listings, but at most per_key per (product, source):
    ten identical V100 offers from one marketplace say less than the cheapest
    two from each place that has them."""
    out, seen = [], {}
    for r in rows:
        if r["verdict"] != "yes":
            continue
        k = (r["product"], r["source"])
        if seen.get(k, 0) >= per_key:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(r)
        if len(out) >= n:
            break
    return out


def _fit(gb: int) -> str:
    """Can one unit run the ~94GB int4 model + KV? From the master sheet."""
    units = -(-105 // gb) if gb else 99
    return "1u" if units <= 1 else f"x{units}"


def crawl(db, only: str | None):
    catalog = load_catalog()
    store = Store(db)
    refresh_fx(db, _direct)
    names = only.split(",") if only else list(ADAPTERS)
    counts: dict[str, int] = {}
    for name in names:
        fn = ADAPTERS.get(name)
        if not fn:
            print(f"unknown adapter {name}", file=sys.stderr)
            continue
        n = 0
        try:
            for cand in fn():
                try:
                    store.upsert(cand, catalog)
                    n += 1
                except Exception as exc:  # one bad candidate must not stop a run
                    print(f"[{name}] store error: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"[{name}] aborted: {exc}", file=sys.stderr)
        counts[name] = n
        print(f"[{name}] {n} candidates")
    return counts


def export(db):
    store = Store(db)
    now = time.time()
    active = store.active()
    news = store.news(48)
    drops = store.drops(168)
    unknown = store.unknown()
    catalog = {e["id"]: e for e in load_catalog()}
    trends = store.product_trends(14)

    def _row(r, now=None) -> dict:
        now = now or time.time()
        t = trends.get(r["product"]) or []
        row = {
            "src": r["source"], "title": r["title"][:90], "url": r["url"],
            "usd": r["usd"], "price": r["price"], "cur": r["currency"],
            "gb": r["vram"], "product": r["product"], "fp4": r["fp4"],
            "per_gb": r["landed_per_gb"], "list_per_gb": r["usd_per_gb"],
            "landed": r["landed_usd"], "ship": r["ship"],
            "units": r["units"], "verdict": r["verdict"],
            "needs": r["needs"] or "",
            "make": r["make"] or "", "model": r["model"] or "",
            "vram_unit": r["vram_unit"],
            "cond": r["condition"],
            "country": r["country"], "region": r["region"],
            "age_h": round((now - (r["first_seen"] or now)) / 3600, 1),
            "chg_h": round((now - (r["last_price_change"] or now)) / 3600, 1),
            "bw": catalog.get(r["catalog_id"] or "", {}).get("bw"),
            "t7": t[-7:], "d7": _delta7(t),
        }
        return row

    street = []
    for r in active:
        p = r["product"]
        if p in [s["product"] for s in street]:
            continue
        gb = r["vram"] or 0
        t = trends.get(p) or []
        street.append({
            "product": p, "gb": gb,
            "bw": catalog.get(r["catalog_id"] or "", {}).get("bw"),
            "fp4": r["fp4"], "fit": _fit(gb),
            "min_usd": r["landed_usd"], "per_gb": r["landed_per_gb"],
            "n": sum(1 for a in active if a["product"] == p),
            "t7": t[-7:], "d7": _delta7(t),
        })
    street.sort(key=lambda s: s["per_gb"] or 9e9)

    payload = {
        "generated": now,
        "stats": {
            "active": len(active),
            "new_48h": len(news),
            "drops_7d": len(drops),
            "unknown": len(unknown),
        },
        "news": [_row(r, now) for r in news[:20]],
        "drops": [dict(_row(r, now), drop_pct=pct) for r, pct in drops[:10]],
        "best": [_row(r, now) for r in _diverse(active, 40)],
        "street": street,
        "unknown": [{"title": r["title"][:80], "src": r["source"],
                     "usd": r["usd"], "url": r["url"]} for r in unknown[:20]],
    }
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXPORT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(EXPORT_PATH)
    return payload


def status(db):
    tot = db.execute("SELECT COUNT(*) c, SUM(matched) m FROM listings").fetchone()
    last = db.execute("SELECT MAX(last_seen) t FROM listings").fetchone()["t"]
    print(f"listings: {tot['c']} total, {tot['m'] or 0} matched")
    print(f"last sighting: {time.strftime('%F %T', time.localtime(last)) if last else 'never'}")
    print(f"export: {EXPORT_PATH}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fp4sweep")
    ap.add_argument("cmd", choices=["crawl", "export", "status", "verify"])
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    db = connect()
    if args.cmd == "crawl":
        try:
            crawl(db, args.only)
        finally:
            close_stealth()
        ok, bad = verify.run(Store(db))
        print(f"[verify] {ok} real, {bad} rejected")
        print(f"[sightings] {Store(db).record_sightings()} rows for today")
        export(db)
    elif args.cmd == "verify":
        ok, bad = verify.run(Store(db), max_batches=40)
        print(f"[verify] {ok} real, {bad} rejected")
        export(db)
    elif args.cmd == "export":
        export(db)
    else:
        status(db)


if __name__ == "__main__":
    main()
