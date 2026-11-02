"""Data models shared by every stage of the pipeline."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

# Fields every invoice must have before it can be posted.
REQUIRED_FIELDS = ("supplier", "invoice_number", "issue_date", "total")
# Fields whose extraction confidence is checked against the threshold.
SCORED_FIELDS = ("supplier", "invoice_number", "issue_date", "due_date", "net", "tax", "total")


class InvoiceData(BaseModel):
    """Structured fields extracted from one invoice, each with a 0–1 confidence."""

    supplier: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    net: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    confidence: dict[str, float] = Field(default_factory=dict)

    def conf(self, field: str) -> float:
        return self.confidence.get(field, 0.0) if getattr(self, field) is not None else 0.0


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class Decision(str, Enum):
    AUTO_POST = "auto_post"  # every check passed and the amount is under the limit
    REVIEW = "review"  # a person must look at it before anything is posted
    REJECT = "reject"  # must not be posted (e.g. a duplicate)


class Result(BaseModel):
    id: str
    source: str
    received_at: datetime
    extractor: str
    data: InvoiceData
    checks: list[Check]
    decision: Decision
    reasons: list[str]
