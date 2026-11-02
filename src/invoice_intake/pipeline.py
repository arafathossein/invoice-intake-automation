"""The pipeline: text in, explained decision out."""

from __future__ import annotations

import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from .extract import Extractor, LLMExtractor, RuleExtractor
from .models import Decision, Result
from .rules import Policy, decide, run_checks
from .store import AuditLog, Register, load_vendors

log = logging.getLogger("invoice_intake")


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    """Configuration, read from environment variables when the object is created."""

    vendors_path: Path = field(default_factory=lambda: Path(_env("VENDORS_CSV", "config/vendors.csv")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    confidence_threshold: float = field(default_factory=lambda: float(_env("CONFIDENCE_THRESHOLD", "0.90")))
    auto_post_limit: Decimal = field(default_factory=lambda: Decimal(_env("AUTO_POST_LIMIT", "2000")))
    llm_base_url: str | None = field(default_factory=lambda: _env("LLM_BASE_URL") or None)
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o-mini"))


class Pipeline:
    def __init__(self, settings: Settings | None = None, extractor: Extractor | None = None):
        self.settings = s = settings or Settings()
        self.policy = Policy(confidence_threshold=s.confidence_threshold, auto_post_limit=s.auto_post_limit)
        self.vendors = load_vendors(s.vendors_path)
        self.register = Register(s.data_dir / "register.sqlite3")
        self.audit = AuditLog(s.data_dir / "audit.jsonl")
        self.fallback = RuleExtractor()
        if extractor is not None:
            self.extractor: Extractor = extractor
        elif s.llm_base_url:
            self.extractor = LLMExtractor(s.llm_base_url, s.llm_api_key, s.llm_model)
        else:
            self.extractor = self.fallback

    def process_pdf(self, content: bytes, source: str, *, commit: bool = True) -> Result:
        return self.process_text(pdf_to_text(content), source, commit=commit)

    def process_text(self, text: str, source: str, *, commit: bool = True) -> Result:
        extractor_name = self.extractor.name
        try:
            data = self.extractor.extract(text)
        except Exception as exc:  # network, quota, malformed JSON …
            if self.extractor is self.fallback:
                raise
            log.warning("LLM extraction failed (%s); falling back to rules", exc)
            data = self.fallback.extract(text)
            extractor_name = "rules (llm failed)"

        duplicate = self.register.seen(data.supplier, data.invoice_number)
        checks = run_checks(data, known_vendors=self.vendors, is_duplicate=duplicate, policy=self.policy)
        decision, reasons = decide(data, checks, self.policy)

        result = Result(
            id=uuid.uuid4().hex[:12],
            source=source,
            received_at=datetime.now(timezone.utc),
            extractor=extractor_name,
            data=data,
            checks=checks,
            decision=decision,
            reasons=reasons,
        )
        if commit:
            # Anything accepted or awaiting review counts as "received" for duplicate detection.
            if decision in (Decision.AUTO_POST, Decision.REVIEW):
                self.register.add(data.supplier, data.invoice_number, result.id)
            self.audit.write(result)
        return result
