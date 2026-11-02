"""Tolerant parsers for amounts and dates as they appear on real invoices."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_CURRENCY = {"€": "EUR", "$": "USD", "£": "GBP", "EUR": "EUR", "USD": "USD", "GBP": "GBP"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d.%m.%Y",
    "%d/%m/%Y",
)


def parse_amount(text: str | None) -> Decimal | None:
    """Parse '€1,488.00', '1.488,00 EUR', '3 000,50' or '1488' into a Decimal.

    The last '.' or ',' followed by exactly two digits is treated as the
    decimal separator; every other separator is a thousands separator.
    """
    if not text:
        return None
    s = re.sub(r"[^\d.,\-]", "", text)
    if not s or not re.search(r"\d", s):
        return None
    m = re.search(r"[.,](\d{2})$", s)
    if m:
        whole = re.sub(r"[.,]", "", s[: m.start()])
        s = f"{whole}.{m.group(1)}"
    else:
        s = re.sub(r"[.,]", "", s)
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def detect_currency(text: str | None) -> str | None:
    if not text:
        return None
    for token, code in _CURRENCY.items():
        if token in text:
            return code
    return None


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    cleaned = text.strip().rstrip(".")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None
