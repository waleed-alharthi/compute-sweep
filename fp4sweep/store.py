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
-- one row per listing per day it was seen alive: the raw material for
-- any trend query (by make/model, source, ship class, condition...)
CREATE TABLE IF NOT EXISTS sightings(
  day TEXT, key TEXT, make TEXT, model TEXT, units INTEGER,
  vram_unit INTEGER, vram_total INTEGER, fp TEXT, usd REAL,
  landed_usd REAL, landed_per_gb REAL, ship TEXT, source TEXT,
  country TEXT, condition TEXT, needs TEXT,
  PRIMARY KEY(day, key)
);
CREATE INDEX IF NOT EXISTS ix_sight_model ON sightings(make, model, day);
CREATE TABLE IF NOT EXISTS verdicts(
  key TEXT PRIMARY KEY, title TEXT, ok INTEGER, units INTEGER,
  vram_total INTEGER, reason TEXT, model TEXT, ts REAL
);
"""

# columns added by the 2026-10 overhaul; ALTERed onto older databases
_NEW_COLS = (("ship", "TEXT"), ("landed_usd", "REAL"), ("landed_per_gb", "REAL"),
             ("units", "INTEGER"), ("verdict", "TEXT"), ("needs", "TEXT"),
             ("make", "TEXT"), ("model", "TEXT"), ("vram_unit", "INTEGER"))

_CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


def connect(path=DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(listings)")}
    for col, typ in _NEW_COLS:
        if col not in have:
            db.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")
    db.commit()
    return db


def load_catalog(path=_CATALOG_PATH):
    return yaml.safe_load(Path(path).read_text())


_WORD_COUNTS = {"dual": 2, "twin": 2, "triple": 3, "quad": 4}

# buyer's ads masquerading as listings: the number on them is an ask, not
# something you can pay
WANTED_RE = re.compile(
    r"\b(wanted|wanting|looking for|seeking|hunting|ankauf)\b|"
    r"\bwir kaufen\b|^\s*suche\b|"
    r"مطلوب|أبحث|ابحث|نبحث|نريد|اريده", re.I)

# accessory/part titles that match a GPU pattern but are not the card
ACCESSORY_RE = re.compile(
    r"water\s?block|backplate|bracket|shroud|riser|riserless|schematic|"
    r"thermal pad|\bfan\b|cooler|heatsink|\bcable\b|adapter|sleeve|"
    r"\bcase\b|\bpsu\b|\bmb\b|motherboard|repair|donor|\bshell\b|"
    r"shaper|\bstand\b|holder|\bport|liquid \b|water\s?cool", re.I)

# editorial pages the discovery adapter trips over: buyer's guides, FAQ,
# "vs" explainers. A listing is a thing you can buy; a question is not.
ARTICLE_RE = re.compile(
    r"\?$|^(who|what|why|how|can you|should you|is the|are the)\b|"
    r"\bfaq\b|\bvs\.?\s|\balternatives?\b|^best\s|^top\s+\d|compar(?:e|ison|ed)", re.I)


# How a listing reaches Oman, and what that adds. A fixed courier/freight
# allowance plus duty+VAT as a fraction of the goods. Deliberately
# conservative: a cheap card that needs a forwarder must beat a local one
# after the forwarder, or it is not cheap.
#   om     seller is in Oman (meet / local courier)
#   gcc    GCC seller: courier + 5% VAT at the border
#   intl   seller ships internationally (AliExpress, DHgate, Alibaba, eBay GSP)
#   fwd    domestic-only seller abroad -> parcel forwarder to Muscat
#   proxy  Japanese auctions through a proxy buyer (Buyee/ZenMarket)
SHIP_COST = {"om": (0, 0.0), "gcc": (40, 0.05), "intl": (80, 0.10),
             "fwd": (160, 0.10), "proxy": (140, 0.10)}
GCC = {"sa", "ae", "kw", "bh", "qa"}


def ship_class(source: str, country: str, meta: dict) -> str:
    if meta.get("ship"):
        return meta["ship"]
    c = (country or "").lower()
    if c == "om":
        return "om"
    if c in GCC:
        return "gcc"
    if source in ("aliexpress", "dhgate", "alibaba", "ebay", "amazon", "tenstorrent"):
        return "intl"
    if source in ("yahoojp",):
        return "proxy"
    return "fwd"


def landed(usd: float, ship: str) -> float:
    fixed, duty = SHIP_COST.get(ship, SHIP_COST["fwd"])
    return round(usd * (1 + duty) + fixed, 2)


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
        if any(re.search(r, t) for r in entry.get("reject") or ()):
            continue  # e.g. "Tesla P100" is not a Tenstorrent p100
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
        if matched and ACCESSORY_RE.search(c.title or ""):
            return  # "$240 RTX 5090" is a water block, not a card
        if matched and ARTICLE_RE.search(c.title or ""):
            return
        key = f"{c.source}:{c.ext_id}"
        old = self.db.execute(
            "SELECT usd, hits, last_price_change, currency, title, verdict, "
            "vram, units FROM listings WHERE key=?", (key,)).fetchone()
        gated = old is not None and old["verdict"] == "yes" \
            and old["title"] == c.title and old["vram"]
        if gated:  # the legitimacy gate read this exact title: keep its VRAM
            vram = old["vram"]
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
        units = (old["units"] if gated else gpu_count(c.title)) if matched else None
        ship = ship_class(c.source, c.country, c.meta)
        land = landed(usd, ship) if usd else None
        per_gb = round(usd / vram, 2) if usd and matched else None
        if per_gb is not None and per_gb > 5000:
            # nothing real is $5k/GB; that's a mis-parsed currency
            matched = 0
            per_gb = None
        if matched and usd and entry and entry.get("floor_usd") \
                and usd / (units or 1) < entry["floor_usd"]:
            # below the floor it is a fake listing or a parts/scrap price
            matched = 0
            per_gb = None
        self.db.execute("""
          INSERT INTO listings(key,source,ext_id,url,title,price,currency,usd,prev_usd,
            region,country,condition,stock,evidence,catalog_id,product,vram,fp4,
            usd_per_gb,matched,first_seen,last_seen,last_price_change,hits,raw,
            ship,landed_usd,landed_per_gb,units)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(key) DO UPDATE SET
            title=excluded.title, price=excluded.price, currency=excluded.currency,
            usd=excluded.usd, prev_usd=excluded.prev_usd, condition=excluded.condition,
            stock=excluded.stock, evidence=excluded.evidence,
            catalog_id=excluded.catalog_id, product=excluded.product,
            vram=excluded.vram, fp4=excluded.fp4, matched=excluded.matched,
            usd_per_gb=excluded.usd_per_gb,
            last_seen=excluded.last_seen, last_price_change=excluded.last_price_change,
            hits=hits+1, raw=excluded.raw, ship=excluded.ship,
            landed_usd=excluded.landed_usd, landed_per_gb=excluded.landed_per_gb,
            units=excluded.units,
            verdict=CASE WHEN listings.title=excluded.title THEN listings.verdict END
        """, (key, c.source, c.ext_id, c.url, c.title, c.price, c.currency, usd,
              prev_usd, c.region, c.country, c.condition, c.stock, c.evidence,
              entry["id"] if matched else (entry["id"] if entry else None),
              entry["name"] if matched else None, vram if matched else None,
              entry["fp"] if matched else None, per_gb, 1 if matched else 0,
              now, now, change, (old["hits"] + 1) if old else 1,
              json.dumps(c.meta, ensure_ascii=False),
              ship, land, round(land / vram, 2) if land and matched else None,
              units))
        self.db.commit()

    def to_usd(self, amount: float, cur: str) -> float | None:
        if cur == "USD":
            return round(amount, 2)
        rates = current_rates(self.db)
        rate = rates.get(cur) if rates else None
        return round(amount / rate, 2) if rate else None

    # ---- views for the export ----

    def active(self, hours: float = 72.0) -> list[sqlite3.Row]:
        """Buyable now: matched, seen by a sweep in the last 72h, not
        rejected by the legitimacy gate, ranked by landed $/GB in Oman."""
        cut = time.time() - hours * 3600
        return self.db.execute(
            "SELECT * FROM listings WHERE matched=1 AND last_seen>? "
            "AND landed_per_gb IS NOT NULL AND COALESCE(verdict,'') != 'no' "
            "AND COALESCE(needs,'') != 'sxm' "
            "ORDER BY landed_per_gb ASC", (cut,)).fetchall()

    def record_sightings(self) -> int:
        """Snapshot every verified, live listing into today's sightings.
        Idempotent per day (latest sweep of the day wins)."""
        day = time.strftime("%Y-%m-%d")
        cur = self.db.execute("""
          INSERT OR REPLACE INTO sightings
          SELECT ?, key, make, model, units, vram_unit, vram, fp4, usd,
                 landed_usd, landed_per_gb, ship, source, country, condition,
                 needs
          FROM listings WHERE verdict='yes' AND matched=1 AND last_seen>?""",
                              (day, time.time() - 26 * 3600))
        self.db.commit()
        return cur.rowcount

    def pending_verdicts(self, limit: int = 60) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT key, title, url, source, price, currency, usd, vram "
            "FROM listings WHERE matched=1 AND verdict IS NULL "
            "AND last_seen>? ORDER BY landed_per_gb ASC LIMIT ?",
            (time.time() - 72 * 3600, limit)).fetchall()

    def set_verdict(self, key: str, ok: bool, units: int | None,
                    vram_total: int | None, reason: str, model: str,
                    needs: str = "", make: str = "", hw_model: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO verdicts VALUES(?,?,?,?,?,?,?,?)",
            (key, None, int(ok), units, vram_total, reason, model, time.time()))
        if ok and vram_total and vram_total < MIN_VRAM:
            ok = False  # the gate read the title: it is a 16GB variant
        if ok and vram_total:
            # the gate read the title properly: trust its VRAM over the regex
            self.db.execute(
                "UPDATE listings SET verdict='yes', vram=?, units=?, needs=?, "
                "make=?, model=?, vram_unit=?, "
                "usd_per_gb=ROUND(usd/?,2), landed_per_gb=ROUND(landed_usd/?,2) "
                "WHERE key=?", (vram_total, units, needs, make or None,
                                hw_model or None,
                                vram_total // (units or 1),
                                vram_total, vram_total, key))
        else:
            self.db.execute("UPDATE listings SET verdict=?, needs=? WHERE key=?",
                            ("yes" if ok else "no", needs, key))
        self.db.commit()

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

    def product_trends(self, days: int = 14) -> dict[str, list]:
        """Daily minimum street USD per catalog product, oldest first.

        Reconstructed from each listing's price-event chain: the price in
        effect on a day is the last event at or before it (the chain's
        first prev_usd is the price before the first recorded change), and
        a listing contributes only on days it was alive. A day with no
        live listing is a None gap, never a zero - "no data" and "free"
        are different facts. Gaps close as the sweep accumulates days.
        """
        day = 86400.0
        now = time.time()
        ends = [now - (days - 1 - i) * day for i in range(days)]
        starts = [e - day for e in ends]
        rows = self.db.execute(
            "SELECT key, product, usd, prev_usd, first_seen, last_seen "
            "FROM listings WHERE matched=1 AND product IS NOT NULL "
            "AND last_seen>? AND usd IS NOT NULL", (starts[0] - day,)).fetchall()
        if not rows:
            return {}
        events: dict[str, list[tuple]] = {}
        for r in self.db.execute(
                "SELECT key, ts, usd, prev_usd FROM price_events "
                "WHERE key IN (%s) ORDER BY ts"
                % ",".join("?" * len(rows)), [r["key"] for r in rows]):
            events.setdefault(r["key"], []).append(
                (r["ts"], r["usd"], r["prev_usd"]))
        out: dict[str, list] = {}
        for r in rows:
            chain = events.get(r["key"]) or []
            for i, d_end in enumerate(ends):
                if r["first_seen"] > d_end or r["last_seen"] < starts[i] - day:
                    continue
                price = r["usd"]
                seen_event = False
                for ts, usd, prev in chain:
                    if ts <= d_end:
                        price = usd
                        seen_event = True
                    else:
                        break
                if not seen_event and chain and chain[0][2]:
                    # before its first recorded change the listing sat at
                    # that change's prev price, not at today's price
                    price = chain[0][2]
                if price is None:
                    continue
                series = out.setdefault(r["product"], [None] * days)
                if series[i] is None or price < series[i]:
                    series[i] = round(price, 2)
        return out


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
