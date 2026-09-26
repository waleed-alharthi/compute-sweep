"""SQLite store: listings, price events, FX, and the news rollups."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import yaml

from .config import DB_PATH, MIN_VRAM
from .model import Candidate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings(
  key TEXT PRIMARY KEY,            -- source:ext_id
  source TEXT, ext_id TEXT, url TEXT, title TEXT,
  price REAL, currency TEXT, usd REAL, prev_usd REAL,
  region TEXT, country TEXT, condition TEXT, stock TEXT,
  evidence TEXT, catalog_id TEXT, product TEXT, vram INTEGER, fp4 TEXT,
  usd_per_gb REAL, matched INTEGER,
  first_seen REAL, last_seen REAL, last_price_change REAL,
  hits INTEGER DEFAULT 1, raw TEXT
);
CREATE TABLE IF NOT EXISTS price_events(
  key TEXT, ts REAL, usd REAL, prev_usd REAL
);
CREATE TABLE IF NOT EXISTS fx(
  day TEXT, base TEXT, rates TEXT, ts REAL, PRIMARY KEY(day, base)
);
CREATE INDEX IF NOT EXISTS ix_seen ON listings(last_seen);
CREATE INDEX IF NOT EXISTS ix_first ON listings(first_seen);
"""

_CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


