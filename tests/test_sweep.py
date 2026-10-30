"""Regression cases from real listings. Run: python -m unittest discover tests"""
from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from fp4sweep import adapters
from fp4sweep.model import Candidate
from fp4sweep.store import Store, connect, gpu_count, load_catalog, match_catalog

CATALOG = load_catalog()


def family(title: str):
    entry, vram = match_catalog(title, CATALOG)
    if entry is None or vram is None or vram < entry.get("min_vram", 24):
        return None, None
    return entry["id"], vram


class MatcherTest(unittest.TestCase):
    CASES = [
        # laptops: a mobile GPU next to system RAM is not a desktop card
        ('3Year-Lenovo ThinkPad P16 Gen 3 Ultra 9 275HX,16" WQUXGA,RTX Pro 4000,64GB+1TB', None, None),
        ("Dell Pro Max 18 Plus Workstation NVIDIA RTX PRO 4000 64GB MB18250", None, None),
        ("LENOVO THINKPAD P16 G2 192GB RAM RTX 5000ADA WQUXGA TOUCH 2TB", None, None),
        ('Acer Predator PH18-72, 18", I9-14900HX, RTX 4090, 1TB', None, None),
        ("PNY NVIDIA RTX PRO 4000 Blackwell 24GB GDDR7", "rtxpro4000bw", 24),
        # two stock cards are not the 48GB mod
        ("AI Server 2x RTX 3090 FE NVLink 48GB Threadripper PRO 3955WX", "rtx3090", 48),
        ("AI INFERENCE SERVER - Dual RTX 3090 48GB VRAM - 256GB RAM", "rtx3090", 48),
        ("RTX 3090 48GB Turbo dual-slot blower modified", "rtx3090_48", 48),
        ("RTX 4090 48GB dual fan turbo", "rtx4090_48", 48),
        # Strix Halo boxes keyword-stuff GB10
        ("Mini PC Amd Ai Max+395 128gb+2tb Gb10 128gb Mini Desktop", "strixhalo", 128),
        ("Bosgame M5 AI Mini Desktop Ryzen AI Max+ 395 WIFI 7 PC 128G LPPDDR5 2TB", "strixhalo", 128),
        ("NVIDIA DGX Spark 4TB Founders Edition", "dgxspark", 128),
        ("NVIDIA Jetson Thor Series T4000 Module Edge computer", "jetsonthor", 64),
        ("Original Jetson AGX Thor 128GB Developer Kit T5000", "jetsonthor", 128),
        # memory mods vs PCs with system RAM
        ("High Performance NVIDIA RTX 5090 96GB AI GPU Graphic Card", "rtx5090_96", 96),
        ("Ryzen 9 9950X3D2 + RTX 5090 + 96GB + 4TB AI/Gaming", "rtx5090", 32),
        ("RTX 5090 Gaming PC i9 14900K 96GB DDR5 RAM 4TB", "rtx5090", 32),
        ("RTX 4080s 32G Turbo Edition Rtx 4080 Super 32GB", "rtx4080_32", 32),
        ("Hot selling RTX 4080 32G New Turbo Edition", "rtx4080_32", 32),
        ("Komputer do grania RTX 4080, AMD 9800x3d, x870e, 32 gb", None, None),
        ("NVIDIA GeForce RTX 4080 Super Turbo 32GB 2SLOT GDDR6X Graphics Card", "rtx4080_32", 32),
        ("Lenovo Legion Tower 7i Gen 8 i9-14900KF 32GB 1TB RTX 4080 SUPER", None, None),
        ("RTX 4080 SUPER 16GB 32GB RAM gaming PC", None, None),
        # families added 2026-10-31
        ("New Spot Tesla L20 48GB GPU Deep Learning", "l20", 48),
        ("A800 Tensor Core GPU SXM4 80GB", "a100", 80),
        ("AMD Radeon PRO W7800 AI TOP 32GB GPU Graphics card", "w7800", 32),
        ("AMD Radeon PRO W7800 48GB", "w7800", 48),
        ("Used Pro W6800 for AMD Radeon 32GB GDDR6 256bit", "w6800", 32),
        ("GIGABYTE Radeon AI PRO R9700 AI TOP 32G Graphics Card", "r9700", 32),
        ("ASRock AMD Radeon AI PRO R9600D Passive 32GB", "r9700", 32),
        ("MAXSUN Intel Arc Pro B70 32G Turbo GDDR6", "arcb70", 32),
        ("Intel Arc Pro B60 24GB", "arcb60", 24),
        # tenstorrent's own store names
        ("p150b", "tenstorrent", 32),
        ("Blackhole® p100a", "tenstorrent", 28),
        ("Wormhole™ n300 Series", "tenstorrent", 24),
        ("TT-QuietBox® 2 (Blackhole®)", "tenstorrent", 128),
        ("TT-QuietBox®", "tenstorrent", 96),
        ("n150s", None, None),
        ("NVIDIA Tesla P100 16GB", None, None),
        ("Mac Studio 2025 M4 Max 14-Core CPU 32-Core GPU 36GB 1TB", "macstudio", 36),
        ("Apple Mac Studio M5 Ultra 256GB 2TB", "macstudio", 256),
    ]

    def test_cases(self):
        for title, fam, vram in self.CASES:
            with self.subTest(title=title):
                self.assertEqual(family(title), (fam, vram))


