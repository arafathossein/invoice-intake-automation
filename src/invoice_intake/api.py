"""HTTP service used by n8n (or any workflow tool).

POST /invoices        multipart PDF upload  → Result
POST /invoices/text   {"text", "source"}     → Result
GET  /health
"""

from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from .models import Result
from .pipeline import Pipeline

MAX_BYTES = 10 * 1024 * 1024

app = FastAPI(title="Invoice intake", version="0.1.0")
_pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


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
