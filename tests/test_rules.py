from datetime import date
from decimal import Decimal

from invoice_intake.models import Decision, InvoiceData
from invoice_intake.rules import Policy, decide, normalise_name, run_checks

VENDORS = {normalise_name("Northwind Supplies Ltd"): "V-0193"}
POLICY = Policy()
TODAY = date(2026, 10, 10)
CONF = {k: 0.99 for k in ("supplier", "invoice_number", "issue_date", "due_date", "net", "tax", "total")}


def invoice(**overrides) -> InvoiceData:
    base = dict(
        supplier="Northwind Supplies Ltd",
        invoice_number="INV-1",
        issue_date=date(2026, 10, 2),
        due_date=date(2026, 11, 1),
        currency="EUR",
        net=Decimal("100.00"),
        tax=Decimal("20.00"),
        total=Decimal("120.00"),
        confidence=dict(CONF),
    )
    base.update(overrides)
    return InvoiceData(**base)


def route(data: InvoiceData, duplicate: bool = False):
    checks = run_checks(data, known_vendors=VENDORS, is_duplicate=duplicate, policy=POLICY, today=TODAY)
    return decide(data, checks, POLICY)


def test_clean_invoice_auto_posts():
    decision, _ = route(invoice())
    assert decision is Decision.AUTO_POST


def test_arithmetic_mismatch_goes_to_review():
    decision, reasons = route(invoice(total=Decimal("125.00")))
    assert decision is Decision.REVIEW
    assert any("not 125.00" in r for r in reasons)


def test_one_cent_rounding_is_tolerated():
    decision, _ = route(invoice(total=Decimal("120.01")))
    assert decision is Decision.AUTO_POST


def test_duplicate_is_rejected_even_if_otherwise_valid():
    decision, _ = route(invoice(), duplicate=True)
    assert decision is Decision.REJECT


def test_unknown_supplier_goes_to_review():
    decision, _ = route(invoice(supplier="Someone Else Inc"))
    assert decision is Decision.REVIEW


def test_low_confidence_goes_to_review():
    decision, reasons = route(invoice(confidence={**CONF, "total": 0.6}))
    assert decision is Decision.REVIEW
    assert any("total (60%)" in r for r in reasons)


def test_missing_required_field_goes_to_review():
    decision, reasons = route(invoice(invoice_number=None))
    assert decision is Decision.REVIEW
    assert any("invoice_number" in r for r in reasons)


def test_due_before_issue_goes_to_review():
    decision, _ = route(invoice(due_date=date(2026, 9, 1)))
    assert decision is Decision.REVIEW


def test_future_issue_date_goes_to_review():
    decision, _ = route(invoice(issue_date=date(2027, 1, 1), due_date=date(2027, 2, 1)))
    assert decision is Decision.REVIEW


def test_over_limit_goes_to_review():
    decision, reasons = route(invoice(net=Decimal("2000"), tax=Decimal("400"), total=Decimal("2400")))
    assert decision is Decision.REVIEW
    assert "auto-post limit" in reasons[0]


def test_supplier_matching_ignores_case_and_punctuation():
    decision, _ = route(invoice(supplier="NORTHWIND SUPPLIES, LTD."))
    assert decision is Decision.AUTO_POST
