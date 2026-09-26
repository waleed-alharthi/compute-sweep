"""Where everything lives and a few global knobs."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.environ.get("FP4SWEEP_HOME", Path.home() / "fp4sweep"))
STATE = Path(os.environ.get("FP4SWEEP_STATE",
                           Path.home() / ".local" / "state" / "fp4sweep"))
DB_PATH = STATE / "market.db"
EXPORT_PATH = STATE / "market.json"
CONFIG_DIR = Path.home() / ".config" / "fp4sweep"

SEARXNG = os.environ.get("FP4_SEARXNG", "http://127.0.0.1:8888")
BROWSERLESS = os.environ.get("FP4_BROWSERLESS", "http://127.0.0.1:9222")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")

MIN_VRAM = 24
# currencies we expect to see in the Gulf + the usual retail countries
FX_BASE = "USD"
CURRENCIES = ("USD EUR GBP OMR SAR AED KWD BHD QAR JOD EGP CNY INR JPY CAD AUD CHF")
