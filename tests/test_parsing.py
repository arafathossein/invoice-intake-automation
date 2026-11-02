from datetime import date
from decimal import Decimal

import pytest

from invoice_intake.parsing import detect_currency, parse_amount, parse_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("€1,488.00", "1488.00"),
        ("3.000,00 EUR", "3000.00"),
        ("$12,402.50", "12402.50"),
        ("1 250,75", "1250.75"),
        ("1488", "1488.00"),
        ("570,00 EUR", "570.00"),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "n/a", "see note"])
def test_parse_amount_rejects_non_numbers(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("02 Oct 2026", date(2026, 10, 2)),
        ("28.09.2026", date(2026, 9, 28)),
        ("2026-11-01", date(2026, 11, 1)),
        ("October 7, 2026", date(2026, 10, 7)),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_unreadable():
    assert parse_date("end of month (see note)") is None


def test_detect_currency():
    assert detect_currency("Total: €10") == "EUR"
    assert detect_currency("3.000,00 EUR") == "EUR"
    assert detect_currency("no currency") is None
