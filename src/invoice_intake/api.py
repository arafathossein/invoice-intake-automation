"""HTTP service used by n8n (or any workflow tool).

POST /invoices        multipart PDF upload  → Result
POST /invoices/text   {"text", "source"}     → Result
POST /ledger/post     {"result", "approved_by"} → post the bill to Xero, safe to retry
GET  /health
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .ledger import Bill, Poster, PostingError, PostingLedger, XeroClient, token_from_env_or_file
from .models import Decision, Result
from .pipeline import Pipeline

MAX_BYTES = 10 * 1024 * 1024

app = FastAPI(title="Invoice intake", version="0.1.0")
_pipeline: Pipeline | None = None
_poster: Poster | None = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def get_poster() -> Poster:
    global _poster
    if _poster is None:
        tenant = os.getenv("XERO_TENANT_ID")
        if not tenant:
            raise HTTPException(status_code=503, detail="Ledger not configured: set XERO_TENANT_ID and a token")
        client = XeroClient(
            token_from_env_or_file(os.getenv("XERO_ACCESS_TOKEN"), os.getenv("XERO_TOKEN_FILE")),
            tenant,
            status=os.getenv("XERO_BILL_STATUS", "DRAFT"),
            account_code=os.getenv("XERO_ACCOUNT_CODE") or None,
        )
        _poster = Poster(client, PostingLedger(get_pipeline().settings.data_dir / "postings.sqlite3"))
    return _poster


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("SERVICE_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


class TextIn(BaseModel):
    text: str
    source: str = "text"
    dry_run: bool = False


@app.get("/health")
def health() -> dict:
    p = get_pipeline()
    return {"status": "ok", "extractor": p.extractor.name, "vendors": len(p.vendors)}


@app.post("/invoices", response_model=Result, dependencies=[Depends(require_key)])
async def process_invoice(file: UploadFile = File(...), dry_run: bool = False) -> Result:
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 10 MB")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="Only PDF files are accepted")
    return get_pipeline().process_pdf(content, file.filename or "upload.pdf", commit=not dry_run)


@app.post("/invoices/text", response_model=Result, dependencies=[Depends(require_key)])
def process_text(body: TextIn) -> Result:
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Text is empty")
    return get_pipeline().process_text(body.text, body.source, commit=not body.dry_run)


class PostIn(BaseModel):
    result: Result
    approved_by: str | None = None  # required for invoices that went to review


@app.post("/ledger/post", dependencies=[Depends(require_key)])
def post_to_ledger(body: PostIn, poster: Poster = Depends(get_poster)) -> dict:
    r = body.result
    if r.decision == Decision.REJECT:
        raise HTTPException(status_code=409, detail="Rejected invoices are never posted")
    if r.decision == Decision.REVIEW and not (body.approved_by or "").strip():
        raise HTTPException(status_code=409, detail="This invoice needs review; send approved_by once a person has approved it")
    try:
        bill = Bill.from_result(r)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        out = poster.post(bill)
    except PostingError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": out.status, "bill_id": out.bill_id, "attempts": out.attempts,
            "idempotency_key": out.idempotency_key, "approved_by": body.approved_by}
