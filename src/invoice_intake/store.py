"""Persistence: vendor list, duplicate register (SQLite) and the audit log (JSONL)."""

from __future__ import annotations

import csv
import sqlite3
import threading
from pathlib import Path

from .models import Result
from .rules import normalise_name


def load_vendors(path: Path) -> dict[str, str]:
    """Return {normalised name: vendor_id} from a CSV with columns vendor_id,name."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {normalise_name(row["name"]): row["vendor_id"] for row in csv.DictReader(f)}


class Register:
    """Remembers which (supplier, invoice number) pairs have been received."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS seen (supplier TEXT, number TEXT, result_id TEXT, PRIMARY KEY (supplier, number))"
        )
        self._db.commit()

    def seen(self, supplier: str | None, number: str | None) -> bool:
        if not supplier or not number:
            return False
        with self._lock:
            cur = self._db.execute(
                "SELECT 1 FROM seen WHERE supplier = ? AND number = ?", (normalise_name(supplier), number.strip())
            )
            return cur.fetchone() is not None

    def add(self, supplier: str | None, number: str | None, result_id: str) -> None:
        if not supplier or not number:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO seen VALUES (?, ?, ?)", (normalise_name(supplier), number.strip(), result_id)
            )
            self._db.commit()


class AuditLog:
    """Append-only JSON Lines log: one line per processed invoice."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()

    def write(self, result: Result) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(result.model_dump_json() + "\n")
