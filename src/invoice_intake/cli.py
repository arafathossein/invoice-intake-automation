"""Command line: process PDFs and print the decision for each.

    invoice-intake process samples/*.pdf
    invoice-intake process invoice.pdf --json --dry-run
    invoice-intake serve --port 8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Decision
from .pipeline import Pipeline

_MARK = {Decision.AUTO_POST: "AUTO-POST", Decision.REVIEW: "REVIEW", Decision.REJECT: "REJECT"}


def _process(args: argparse.Namespace) -> int:
    pipeline = Pipeline()
    print(f"extractor: {pipeline.extractor.name} | vendors: {len(pipeline.vendors)}\n", file=sys.stderr)
    for path in args.files:
        result = pipeline.process_pdf(Path(path).read_bytes(), Path(path).name, commit=not args.dry_run)
        if args.json:
            print(result.model_dump_json(indent=2))
            continue
        d = result.data
        print(f"{_MARK[result.decision]:<10} {Path(path).name}")
        print(f"           {d.supplier} | {d.invoice_number} | total {d.currency or ''} {d.total}")
        for c in result.checks:
            print(f"           {'✓' if c.passed else '✗'} {c.name}: {c.detail}")
        print()
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("invoice_intake.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invoice-intake")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="Process one or more PDF invoices")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", action="store_true", help="Print full JSON results")
    p.add_argument("--dry-run", action="store_true", help="Don't record in the register or audit log")
    p.set_defaults(func=_process)

    s = sub.add_parser("serve", help="Run the HTTP API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
