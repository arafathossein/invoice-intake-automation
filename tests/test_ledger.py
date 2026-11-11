"""Posting to the ledger must never create a second bill, whatever fails in between."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from invoice_intake import api
from invoice_intake.ledger import Bill, Poster, PostingError, PostingLedger, XeroClient, idempotency_key
from invoice_intake.pipeline import Pipeline

from .conftest import sample


def make_bill(**kw) -> Bill:
    base = dict(supplier="Northwind Supplies Ltd", invoice_number="INV-2048", issue_date=date(2026, 10, 2),
                due_date=date(2026, 11, 1), currency="EUR", net=Decimal("1240.00"), tax=Decimal("248.00"),
                total=Decimal("1488.00"), result_id="r1")
    base.update(kw)
    return Bill(**base)


class FakeLedger:
    """An accounting system that can save a bill and then lose the response."""

    name = "Fake ledger"

    def __init__(self, lose_response_times: int = 0, fail_before_save_times: int = 0):
        self.bills: dict[str, dict] = {}
        self.keys_seen: dict[str, str] = {}
        self.lose_response_times = lose_response_times
        self.fail_before_save_times = fail_before_save_times
        self.creates = 0

    def find_bill(self, supplier, invoice_number):
        for bill_id, b in self.bills.items():
            if b["number"] == invoice_number and b["supplier"].lower() == supplier.lower():
                return bill_id
        return None

    def create_bill(self, bill, key):
        self.creates += 1
        if self.fail_before_save_times:
            self.fail_before_save_times -= 1
            raise httpx.ConnectError("connection refused")
        if key in self.keys_seen:  # like a real idempotent API
            return self.keys_seen[key]
        bill_id = f"bill-{len(self.bills) + 1}"
        self.bills[bill_id] = {"supplier": bill.supplier, "number": bill.invoice_number}
        self.keys_seen[key] = bill_id
        if self.lose_response_times:
            self.lose_response_times -= 1
            raise httpx.ReadTimeout("saved, but the response never arrived")
        return bill_id


@pytest.fixture
def ledger(tmp_path):
    return PostingLedger(tmp_path / "postings.sqlite3")


def test_saved_but_timed_out_then_retried_creates_one_bill(ledger):
    fake = FakeLedger(lose_response_times=1)
    poster = Poster(fake, ledger)
    with pytest.raises(PostingError, match="retrying is safe"):
        poster.post(make_bill())
    assert len(fake.bills) == 1  # the ledger did save it
    retry = poster.post(make_bill())
    assert retry.status == "found_existing" and retry.bill_id == "bill-1" and retry.attempts == 2
    assert len(fake.bills) == 1 and fake.creates == 1


def test_failed_before_save_is_created_on_retry(ledger):
    fake = FakeLedger(fail_before_save_times=1)
    poster = Poster(fake, ledger)
    with pytest.raises(PostingError):
        poster.post(make_bill())
    assert fake.bills == {}
    assert poster.post(make_bill()).status == "created"
    assert len(fake.bills) == 1


def test_posted_invoice_is_never_sent_again(ledger):
    fake = FakeLedger()
    poster = Poster(fake, ledger)
    assert poster.post(make_bill()).status == "created"
    again = poster.post(make_bill(supplier="  NORTHWIND supplies ltd ", invoice_number=" INV-2048 "))
    assert again.status == "already_posted" and again.bill_id == "bill-1"
    assert fake.creates == 1


def test_bill_entered_by_hand_is_found_not_duplicated(ledger):
    fake = FakeLedger()
    fake.bills["manual-7"] = {"supplier": "Northwind Supplies Ltd", "number": "INV-2048"}
    out = Poster(fake, ledger).post(make_bill())
    assert out.status == "found_existing" and out.bill_id == "manual-7" and fake.creates == 0


def test_two_workers_racing_send_the_same_idempotency_key(tmp_path):
    fake = FakeLedger()
    a = Poster(fake, PostingLedger(tmp_path / "a.sqlite3"))
    b = Poster(fake, PostingLedger(tmp_path / "b.sqlite3"))
    # Both looked up before either created: simulate by calling create directly with each poster's key.
    key_a = idempotency_key("Northwind Supplies Ltd", "INV-2048")
    key_b = idempotency_key("northwind supplies ltd", "INV-2048 ")
    assert key_a == key_b
    assert fake.create_bill(make_bill(), key_a) == fake.create_bill(make_bill(), key_b)
    assert len(fake.bills) == 1
    assert a.post(make_bill()).status == b.post(make_bill()).status == "found_existing"


def test_different_suppliers_same_number_are_different_bills(ledger):
    fake = FakeLedger()
    poster = Poster(fake, ledger)
    poster.post(make_bill())
    assert poster.post(make_bill(supplier="Harbor Print Co")).status == "created"
    assert len(fake.bills) == 2


# ---------- Xero client against a mocked Xero API ----------

def xero(handler, **kw) -> XeroClient:
    return XeroClient(lambda: "tok", "tenant-1", client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_xero_lookup_filters_bills_and_skips_voided():
    seen = {}

    def handler(req: httpx.Request):
        seen["where"] = req.url.params["where"]
        seen["headers"] = req.headers
        return httpx.Response(200, json={"Invoices": [
            {"InvoiceID": "v1", "Status": "VOIDED", "Contact": {"Name": "Northwind Supplies Ltd"}},
            {"InvoiceID": "x1", "Status": "AUTHORISED", "Contact": {"Name": "Other Co"}},
            {"InvoiceID": "ok1", "Status": "DRAFT", "Contact": {"Name": "Northwind Supplies Ltd."}},
        ]})

    assert xero(handler).find_bill("Northwind Supplies Ltd", 'INV-"2048"') == "ok1"
    assert seen["where"] == 'Type=="ACCPAY" AND InvoiceNumber=="INV-\\"2048\\""'
    assert seen["headers"]["xero-tenant-id"] == "tenant-1"
    assert seen["headers"]["authorization"] == "Bearer tok"


def test_xero_create_sends_idempotency_key_and_bill():
    seen = {}

    def handler(req: httpx.Request):
        seen["method"] = req.method
        seen["key"] = req.headers.get("Idempotency-Key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"Invoices": [{"InvoiceID": "new-1"}]})

    bill_id = xero(handler, account_code="400").create_bill(make_bill(), "k-123")
    inv = seen["body"]["Invoices"][0]
    assert bill_id == "new-1" and seen["method"] == "PUT" and seen["key"] == "k-123"
    assert inv["Type"] == "ACCPAY" and inv["Status"] == "DRAFT" and inv["InvoiceNumber"] == "INV-2048"
    assert inv["LineItems"][0] == {"Description": "Invoice INV-2048", "Quantity": 1, "UnitAmount": "1240.00",
                                   "TaxAmount": "248.00", "AccountCode": "400"}
    assert inv["DueDate"] == "2026-11-01" and inv["CurrencyCode"] == "EUR"


def test_xero_rejects_unknown_status():
    with pytest.raises(ValueError):
        XeroClient(lambda: "t", "x", status="PAID")


# ---------- HTTP endpoint ----------

@pytest.fixture
def client(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_pipeline", Pipeline(settings))
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    fake = FakeLedger(lose_response_times=1)
    monkeypatch.setattr(api, "_poster", Poster(fake, PostingLedger(tmp_path / "p.sqlite3")))
    c = TestClient(api.app)
    c.fake = fake
    return c


def _result(client, name):
    return client.post("/invoices", files={"file": ("i.pdf", sample(name), "application/pdf")}, params={"dry_run": True}).json()


def test_endpoint_retry_after_lost_response(client):
    result = _result(client, "01_northwind_inv-2048.pdf")
    first = client.post("/ledger/post", json={"result": result})
    assert first.status_code == 502 and "retrying is safe" in first.json()["detail"]
    second = client.post("/ledger/post", json={"result": result})
    assert second.status_code == 200 and second.json()["status"] == "found_existing"
    third = client.post("/ledger/post", json={"result": result})
    assert third.json()["status"] == "already_posted"
    assert len(client.fake.bills) == 1


def test_endpoint_refuses_reject_and_unapproved_review(client):
    result = _result(client, "01_northwind_inv-2048.pdf")
    result["decision"] = "reject"
    assert client.post("/ledger/post", json={"result": result}).status_code == 409
    result["decision"] = "review"
    assert client.post("/ledger/post", json={"result": result}).status_code == 409
    assert client.post("/ledger/post", json={"result": result, "approved_by": "dana"}).status_code in (200, 502)


def test_endpoint_needs_configuration(settings, monkeypatch):
    monkeypatch.setattr(api, "_pipeline", Pipeline(settings))
    monkeypatch.setattr(api, "_poster", None)
    monkeypatch.delenv("XERO_TENANT_ID", raising=False)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    r = TestClient(api.app).post("/ledger/post", json={"result": {}})
    assert r.status_code in (422, 503)
