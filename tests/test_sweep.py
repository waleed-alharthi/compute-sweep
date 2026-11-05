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
        # families added 2026-11-05
        ("PNY NVIDIA RTX PRO 5500 Blackwell Workstation Edition 84GB GDDR7", "rtxpro5500bw", 84),
        ("NVIDIA RTX PRO 5000 Blackwell 48GB", "rtxpro5000bw", 48),
        ('ASUS ProArt P16 16" WQUXGA OLED Laptop NVIDIA RTX Spark N1X 128GB', "rtxspark", 128),
        ("Microsoft Surface RTX Spark Dev Box 128GB", "rtxspark", 128),
        ("Dell XPS 16 NVIDIA RTX Spark N1X 64GB LPDDR5X 1TB", "rtxspark", 64),
        ("NVIDIA DGX Spark 64GB Founders Edition 2TB", "dgxspark", 64),
        ("NVIDIA DGX Spark 128GB 4TB", "dgxspark", 128),
        ("GMKtec EVO-X5 Pro AMD Ryzen AI Max+ PRO 495 192GB LPDDR5X 2TB", "gorgonhalo", 192),
        ("Minisforum Ryzen AI Max+ 395 128GB", "strixhalo", 128),
        ("Apple Mac mini M5 Pro 64GB 1TB", "macmini", 64),
        ("Mac mini M4 Pro 24GB 512GB SSD", "macmini", 24),
        ("Mac mini M6 16GB 256GB SSD", None, None),
        ("Huawei Atlas 350 Ascend 950PR 112GB AI accelerator", "atlas350", 112),
        ("Huawei Atlas 300I Duo 96GB", "atlas300i", 96),
        ("NVIDIA Jetson AGX Thor Dev Kit 128GB T5000 Blackwell Edge AI Robotics", "jetsonthor", 128),
        ("Newest RTX 5090 Turbo 96gb Graphics Card Gpu 5090 Video Card", "rtx5090_96", 96),
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

    def test_a_timing_out_primary_stops_leading(self):
        from fp4sweep import verify
        calls = []

        def call(prompt, model):
            calls.append(model)
            if model == verify.MODEL:
                raise TimeoutError()
            return self._reply(prompt.count("[ebay]"))
        store = _FakeStore(60)
        with mock.patch.object(verify, "_call", side_effect=call), \
                mock.patch("time.sleep"):
            self.assertEqual(verify.run(store, batch=20, max_batches=3), (60, 0))
        self.assertEqual(calls, [verify.MODEL, verify.FALLBACK,
                                 verify.FALLBACK, verify.FALLBACK])

    def test_dead_gateway_stops_the_run(self):
        from fp4sweep import verify
        store = _FakeStore(120)
        with mock.patch.object(verify, "_call", side_effect=OSError("down")) as c, \
                mock.patch("time.sleep"):
            self.assertEqual(verify.run(store, batch=20, max_batches=6), (0, 0))
        self.assertEqual(c.call_count, 6)  # 3 judgements x 2 models


def _fixture(name: str) -> str:
    import gzip
    from pathlib import Path
    return gzip.open(Path(__file__).parent / "fixtures" / name, "rt").read()


class PageParseTest(unittest.TestCase):
    """Real item pages captured 2026-11-05."""

    def test_aliexpress_zero_sold_dgx_spark(self):
        from fp4sweep.pagecheck import parse_aliexpress
        f = parse_aliexpress(_fixture("ae-dgxspark-0sold.html.gz"))
        self.assertFalse(f["dead"])
        self.assertEqual(f["sold"], 0)
        self.assertEqual(f["store"], "Canvas HHH Store")
        self.assertEqual(f["followers"], 88)
        self.assertEqual(f["price"], "OMR1,080.54")

    def test_aliexpress_delisted(self):
        from fp4sweep.pagecheck import parse_aliexpress
        self.assertTrue(parse_aliexpress(_fixture("ae-delisted.html.gz"))["dead"])

    def test_aliexpress_sold_count(self):
        from fp4sweep.pagecheck import parse_aliexpress
        h = '<span class="reviewer--sold--ytPeoEy">1,234 sold</span>'
        self.assertEqual(parse_aliexpress(h)["sold"], 1234)
        h = '<span class="reviewer--sold--ytPeoEy">5K+ sold</span>'
        self.assertEqual(parse_aliexpress(h)["sold"], 5000)

    def test_dhgate_store_and_options(self):
        from fp4sweep.pagecheck import option_for, parse_dhgate
        f = parse_dhgate(_fixture("dhgate-szbox-multisku.html.gz"))
        self.assertEqual((f["positive"], f["orders"]), (66.7, 5))
        self.assertEqual(f["price"], "US $1,888.65")
        self.assertEqual(len(f["options"]), 20)
        hits, needed = option_for(f["options"], 128)
        self.assertTrue(needed)
        self.assertEqual(hits[0], "US-LPDDR5 128GB 4TB")

    def test_alibaba_live_page_whose_reviews_say_unavailable(self):
        from fp4sweep.pagecheck import parse_alibaba
        f = parse_alibaba(_fixture("alibaba-live-review-mentions-unavailable.html.gz"))
        self.assertFalse(f["dead"])
        self.assertEqual((f["rating"], f["ratings"]), (4.7, 48))

    def test_alibaba_store_rating(self):
        from fp4sweep.pagecheck import parse_alibaba
        f = parse_alibaba(_fixture("alibaba-p40-rated.html.gz"))
        self.assertEqual((f["rating"], f["ratings"], f["years"]), (4.4, 95, 7))


