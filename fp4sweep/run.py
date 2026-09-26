"""fp4sweep: crawl the markets for FP4-capable hardware, score by $/VRAM GB.

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
from .fetch import FetchError, _direct
from .store import Store, connect, load_catalog, refresh_fx

ADAPTERS = {
    "opensooq": adapters.opensooq,
    "haraj": adapters.haraj,
    "ebay": adapters.ebay,
    "aliexpress": adapters.aliexpress,
    "dubizzle": adapters.dubizzle,
    "discovery": adapters.discovery,
    "retail": adapters.retail,
}


def _row(r, now=None) -> dict:
    now = now or time.time()
    return {
        "src": r["source"], "title": r["title"][:90], "url": r["url"],
        "usd": r["usd"], "price": r["price"], "cur": r["currency"],
        "gb": r["vram"], "product": r["product"], "fp4": r["fp4"],
        "per_gb": r["usd_per_gb"], "cond": r["condition"],
        "country": r["country"], "region": r["region"],
        "age_h": round((now - (r["first_seen"] or now)) / 3600, 1),
        "chg_h": round((now - (r["last_price_change"] or now)) / 3600, 1),
    }


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
        "best": [_row(r, now) for r in active[:20]],
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
    ap.add_argument("cmd", choices=["crawl", "export", "status"])
    ap.add_argument("--only")
    args = ap.parse_args(argv)
    db = connect()
    if args.cmd == "crawl":
        crawl(db, args.only)
        export(db)
    elif args.cmd == "export":
        export(db)
    else:
        status(db)


if __name__ == "__main__":
    main()
