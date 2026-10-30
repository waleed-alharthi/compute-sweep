"""The legitimacy gate: an LLM reads each new listing the way a buyer would.

Regexes get a title into the catalog; they cannot tell an article from an
ad, a carrier board from a dev kit, a rental from a sale, or "8x A100 server"
from "A100 heatsink". Each listing is judged once per title (verdicts are
cached by key and reset when the title changes), in batches, through the
LiteLLM gateway by alias.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

GATEWAY = "http://127.0.0.1:4100/v1/chat/completions"
KEY_FILE = Path.home() / ".config" / "opencode" / "litellm.key"
MODEL = "gemini-flash"
FALLBACK = "deepseek-4.1-flash"   # same job, different upstream

PROMPT = """You vet marketplace listings for someone buying AI inference hardware.
Today is {today}. Your training data may be older than these products: ALL of
these are released, shipping, and sold new and used right now - never reject a
listing for being "unreleased", "non-existent" or "a preorder" because of the
product name: NVIDIA DGX Spark / GB10 boxes (ASUS Ascent GX10, Gigabyte
AI TOP ATOM, MSI EdgeXpert, Dell Pro Max GB10), Jetson AGX Thor (T5000/T4000),
RTX 5090, RTX PRO 4000/4500/5000/6000 Blackwell, AMD Ryzen AI Max+ 395 boxes,
Intel Arc Pro B60 and B70 (32GB), AMD Radeon AI PRO R9700 and R9600D (32GB),
Radeon PRO W7800 48GB, NVIDIA L20 48GB, Apple M4/M5 Max and M3/M5 Ultra Macs,
Jetson T4000 (64GB) and T5000 (128GB) modules, Tenstorrent Blackhole and
QuietBox, Huawei Atlas 300I Duo, and Chinese GPU memory mods (RTX 4090 48GB,
RTX 4080 Super 32GB, RTX 5090 96GB).

For each listing decide if it is a REAL OFFER TO SELL a COMPLETE, WORKING
accelerator/GPU/AI computer that a buyer can purchase now at the stated price.

Answer ok=false for: news/blog/review/guide/FAQ/comparison pages, price-tracker
or "where to buy" pages, wanted/buying ads, rentals/cloud/hourly pricing,
parts (carrier/base boards, coolers, fans, brackets, shrouds, risers, cables,
PCBs, empty boxes, "for parts", broken/faulty/dead units, cores without
memory), accounts/services, preorder deposits, listings whose price is
clearly a placeholder (e.g. 1, 999999) or for something else, bundle-bait
promos ("buy 5 get 3 free", "lowest price" with a price far under market for a
datacenter card), and multi-SKU catalog pages that list several different
products at once ("X 96G Y 48GB Z 32GB") without saying which one the price is
for - those quote a per-unit price only reachable on bulk orders or hide which
SKU is being sold.

For ok=true also return:
- units: number of accelerators included at that price (1 unless the listing
  is a multi-GPU server/workstation/lot)
- vram_total: total GPU memory in GB across all units at that price (for
  unified-memory machines, total unified memory) - MEMORY ONLY, never SSD or
  storage: "36GB RAM 512GB SSD" is a 36GB machine. Use the title; if the title
  states memory, trust it over your assumptions. When a title lists several
  memory variants ("48G 96G", "16GB 32GB"), the price shown is for the
  SMALLEST variant - use that.
- make: chip maker (NVIDIA, AMD, Intel, Huawei, Apple, Tenstorrent)
- model: the accelerator model, normalised, no brand fluff
  (e.g. "Tesla P40", "RTX 3090", "Instinct MI50", "Atlas 300I Duo",
  "Ryzen AI Max+ 395", "GB10", "Jetson AGX Thor", "M3 Ultra")
- vram_unit: memory in GB of ONE accelerator (vram_total / units)
- needs: "" if it plugs into a normal PC (PCIe card, complete computer);
  "sxm" for SXM/SXM2/SXM4/OAM modules that need a special server board;
  "server" for passive datacenter cards that need server airflow.

Return ONLY a JSON array, one object per input, same order:
[{"i":0,"ok":true,"make":"NVIDIA","model":"Tesla V100","units":1,"vram_unit":32,"vram_total":32,"needs":"","why":"short reason"}, ...]