def connect(path=DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db


def load_catalog(path=_CATALOG_PATH):
    return yaml.safe_load(Path(path).read_text())


_WORD_COUNTS = {"dual": 2, "twin": 2, "triple": 3, "quad": 4}

# buyer's ads masquerading as listings: the number on them is an ask, not
# something you can pay
WANTED_RE = re.compile(
    r"\b(wanted|wanting|looking for|seeking|hunting)\b|"
    r"مطلوب|أبحث|ابحث|نبحث|نريد|اريده", re.I)


def gpu_count(title: str) -> int:
    """"4x RTX 5090" / "quad 5090" workstations: the price is for the pile,
    so the pile's VRAM is what $/GB must divide by."""
    t = title.lower().replace("\u00d7", "x")
    m = re.search(r"\b(\d)\s*x\s*(?:nvidia|asus|msi|gigabyte|pny|zotac)?\s*(?:rtx|geforce|\d{3,4})", t)
    if m:
        return int(m.group(1))
    for word, n in _WORD_COUNTS.items():
        if re.search(rf"\b{word}\b", t):
            return n
    return 1


def match_catalog(title: str, catalog) -> tuple[dict, int] | tuple[None, None]:
    """First catalog entry whose patterns hit the title wins. VRAM can be
    corrected by vram_overrides (a 512 GB Mac Studio is not a 96 GB one)
    and by the GPU count of multi-card workstations."""
    t = title.lower()
    for entry in catalog:
        for pat in entry["patterns"]:
            if re.search(pat, t):
                vram = entry.get("vram", 0)
                for vpat, vval in entry.get("vram_overrides") or ():
                    if re.search(vpat, t):
                        vram = vval
                        break
                return entry, vram * gpu_count(title)
    return None, None


class Store:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def upsert(self, c: Candidate, catalog) -> None:
        if WANTED_RE.search(c.title or ""):
            return
        entry, vram = match_catalog(c.title, catalog)
        matched = entry is not None and vram is not None and vram >= MIN_VRAM
        key = f"{c.source}:{c.ext_id}"
        old = self.db.execute(
            "SELECT usd, hits, last_price_change, currency FROM listings WHERE key=?",
            (key,)).fetchone()
        now = time.time()
        usd = None
        if c.price and c.currency:
            usd = self.to_usd(c.price, c.currency)
            if usd is None:  # fx missing: keep the candidate, price stays raw
                c.meta["fx_pending"] = True
        if usd is None and old:
            usd = self.to_usd(c.price, c.currency) if c.price else old["usd"]
        prev_usd = old["usd"] if old else None
        change = old["last_price_change"] if old else None
        if usd and prev_usd and abs(usd - prev_usd) / prev_usd > 0.005:
            change = now
            self.db.execute(
                "INSERT INTO price_events(key,ts,usd,prev_usd) VALUES(?,?,?,?)",
                (key, now, usd, prev_usd))
        elif old is None:
            change = now
        if old and usd is None:
            usd = prev_usd  # a re-sighted listing keeps its known conversion
        per_gb = round(usd / vram, 2) if usd and matched else None
        if per_gb is not None and per_gb > 5000:
            # nothing real is $5k/GB; that's a mis-parsed currency
            matched = 0
            per_gb = None
        self.db.execute("""
          INSERT INTO listings(key,source,ext_id,url,title,price,currency,usd,prev_usd,
            region,country,condition,stock,evidence,catalog_id,product,vram,fp4,
            usd_per_gb,matched,first_seen,last_seen,last_price_change,hits,raw)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(key) DO UPDATE SET
            title=excluded.title, price=excluded.price, currency=excluded.currency,
            usd=excluded.usd, prev_usd=excluded.prev_usd, condition=excluded.condition,
            stock=excluded.stock, evidence=excluded.evidence,
            catalog_id=excluded.catalog_id, product=excluded.product,
            vram=excluded.vram, fp4=excluded.fp4, matched=excluded.matched,
            usd_per_gb=excluded.usd_per_gb,
            last_seen=excluded.last_seen, last_price_change=excluded.last_price_change,
            hits=hits+1, raw=excluded.raw
        """, (key, c.source, c.ext_id, c.url, c.title, c.price, c.currency, usd,
              prev_usd, c.region, c.country, c.condition, c.stock, c.evidence,
              entry["id"] if matched else (entry["id"] if entry else None),
              entry["name"] if matched else None, vram if matched else None,
              entry["fp4"] if matched else None, per_gb, 1 if matched else 0,
              now, now, change, (old["hits"] + 1) if old else 1,
              json.dumps(c.meta, ensure_ascii=False)))
        self.db.commit()

    def to_usd(self, amount: float, cur: str) -> float | None:
        if cur == "USD":
            return round(amount, 2)
        rates = current_rates(self.db)
        rate = rates.get(cur) if rates else None
        return round(amount / rate, 2) if rate else None

    # ---- views for the export ----

    def active(self, days: float = 14.0) -> list[sqlite3.Row]:
        cut = time.time() - days * 86400
        return self.db.execute(
            "SELECT * FROM listings WHERE matched=1 AND last_seen>? "
            "ORDER BY usd_per_gb ASC", (cut,)).fetchall()

    def news(self, hours: float = 48.0) -> list[sqlite3.Row]:
        cut = time.time() - hours * 3600
        return self.db.execute(
            "SELECT * FROM listings WHERE matched=1 AND first_seen>? "
            "ORDER BY first_seen DESC", (cut,)).fetchall()

    def drops(self, hours: float = 168.0) -> list[sqlite3.Row]:
        cut = time.time() - hours * 3600
        rows = []
        for r in self.db.execute(
                "SELECT * FROM listings WHERE matched=1 "
                "AND last_price_change>? AND prev_usd>0", (cut,)):
            pct = (r["usd"] - r["prev_usd"]) / r["prev_usd"] * 100
            if pct <= -5:
                rows.append((r, round(pct, 1)))
        rows.sort(key=lambda x: x[1])
        return rows

    def unknown(self, hours: float = 336.0) -> list[sqlite3.Row]:
        cut = time.time() - hours * 3600
        return self.db.execute(
            "SELECT * FROM listings WHERE matched=0 AND last_seen>? "
            "ORDER BY last_seen DESC LIMIT 50", (cut,)).fetchall()


# ---- FX ----

def current_rates(db, max_age_h: float = 30.0) -> dict | None:
    day = time.strftime("%Y-%m-%d")
    row = db.execute("SELECT rates,ts FROM fx WHERE base='USD' AND day=?",
                     (day,)).fetchone()
    if row and time.time() - row["ts"] < max_age_h * 3600:
        return json.loads(row["rates"])
    return None


def refresh_fx(db, fetcher) -> dict | None:
    """Rates as <cur> per 1 USD. Two free sources; whichever answers wins."""
    day = time.strftime("%Y-%m-%d")
    if current_rates(db):
        return current_rates(db)
    urls = (
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.dev/v1/latest?base=USD",
    )
    for url in urls:
        try:
            data = json.loads(fetcher(url))
            rates = {"USD": 1.0, **data.get("rates", {})}
            if len(rates) < 10:
                continue
            db.execute(
                "INSERT OR REPLACE INTO fx(day,base,rates,ts) VALUES(?,?,?,?)",
                (day, "USD", json.dumps(rates), time.time()))
            db.commit()
            return rates
        except Exception:
            continue
    # a stale day is better than none
    row = db.execute(
        "SELECT rates FROM fx WHERE base='USD' ORDER BY day DESC LIMIT 1").fetchone()
    return json.loads(row["rates"]) if row else None