class GpuCountTest(unittest.TestCase):
    def test_counts(self):
        for title, n in [
            ("New 10 PCs Radeon Instinct Mi50 32GB", 10),
            ("Ryzen AI Max+ 395 Computer WIFI 7 PC 128G", 1),
            ("NVIDIA DGX Spark 128GB - NEW/SEALED - 4 UNITS AVAILABLE", 1),
            ("Lot of 4 units RTX 3090", 4),
            ("4x RTX 5090 workstation", 4),
            ("RTX A6000 48GB 300W Dual-slot", 1),
        ]:
            with self.subTest(title=title):
                self.assertEqual(gpu_count(title), n)


def _page(items: list[dict]) -> str:
    state = {"props": {"pageProps": {"initialState": {"search": {
        "items": {"listing": {"items": items}}}}}}}
    return ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(state) + "</script></html>")


class YahooJpTest(unittest.TestCase):
    def test_next_data(self):
        soon = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))
        later = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 86400 * 2))
        page = _page([
            {"auctionId": "x1", "title": "RTX 3090 24GB", "price": 200000,
             "buyNowPrice": None, "endTime": later, "itemCondition": "USED40"},
            {"auctionId": "z2", "title": "RTX 3090 未開封", "price": 226900,
             "buyNowPrice": 226900, "endTime": soon, "itemCondition": "NEW"},
            {"auctionId": "p3", "title": "RTX 3090 ending", "price": 10000,
             "buyNowPrice": None, "endTime": soon},
        ])
        with mock.patch.object(adapters, "FAMILY_TERMS", ("rtx 3090",)), \
                mock.patch.object(adapters, "get", return_value=(page, "direct")):
            got = list(adapters.yahoojp(sleep=0))
        self.assertEqual([(c.ext_id, c.price, c.condition) for c in got],
                         [("x1", 200000, "used"), ("z2", 226900, "new")])
        self.assertEqual(got[0].currency, "JPY")


class AmazonTest(unittest.TestCase):
    def test_title_is_brand_plus_label_not_stars(self):
        blk = ('data-asin="B0GLHB6D3W" x><h2 class="a-size-mini"><span>PNY</span></h2>'
               '<h2 aria-label="NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation" '
               'class="a-text-normal"><span>NVIDIA RTX PRO 6000</span></h2>'
               '<span class="a-icon-alt">4.6 out of 5 stars</span>'
               '<span class="a-offscreen">$8,565.00</span>')
        with mock.patch.object(adapters, "_browserless", return_value=blk), \
                mock.patch("time.sleep"):
            got = list(adapters.amazon(sleep=0))
        self.assertTrue(got)
        self.assertEqual(got[0].title,
                         "PNY NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation")
        self.assertEqual(got[0].price, 8565.0)