class PageJudgeTest(unittest.TestCase):
    def test_option_for(self):
        from fp4sweep.pagecheck import option_for
        self.assertEqual(option_for(["24GB"], 24), ([], False))
        self.assertEqual(option_for(["Black", "White"], 32), ([], False))
        # only smaller memory on offer: the 128GB in the title is not for sale
        self.assertEqual(option_for(["US-LPDDR5 64GB 2TB", "US-LPDDR5 64GB 4TB"], 128),
                         ([], True))
        # storage sizes are not memory
        self.assertEqual(option_for(["128GB 1TB SSD", "128GB 2TB SSD"], 128), ([], False))

    def test_judge(self):
        from fp4sweep.pagecheck import judge
        ae0 = {"dead": False, "sold": 0}
        self.assertFalse(judge("aliexpress", ae0, 22.0, 27.0, None, False)[0])
        self.assertTrue(judge("aliexpress", ae0, 30.0, 27.0, None, False)[0])   # at/above street
        self.assertTrue(judge("aliexpress", {"sold": 40}, 22.0, 27.0, None, False)[0])
        self.assertFalse(judge("dhgate", {"orders": 500}, 30.0, 27.0, None, True)[0])
        self.assertTrue(judge("dhgate", {"orders": 500}, 30.0, 27.0, 3155.0, True)[0])
        self.assertFalse(judge("alibaba", {"dead": True}, 30.0, 27.0, None, False)[0])
        self.assertTrue(judge("alibaba", {"ratings": 95}, 10.0, 27.0, None, False)[0])


class StreetRowTest(unittest.TestCase):
    def test_family_row_is_the_cheapest_single_unit(self):
        from fp4sweep.run import _street_rows
        rows = [{"product": "W6800", "units": 25, "key": "lot"},
                {"product": "W6800", "units": 1, "key": "one"},
                {"product": "MI50", "units": 10, "key": "mi50lot"}]
        self.assertEqual([r["key"] for r in _street_rows(rows)], ["one", "mi50lot"])


class PageRunTest(unittest.TestCase):
    def _db(self):
        db = connect(":memory:")
        store = Store(db)
        rows = [("aliexpress", "1", "NVIDIA DGX Spark 128GB", 2800.0),
                ("dhgate", "2", "NVIDIA DGX Spark 128GB GB10", 3000.0),
                ("ebay", "3", "NVIDIA DGX Spark 128GB Founders", 3200.0),
                ("ebay", "4", "NVIDIA DGX Spark 128GB new", 3400.0)]
        for src, i, t, usd in rows:
            store.upsert(Candidate(source=src, ext_id=i, url=f"https://x/{i}", title=t,
                                   price=usd, currency="USD"), CATALOG)
        db.execute("UPDATE listings SET verdict='yes'")
        db.commit()
        return db, store

    def test_drop_then_next_cheapest_gets_checked(self):
        from fp4sweep import pagecheck
        from fp4sweep.run import board
        db, store = self._db()
        seen = []

        def fake(store_, factory, row, median):
            seen.append(row["key"])
            if row["source"] == "aliexpress":
                return False, "unproven seller", None
            return True, "page ok", 3155.0  # the 128GB option's price
        with mock.patch.object(pagecheck, "check_row", side_effect=fake):
            self.assertEqual(pagecheck.run(store, board, None, budget=10), (1, 1))
        self.assertEqual(seen, ["aliexpress:1", "dhgate:2"])
        keys = [r["key"] for r in store.active()]
        self.assertNotIn("aliexpress:1", keys)
        r = db.execute("SELECT usd, page_ok FROM listings WHERE key='dhgate:2'").fetchone()
        self.assertEqual((r[0], r[1]), (3155.0, 1))
        # the next sweep sees the same card: the option price holds, no fake drop
        store.upsert(Candidate(source="dhgate", ext_id="2", url="https://x/2",
                               title="NVIDIA DGX Spark 128GB GB10", price=3000.0,
                               currency="USD"), CATALOG)
        r = db.execute("SELECT usd, page_ok FROM listings WHERE key='dhgate:2'").fetchone()
        self.assertEqual((r[0], r[1]), (3155.0, 1))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM price_events "
                                    "WHERE key='dhgate:2'").fetchone()[0], 0)
        # a new card price voids the check
        store.upsert(Candidate(source="dhgate", ext_id="2", url="https://x/2",
                               title="NVIDIA DGX Spark 128GB GB10", price=2900.0,
                               currency="USD"), CATALOG)
        r = db.execute("SELECT usd, page_ok, page_ts FROM listings WHERE key='dhgate:2'").fetchone()
        self.assertEqual((r[0], r[1], r[2]), (2900.0, None, None))

    def test_a_page_that_will_not_load_is_retried_later_not_dropped(self):
        from fp4sweep import pagecheck
        from fp4sweep.run import board
        db, store = self._db()
        with mock.patch.object(pagecheck, "check_row", side_effect=TimeoutError()):
            self.assertEqual(pagecheck.run(store, board, None, budget=10), (0, 0))
        self.assertIn("aliexpress:1", [r["key"] for r in store.active()])


if __name__ == "__main__":
    unittest.main()
