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
Intel Arc Pro B60, Apple M4/M5 Max and M3 Ultra Macs, Tenstorrent Blackhole,
Huawei Atlas 300I Duo, and Chinese 48GB RTX 4090 mods.

For each listing decide if it is a REAL OFFER TO SELL a COMPLETE, WORKING
accelerator/GPU/AI computer that a buyer can purchase now at the stated price.

Answer ok=false for: news/blog/review/guide/FAQ/comparison pages, price-tracker
or "where to buy" pages, wanted/buying ads, rentals/cloud/hourly pricing,
parts (carrier/base boards, coolers, fans, brackets, shrouds, risers, cables,
PCBs, empty boxes, "for parts", broken/faulty/dead units, cores without
memory), accounts/services, preorder deposits, listings whose price is
clearly a placeholder (e.g. 1, 999999) or for something else, and bundle-bait
promos ("buy 5 get 3 free", "lowest price" with a price far under market for a
datacenter card) - those quote a per-unit price only reachable on bulk orders.

For ok=true also return:
- units: number of accelerators included at that price (1 unless the listing
  is a multi-GPU server/workstation/lot)
- vram_total: total GPU memory in GB across all units at that price (for
  unified-memory machines, total unified memory). Use the title; if the title
  states memory, trust it over your assumptions. When a title lists several
  memory variants ("48G 96G", "16GB 32GB"), the price shown is for the
  SMALLEST variant - use that.
- needs: "" if it plugs into a normal PC (PCIe card, complete computer);
  "sxm" for SXM/SXM2/SXM4/OAM modules that need a special server board;
  "server" for passive datacenter cards that need server airflow.

Return ONLY a JSON array, one object per input, same order:
[{"i":0,"ok":true,"units":1,"vram_total":32,"needs":"","why":"short reason"}, ...]

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


def run(store, batch: int = 20, max_batches: int = 6) -> tuple[int, int]:
    """Judge up to batch*max_batches pending listings. Returns (ok, rejected)."""
    ok = bad = 0
    for _ in range(max_batches):
        rows = store.pending_verdicts(batch)
        if not rows:
            break
        lines = [f'{i}. [{r["source"]}] "{r["title"]}" - {r["price"]:g} {r["currency"]}'
                 for i, r in enumerate(rows)]
        prompt = PROMPT.replace("{today}", time.strftime("%Y-%m-%d")) + "\n".join(lines)
        out = None
        for model in (MODEL, FALLBACK):
            try:
                out = _parse(_call(prompt, model))
                break
            except Exception as exc:
                print(f"  [verify] {model} failed: {str(exc)[:100]}", flush=True)
        if out is None:
            break  # both upstreams down: leave the rest pending for next sweep
        by_i = {o.get("i"): o for o in out if isinstance(o, dict)}
        for i, r in enumerate(rows):
            o = by_i.get(i)
            if o is None:
                continue  # stays pending, retried next sweep
            good = bool(o.get("ok"))
            vt = o.get("vram_total")
            vt = int(vt) if isinstance(vt, (int, float)) and vt >= 8 else None
            units = o.get("units")
            units = int(units) if isinstance(units, (int, float)) and units >= 1 else None
            needs = str(o.get("needs") or "").lower()
            needs = needs if needs in ("sxm", "server") else ""
            store.set_verdict(r["key"], good, units, vt,
                              str(o.get("why") or "")[:200], MODEL, needs)
            ok += good
            bad += not good
        time.sleep(1)
    return ok, bad