class RematchTest(unittest.TestCase):
    def _store(self, title, catalog_id, vram, units=1, usd=3000.0):
        db = connect(":memory:")
        store = Store(db)
        store.upsert(Candidate(source="t", ext_id="1", url="u", title=title,
                               price=usd, currency="USD"), CATALOG)
        db.execute("UPDATE listings SET catalog_id=?, vram=?, units=?, verdict='yes' "
                   "WHERE key='t:1'", (catalog_id, vram, units))
        db.commit()
        return db, store

    def test_moved_family_is_regated(self):
        db, store = self._store("Mini PC Ai Max+395 128gb Gb10", "dgxspark", 128)
        store.rematch(CATALOG)
        r = db.execute("SELECT catalog_id, verdict, matched FROM listings").fetchone()
        self.assertEqual((r[0], r[1], r[2]), ("strixhalo", None, 1))

    def test_laptop_leaves_the_board(self):
        db, store = self._store('ThinkPad P16 16" RTX PRO 4000 64GB', "rtxpro4000bw",
                                64, usd=4800.0)
        db.execute("UPDATE listings SET matched=1")
        store.rematch(CATALOG)
        self.assertEqual(db.execute("SELECT matched FROM listings").fetchone()[0], 0)

    def test_gate_units_survive_for_same_family(self):
        # 4 UNITS AVAILABLE at $3,500 each: the gate said 1, the floor holds
        db, store = self._store("NVIDIA DGX Spark 128GB GB10 - 4 UNITS AVAILABLE",
                                "dgxspark", 128, units=1, usd=3500.0)
        store.rematch(CATALOG)
        r = db.execute("SELECT matched, verdict FROM listings").fetchone()
        self.assertEqual((r[0], r[1]), (1, "yes"))

    def test_upsert_regates_on_family_change(self):
        db, store = self._store("Mini PC Ai Max+395 128gb Gb10", "dgxspark", 128)
        store.upsert(Candidate(source="t", ext_id="1", url="u",
                               title="Mini PC Ai Max+395 128gb Gb10",
                               price=3000.0, currency="USD"), CATALOG)
        r = db.execute("SELECT catalog_id, verdict FROM listings").fetchone()
        self.assertEqual((r[0], r[1]), ("strixhalo", None))


class _FakeStore:
    def __init__(self, n):
        self.rows = [{"key": f"k{i}", "source": "ebay", "title": f"t{i}",
                      "price": 1.0, "currency": "USD"} for i in range(n)]
        self.judged = {}

    def pending_verdicts(self, limit):
        return [r for r in self.rows if r["key"] not in self.judged][:limit]

    def set_verdict(self, key, ok, *a):
        self.judged[key] = ok


class GateLoopTest(unittest.TestCase):
    def _reply(self, n):
        return json.dumps([{"i": i, "ok": True, "units": 1, "vram_total": 32,
                            "vram_unit": 32} for i in range(n)])

    def test_empty_reply_falls_through_to_the_fallback(self):
        from fp4sweep import verify
        calls = []

        def call(prompt, model):
            calls.append(model)
            return "" if model == verify.MODEL else self._reply(prompt.count("[ebay]"))
        store = _FakeStore(20)
        with mock.patch.object(verify, "_call", side_effect=call), \
                mock.patch("time.sleep"):
            ok, bad = verify.run(store, batch=20, max_batches=1)
        self.assertEqual((ok, bad), (20, 0))
        self.assertEqual(calls, [verify.MODEL, verify.FALLBACK])

    def test_a_batch_no_model_answers_is_split_not_resent(self):
        from fp4sweep import verify
        sizes = []

        def call(prompt, model):
            n = prompt.count("[ebay]")
            sizes.append(n)
            return "" if n > 5 else self._reply(n)  # only small batches get through
        store = _FakeStore(20)
        with mock.patch.object(verify, "_call", side_effect=call), \
                mock.patch("time.sleep"):
            ok, _ = verify.run(store, batch=20, max_batches=6)
        self.assertEqual(ok, 20)
        self.assertEqual(sizes, [20, 20, 5, 5, 5, 5])

    def test_dead_gateway_stops_the_run(self):
        from fp4sweep import verify
        store = _FakeStore(120)
        with mock.patch.object(verify, "_call", side_effect=OSError("down")) as c, \
                mock.patch("time.sleep"):
            self.assertEqual(verify.run(store, batch=20, max_batches=6), (0, 0))
        self.assertEqual(c.call_count, 6)  # 3 judgements x 2 models


if __name__ == "__main__":
    unittest.main()
