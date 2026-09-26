"""Candidate listings and price parsing."""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

CURRENCY_SYMBOLS = {
    "$": "USD", "€": "EUR", "£": "GBP",
    "ر\\.?ع": "OMR", "ر\\.?س": "SAR", "در\\.?إ": "AED", "د\\.?ك": "KWD",
    "د\\.?ب": "BHD", "ر\\.?ق": "QAR", "د\\.?إ": "AED",
}
CODE_RE = re.compile(r"\b(USD|EUR|GBP|OMR|SAR|AED|KWD|BHD|QAR|JOD|EGP|CNY|INR|JPY|CAD|AUD|CHF)\b")


@dataclass
class Candidate:
    source: str            # adapter id: opensooq, haraj, serp, tenstorrent...
    ext_id: str            # id stable within the source (url hash if none)
    url: str
    title: str
    price: float | None
    currency: str | None
    region: str = ""       # free text: city/country
    country: str = ""      # om/sa/ae/kw/bh/qa/us/...
    condition: str = ""    # new | used | ""
    stock: str = ""        # in_stock | ""
    evidence: str = "direct"
    meta: dict = field(default_factory=dict)


def normalize_digits(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").translate(AR_DIGITS)


def parse_price(text: str, default_currency: str | None = None) -> tuple[float, str] | None:
    """Find the first plausible price in a string and name its currency.

    Comma/dot thousands ambiguity is resolved by the group shape: a trailing
    3-digit group after a separator is thousands, anything else is a decimal.
    """
    if not text:
        return None
    text = normalize_digits(html.unescape(text))
    if default_currency and CODE_RE.search(text.upper()):
        cur = CODE_RE.search(text.upper()).group(1)
    else:
        cur = default_currency
        for sym, code in CURRENCY_SYMBOLS.items():
            if re.search(sym, text):
                cur = code
                break
    if cur is None and CODE_RE.search(text.upper()):
        cur = CODE_RE.search(text.upper()).group(1)
    if cur is None:
        return None
    m = re.search(r"(\d[\d,\s.]*\d|\d)", text.replace("\u00a0", " "))
    if not m:
        return None
    raw = m.group(1).replace(" ", "")
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", raw):
        value = float(re.sub(r"[,.]", "", raw))
    elif "," in raw and "." in raw:
        # last separator wins as the decimal one
        if raw.rfind(",") > raw.rfind("."):
            value = float(raw.replace(".", "").replace(",", "."))
        else:
            value = float(raw.replace(",", ""))
    elif "," in raw:
        value = float(raw.replace(",", ".")) if re.fullmatch(r"\d+,\d{1,2}", raw) \
            else float(raw.replace(",", ""))
    elif "." in raw:
        value = float(raw.replace(".", "")) if re.fullmatch(r"\d+\.\d{3}(\.\d{3})*", raw) \
            else float(raw)
    else:
        value = float(raw)
    if value <= 0:
        return None
    return value, cur
