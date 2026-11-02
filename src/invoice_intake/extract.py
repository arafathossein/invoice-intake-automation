"""Field extraction.

Two extractors share one output schema:

* ``RuleExtractor`` — deterministic label matching. No network, no API key;
  good for clearly labelled invoices and for tests.
* ``LLMExtractor`` — any OpenAI-compatible chat API (OpenAI, Azure OpenAI,
  or a local model served by Ollama / vLLM / LM Studio). The model returns
  fields plus its own confidence for each one.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

import httpx

from .models import InvoiceData
from .parsing import detect_currency, parse_amount, parse_date


class Extractor(Protocol):
    name: str

    def extract(self, text: str) -> InvoiceData: ...


# ── Rule-based ──────────────────────────────────────────────────────────────

_LABELS: dict[str, list[str]] = {
    "supplier": [r"supplier", r"from", r"vendor", r"seller"],
    "invoice_number": [r"invoice\s*(?:no\.?|number|#)", r"rechnung(?:s-?nr\.?|\s*nr\.?)", r"inv\s*#"],
    "issue_date": [r"invoice\s*date", r"issue\s*date", r"date\s*of\s*issue", r"rechnungsdatum"],
    "due_date": [r"due\s*date", r"payment\s*due", r"due", r"fällig(?:keit)?"],
    "net": [r"subtotal", r"net(?:\s*amount)?", r"netto"],
    "tax": [r"vat(?:\s*\d+\s*%)?", r"tax(?:\s*\d+\s*%)?", r"mwst\.?(?:\s*\d+\s*%)?", r"ust\.?(?:\s*\d+\s*%)?"],
    "total": [r"total\s*due", r"amount\s*due", r"grand\s*total", r"total", r"gesamt(?:betrag)?"],
}


def _find(text: str, field: str) -> str | None:
    for label in _LABELS[field]:
        m = re.search(rf"^\s*{label}\s*[:\-]?\s*(.+?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1)
    return None


class RuleExtractor:
    name = "rules"

    def extract(self, text: str) -> InvoiceData:
        data = InvoiceData()
        conf: dict[str, float] = {}

        supplier = _find(text, "supplier")
        if supplier:
            data.supplier, conf["supplier"] = supplier.strip(), 0.97
        else:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
            if first and not re.search(r"invoice|rechnung", first, re.I):
                data.supplier, conf["supplier"] = first, 0.6  # heuristic: letterhead line

        raw_no = _find(text, "invoice_number")
        if raw_no:
            data.invoice_number, conf["invoice_number"] = raw_no.split()[0].strip(), 0.97

        for field in ("issue_date", "due_date"):
            raw = _find(text, field)
            parsed = parse_date(raw)
            if parsed:
                setattr(data, field, parsed)
                conf[field] = 0.96
            elif raw:
                conf[field] = 0.3  # label found, value unreadable

        for field in ("net", "tax", "total"):
            raw = _find(text, field)
            amount = parse_amount(raw)
            if amount is not None:
                setattr(data, field, amount)
                conf[field] = 0.97
                data.currency = data.currency or detect_currency(raw)

        data.currency = data.currency or detect_currency(text)
        data.confidence = conf
        return data


# ── LLM-based ───────────────────────────────────────────────────────────────

_SYSTEM = """You extract fields from supplier invoices. Reply with JSON only, matching:
{"supplier": str|null, "invoice_number": str|null, "issue_date": "YYYY-MM-DD"|null,
 "due_date": "YYYY-MM-DD"|null, "currency": "ISO 4217 code"|null,
 "net": number|null, "tax": number|null, "total": number|null,
 "confidence": {"<field>": 0.0-1.0, ...}}
Rules: copy values exactly as printed; never compute or guess a missing value — use null.
Give a lower confidence when text is unclear, handwritten, ambiguous or inferred."""


class LLMExtractor:
    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str, client: httpx.Client | None = None, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=timeout)

    def extract(self, text: str) -> InvoiceData:
        resp = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": text[:20000]},
                ],
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        raw = json.loads(_strip_fences(content))

        def amt(v: object):
            return parse_amount(str(v)) if v not in (None, "") else None

        conf = {k: max(0.0, min(1.0, float(v))) for k, v in (raw.get("confidence") or {}).items()}
        return InvoiceData(
            supplier=raw.get("supplier") or None,
            invoice_number=(str(raw["invoice_number"]) if raw.get("invoice_number") else None),
            issue_date=parse_date(raw.get("issue_date")),
            due_date=parse_date(raw.get("due_date")),
            currency=raw.get("currency") or None,
            net=amt(raw.get("net")),
            tax=amt(raw.get("tax")),
            total=amt(raw.get("total")),
            confidence=conf,
        )


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    return s
