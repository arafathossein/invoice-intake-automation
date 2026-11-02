import json

import httpx

from invoice_intake.extract import LLMExtractor
from invoice_intake.models import Decision
from invoice_intake.pipeline import Pipeline

from .conftest import sample

EXPECTED = [
    ("01_northwind_inv-2048.pdf", Decision.AUTO_POST),
    ("02_brightline_7731.pdf", Decision.REVIEW),
    ("03_northwind_inv-2048_resent.pdf", Decision.REJECT),
    ("04_orbital_inv-559.pdf", Decision.REVIEW),
    ("05_coastline_inv-9001.pdf", Decision.REVIEW),
]


def test_samples_route_as_expected(pipeline):
    got = [(name, pipeline.process_pdf(sample(name), name).decision) for name, _ in EXPECTED]
    assert got == EXPECTED


def test_rule_extractor_reads_european_numbers(pipeline):
    r = pipeline.process_pdf(sample("02_brightline_7731.pdf"), "x", commit=False)
    assert str(r.data.net) == "3000.00" and str(r.data.total) == "3610.00"
    assert r.data.due_date is None


def test_dry_run_does_not_register(pipeline):
    pipeline.process_pdf(sample("01_northwind_inv-2048.pdf"), "a", commit=False)
    again = pipeline.process_pdf(sample("01_northwind_inv-2048.pdf"), "b", commit=False)
    assert again.decision is Decision.AUTO_POST


def test_every_committed_result_is_audited(pipeline, settings):
    for name, _ in EXPECTED:
        pipeline.process_pdf(sample(name), name)
    lines = (settings.data_dir / "audit.jsonl").read_text().splitlines()
    assert len(lines) == len(EXPECTED)
    assert json.loads(lines[2])["decision"] == "reject"


def _llm_client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_llm_extractor_output_is_validated(settings):
    payload = {
        "supplier": "Northwind Supplies Ltd",
        "invoice_number": "INV-77",
        "issue_date": "2026-10-02",
        "due_date": "2026-11-01",
        "currency": "EUR",
        "net": 100,
        "tax": 20,
        "total": 125,  # model copied a wrong total — validation must catch it
        "confidence": {k: 0.98 for k in ("supplier", "invoice_number", "issue_date", "due_date", "net", "tax", "total")},
    }
    llm = LLMExtractor("http://llm.test/v1", "key", "m", client=_llm_client(payload))
    r = Pipeline(settings, extractor=llm).process_text("invoice text", "t")
    assert r.extractor == "llm"
    assert r.decision is Decision.REVIEW


def test_llm_failure_falls_back_to_rules(settings):
    def broken(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    llm = LLMExtractor("http://llm.test/v1", "key", "m", client=httpx.Client(transport=httpx.MockTransport(broken)))
    r = Pipeline(settings, extractor=llm).process_pdf(sample("01_northwind_inv-2048.pdf"), "a")
    assert r.extractor == "rules (llm failed)"
    assert r.decision is Decision.AUTO_POST