Listings:
"""


def _call(text: str, model: str = MODEL, timeout: float = 90.0) -> str:
    """One gateway round trip under a hard wall-clock limit: urlopen's own
    timeout is per socket read, so a stalled upstream could hang forever."""
    import concurrent.futures as cf
    body = json.dumps({
        "model": model, "max_tokens": 16000, "temperature": 0,
        "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(GATEWAY, data=body, headers={
        "content-type": "application/json",
        "authorization": "Bearer " + KEY_FILE.read_text().strip()})

    def go():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]
    pool = cf.ThreadPoolExecutor(1)
    try:
        return pool.submit(go).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _parse(text: str) -> list[dict]:
    """Every complete {...} object in the reply. A reply cut short still
    yields the verdicts it finished; the rest stay pending."""
    out = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            out.append(json.loads(m.group(0)))
        except json.JSONDecodeError:
            continue
    return out


def _judge(rows) -> list[dict] | None:
    """One batch through the primary, then the fallback. An empty reply is
    a failure too: a reasoning model that spends its whole token budget
    deliberating returns content "" (finish_reason=length), and treating
    that as "no verdicts" re-sent the same batch until max_batches ran out."""
    lines = [f'{i}. [{r["source"]}] "{r["title"]}" - {r["price"]:g} {r["currency"]}'
             for i, r in enumerate(rows)]
    prompt = PROMPT.replace("{today}", time.strftime("%Y-%m-%d")) + "\n".join(lines)
    for model in (MODEL, FALLBACK):
        try:
            out = _parse(_call(prompt, model))
        except Exception as exc:
            # a wall-clock timeout stringifies to "": name the type
            print(f"  [verify] {model} failed: {type(exc).__name__} "
                  f"{str(exc)[:100]}", flush=True)
            continue
        if out:
            return out
        print(f"  [verify] {model} returned no verdicts ({len(rows)} rows)", flush=True)
    return None


def run(store, batch: int = 20, max_batches: int = 6) -> tuple[int, int]:
    """Judge up to batch*max_batches pending listings. Returns (ok, rejected).
    The pool is fetched once and walked in order, so a batch no model can
    answer is never re-sent within a run; it is split into fives instead,
    which keeps a few hard titles from blocking every row behind them."""
    ok = bad = 0
    pool = list(store.pending_verdicts(batch * max_batches))
    queue = [pool[i:i + batch] for i in range(0, len(pool), batch)]
    failed_in_a_row = 0
    while queue:
        rows = queue.pop(0)
        out = _judge(rows)
        if out is None:
            failed_in_a_row += 1
            if failed_in_a_row >= 3:
                print("  [verify] upstreams not answering: rest stays pending", flush=True)
                break
            if len(rows) > 5:
                queue[0:0] = [rows[i:i + 5] for i in range(0, len(rows), 5)]
            continue
        failed_in_a_row = 0
        # models drift between "i" and "id" as the index key; accept both,
        # else the whole batch stays pending and this batch is retried forever
        by_i = {}
        seq = 0
        for o in out:
            if not isinstance(o, dict):
                continue
            k = o.get("i", o.get("id"))
            k = int(k) if isinstance(k, (int, float)) else seq
            by_i.setdefault(k, o)
            seq += 1
        for i, r in enumerate(rows):
            o = by_i.get(i)
            if o is None:
                continue  # stays pending, retried next sweep
            good = bool(o.get("ok"))
            vt = o.get("vram_total")
            vt = int(vt) if isinstance(vt, (int, float)) and vt >= 8 else None
            units = o.get("units")
            units = int(units) if isinstance(units, (int, float)) and units >= 1 else None
            vu = o.get("vram_unit")
            vu = int(vu) if isinstance(vu, (int, float)) and vu >= 8 else None
            if vt and units and vu and abs(vt - units * vu) > 0.25 * max(vt, units * vu):
                # the two reads disagree (storage size read as memory, or a
                # stale total): the per-unit memory is the one worth trusting
                vt = units * vu
            needs = str(o.get("needs") or "").lower()
            needs = needs if needs in ("sxm", "server") else ""
            store.set_verdict(r["key"], good, units, vt,
                              str(o.get("why") or "")[:200], MODEL, needs,
                              str(o.get("make") or "")[:30],
                              str(o.get("model") or "")[:60])
            ok += good
            bad += not good
        time.sleep(1)
    return ok, bad
