"""Validation checks and the routing policy.

Checks never change data; they only report. The policy turns the check
results into one decision with human-readable reasons, so every routing
decision can be explained after the fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .models import REQUIRED_FIELDS, SCORED_FIELDS, Check, Decision, InvoiceData

TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class Policy:
    confidence_threshold: float = 0.90
    auto_post_limit: Decimal = Decimal("2000.00")


def normalise_name(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def run_checks(
    data: InvoiceData,
    *,
    known_vendors: dict[str, str],
    is_duplicate: bool,
    policy: Policy,
    today: date | None = None,
) -> list[Check]:
    today = today or date.today()
    checks: list[Check] = []

    missing = [f for f in REQUIRED_FIELDS if getattr(data, f) is None]
    checks.append(
        Check(
            name="required_fields",
            passed=not missing,
            detail="All required fields present" if not missing else f"Missing: {', '.join(missing)}",
        )
    )

    if data.net is not None and data.tax is not None and data.total is not None:
        expected = data.net + data.tax
        ok = abs(expected - data.total) <= TOLERANCE
        checks.append(
            Check(
                name="totals_add_up",
                passed=ok,
                detail=f"{data.net} + {data.tax} = {expected}" + ("" if ok else f", not {data.total}"),
            )
        )
    else:
        checks.append(Check(name="totals_add_up", passed=False, detail="Net, tax or total missing — cannot verify"))

    date_problems = []
    if data.issue_date and data.issue_date > today + timedelta(days=1):
        date_problems.append("issue date is in the future")
    if data.issue_date and data.due_date and data.due_date < data.issue_date:
        date_problems.append("due date is before issue date")
    if data.due_date is None:
        date_problems.append("due date missing or unreadable")
    checks.append(
        Check(
            name="dates_valid",
            passed=not date_problems,
            detail="Dates are consistent" if not date_problems else "; ".join(date_problems).capitalize(),
        )
    )

    vendor_id = known_vendors.get(normalise_name(data.supplier))
    checks.append(
        Check(
            name="known_supplier",
            passed=vendor_id is not None,
            detail=f"Matched to vendor {vendor_id}" if vendor_id else f"'{data.supplier}' is not in the vendor list",
        )
    )

    checks.append(
        Check(
            name="not_duplicate",
            passed=not is_duplicate,
            detail="No earlier invoice with this number from this supplier"
            if not is_duplicate
            else f"Invoice {data.invoice_number} from this supplier was already received",
        )
    )

    low = [f"{f} ({data.conf(f):.0%})" for f in SCORED_FIELDS if getattr(data, f) is not None and data.conf(f) < policy.confidence_threshold]
    checks.append(
        Check(
            name="confidence",
            passed=not low,
            detail=f"All fields ≥ {policy.confidence_threshold:.0%}" if not low else f"Low confidence: {', '.join(low)}",
        )
    )
    return checks


def decide(data: InvoiceData, checks: list[Check], policy: Policy) -> tuple[Decision, list[str]]:
    by_name = {c.name: c for c in checks}
    if not by_name["not_duplicate"].passed:
        return Decision.REJECT, [by_name["not_duplicate"].detail]

    failed = [c.detail for c in checks if not c.passed]
    if failed:
        return Decision.REVIEW, failed

    if data.total is not None and data.total > policy.auto_post_limit:
        return Decision.REVIEW, [f"Total {data.total} is above the auto-post limit of {policy.auto_post_limit}"]

    return Decision.AUTO_POST, [f"All checks passed and total is within the {policy.auto_post_limit} limit"]
