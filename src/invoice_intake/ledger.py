"""Posting approved invoices to the accounting system, safely under retries.

The failure this guards against: the accounting system saves the bill, but
the response never arrives (timeout, dropped connection, worker restart).
The workflow retries, and a naive retry creates a second bill, which is
how invoices get paid twice.

Three layers, each enough on its own for most cases:

1. A local posting ledger (SQLite). An invoice already marked "posted" is
   never sent again.
2. Look before create. Every attempt first searches the accounting system
   for a bill with the same supplier and invoice number. If the previous
   attempt "failed" after the bill was saved, this finds it.
3. An idempotency key derived from supplier + invoice number, sent with the
   create request, so the accounting system itself can drop a duplicate
   request that arrives while the first is still in flight.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol

import httpx

from .models import Result
from .rules import normalise_name


@dataclass
class Bill:
    supplier: str
    invoice_number: str
    issue_date: date
    due_date: date | None
    currency: str | None
    net: Decimal | None
    tax: Decimal | None
    total: Decimal
    result_id: str

    @classmethod
    def from_result(cls, r: Result) -> "Bill":
        d = r.data
        missing = [f for f in ("supplier", "invoice_number", "issue_date", "total") if getattr(d, f) is None]
        if missing:
            raise ValueError(f"cannot post an invoice without {', '.join(missing)}")
        return cls(d.supplier, d.invoice_number.strip(), d.issue_date, d.due_date, d.currency, d.net, d.tax, d.total, r.id)


def idempotency_key(supplier: str, invoice_number: str) -> str:
    """Same supplier + invoice number always gives the same key, across retries and restarts."""
    digest = hashlib.sha256(f"{normalise_name(supplier)}|{invoice_number.strip()}".encode()).hexdigest()
    return f"invoice-intake-{digest[:40]}"


class LedgerClient(Protocol):
    name: str

    def find_bill(self, supplier: str, invoice_number: str) -> str | None:
        """Return the id of an existing live bill, or None."""

    def create_bill(self, bill: Bill, idempotency_key: str) -> str:
        """Create the bill and return its id."""


@dataclass
class PostResult:
    status: str  # "created", "found_existing" or "already_posted"
    bill_id: str
    attempts: int
    idempotency_key: str


class PostingError(RuntimeError):
    """The post may or may not have reached the ledger. Retrying is safe."""


class PostingLedger:
    """Local record of what has been sent to the accounting system."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS postings (
                 supplier TEXT, number TEXT, state TEXT, bill_id TEXT, attempts INTEGER,
                 result_id TEXT, updated_at TEXT, PRIMARY KEY (supplier, number))"""
        )
        self._db.commit()

    def get(self, supplier: str, number: str) -> tuple[str, str | None, int] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT state, bill_id, attempts FROM postings WHERE supplier = ? AND number = ?",
                (normalise_name(supplier), number.strip()),
            ).fetchone()
        return tuple(row) if row else None

    def mark_pending(self, bill: Bill) -> int:
        key = (normalise_name(bill.supplier), bill.invoice_number)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """INSERT INTO postings VALUES (?, ?, 'pending', NULL, 1, ?, ?)
                   ON CONFLICT (supplier, number) DO UPDATE SET attempts = attempts + 1, updated_at = excluded.updated_at""",
                (*key, bill.result_id, now),
            )
            self._db.commit()
            return self._db.execute("SELECT attempts FROM postings WHERE supplier = ? AND number = ?", key).fetchone()[0]

    def mark_posted(self, bill: Bill, bill_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE postings SET state = 'posted', bill_id = ?, updated_at = ? WHERE supplier = ? AND number = ?",
                (bill_id, datetime.now(timezone.utc).isoformat(), normalise_name(bill.supplier), bill.invoice_number),
            )
            self._db.commit()


class Poster:
    def __init__(self, client: LedgerClient, ledger: PostingLedger):
        self.client = client
        self.ledger = ledger

    def post(self, bill: Bill) -> PostResult:
        key = idempotency_key(bill.supplier, bill.invoice_number)
        row = self.ledger.get(bill.supplier, bill.invoice_number)
        if row and row[0] == "posted":
            return PostResult("already_posted", row[1] or "", row[2], key)

        attempts = self.ledger.mark_pending(bill)
        try:
            existing = self.client.find_bill(bill.supplier, bill.invoice_number)
            if existing:
                self.ledger.mark_posted(bill, existing)
                return PostResult("found_existing", existing, attempts, key)
            bill_id = self.client.create_bill(bill, key)
        except (httpx.HTTPError, OSError) as exc:
            # Left as "pending": the next attempt looks the bill up before creating anything.
            raise PostingError(f"{self.client.name} did not confirm the post ({exc.__class__.__name__}); retrying is safe") from exc
        self.ledger.mark_posted(bill, bill_id)
        return PostResult("created", bill_id, attempts, key)


def _xero_str(value: str) -> str:
    # Xero "where" filters are C#-like expressions with double-quoted strings.
    return value.replace("\\", "\\\\").replace('"', '\\"')


class XeroClient:
    """Bills (ACCPAY invoices) in Xero via the Accounting API.

    Xero access tokens expire after 30 minutes, so the token is read through
    a callable on every request: point it at whatever refreshes your token
    (a file your OAuth helper rewrites, a secrets manager, n8n's credential).
    """

    name = "Xero"

    def __init__(self, token: Callable[[], str], tenant_id: str, *, status: str = "DRAFT",
                 account_code: str | None = None, base_url: str = "https://api.xero.com/api.xro/2.0",
                 client: httpx.Client | None = None, timeout: float = 30):
        if status not in ("DRAFT", "SUBMITTED", "AUTHORISED"):
            raise ValueError("status must be DRAFT, SUBMITTED or AUTHORISED")
        self.token = token
        self.tenant_id = tenant_id
        self.status = status
        self.account_code = account_code
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "xero-tenant-id": self.tenant_id, "Accept": "application/json", **extra}

    def find_bill(self, supplier: str, invoice_number: str) -> str | None:
        where = f'Type=="ACCPAY" AND InvoiceNumber=="{_xero_str(invoice_number)}"'
        resp = self.client.get(f"{self.base_url}/Invoices", params={"where": where}, headers=self._headers())
        resp.raise_for_status()
        wanted = normalise_name(supplier)
        for inv in resp.json().get("Invoices", []):
            if inv.get("Status") in ("DELETED", "VOIDED"):
                continue
            if normalise_name((inv.get("Contact") or {}).get("Name", "")) == wanted:
                return inv["InvoiceID"]
        return None

    def create_bill(self, bill: Bill, idempotency_key: str) -> str:
        net = bill.net if bill.net is not None else bill.total - (bill.tax or Decimal(0))
        line = {"Description": f"Invoice {bill.invoice_number}", "Quantity": 1, "UnitAmount": str(net)}
        if bill.tax is not None:
            line["TaxAmount"] = str(bill.tax)
        if self.account_code:
            line["AccountCode"] = self.account_code
        invoice = {
            "Type": "ACCPAY",
            "Contact": {"Name": bill.supplier},
            "InvoiceNumber": bill.invoice_number,
            "Date": bill.issue_date.isoformat(),
            "LineAmountTypes": "Exclusive",
            "LineItems": [line],
            "Status": self.status,
            "Reference": f"invoice-intake {bill.result_id}",
        }
        if bill.due_date:
            invoice["DueDate"] = bill.due_date.isoformat()
        if bill.currency:
            invoice["CurrencyCode"] = bill.currency
        resp = self.client.put(
            f"{self.base_url}/Invoices",
            json={"Invoices": [invoice]},
            headers=self._headers(**{"Idempotency-Key": idempotency_key, "Content-Type": "application/json"}),
        )
        resp.raise_for_status()
        return resp.json()["Invoices"][0]["InvoiceID"]


def token_from_env_or_file(env_value: str | None, file_path: str | None) -> Callable[[], str]:
    def get() -> str:
        if file_path:
            return Path(file_path).read_text(encoding="utf-8").strip()
        if env_value:
            return env_value
        raise RuntimeError("Set XERO_ACCESS_TOKEN or XERO_TOKEN_FILE")

    return get
