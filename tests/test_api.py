import pytest
from fastapi.testclient import TestClient

from invoice_intake import api
from invoice_intake.pipeline import Pipeline

from .conftest import sample


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setattr(api, "_pipeline", Pipeline(settings))
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    return TestClient(api.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["vendors"] == 3


def test_upload_pdf(client):
    r = client.post("/invoices", files={"file": ("inv.pdf", sample("01_northwind_inv-2048.pdf"), "application/pdf")})
    assert r.status_code == 200
    assert r.json()["decision"] == "auto_post"


def test_rejects_non_pdf(client):
    r = client.post("/invoices", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_text_endpoint(client):
    text = "Supplier: Northwind Supplies Ltd\nInvoice number: T-1\nInvoice date: 02 Oct 2026\nDue date: 01 Nov 2026\nSubtotal: €10.00\nVAT 20%: €2.00\nTotal: €12.00"
    r = client.post("/invoices/text", json={"text": text, "dry_run": True})
    assert r.json()["decision"] == "auto_post"


def test_api_key_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "s3cret")
    files = {"file": ("inv.pdf", sample("01_northwind_inv-2048.pdf"), "application/pdf")}
    assert client.post("/invoices", files=files).status_code == 401
    ok = client.post("/invoices", files=files, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
