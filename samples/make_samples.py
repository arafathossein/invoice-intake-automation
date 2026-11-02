"""Generate the fictional sample invoices in this folder.

    python samples/make_samples.py

Every company, address and number here is invented. Each file exercises
one routing path of the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).parent

SAMPLES = [
    {
        "file": "01_northwind_inv-2048.pdf",
        "expect": "auto_post — routine invoice, all checks pass",
        "supplier": "Northwind Supplies Ltd",
        "address": ["14 Harbour Lane", "Bristol BS1 4XX", "United Kingdom"],
        "fields": [
            ("Invoice number", "INV-2048"),
            ("Invoice date", "02 Oct 2026"),
            ("Due date", "01 Nov 2026"),
        ],
        "lines": [("Corrugated boxes, 400 x 300 x 200 (x500)", "€840.00"), ("Packing tape, 48 mm (x80)", "€400.00")],
        "totals": [("Subtotal", "€1,240.00"), ("VAT 20%", "€248.00"), ("Total due", "€1,488.00")],
    },
    {
        "file": "02_brightline_7731.pdf",
        "expect": "review — totals don't add up and the due date is unreadable",
        "supplier": "Brightline Logistics GmbH",
        "address": ["Hafenstraße 9", "20457 Hamburg", "Germany"],
        "fields": [
            ("Invoice number", "7731"),
            ("Invoice date", "28.09.2026"),
            ("Due date", "end of month (see note)"),
        ],
        "lines": [("Freight Hamburg - Rotterdam, 2 pallets", "3.000,00 EUR")],
        "totals": [("Net", "3.000,00 EUR"), ("VAT 19%", "570,00 EUR"), ("Total", "3.610,00 EUR")],
    },
    {
        "file": "03_northwind_inv-2048_resent.pdf",
        "expect": "reject — same invoice sent a second time",
        "supplier": "Northwind Supplies Ltd",
        "address": ["14 Harbour Lane", "Bristol BS1 4XX", "United Kingdom"],
        "fields": [
            ("Invoice number", "INV-2048"),
            ("Invoice date", "02 Oct 2026"),
            ("Due date", "01 Nov 2026"),
        ],
        "lines": [("Corrugated boxes, 400 x 300 x 200 (x500)", "€840.00"), ("Packing tape, 48 mm (x80)", "€400.00")],
        "totals": [("Subtotal", "€1,240.00"), ("VAT 20%", "€248.00"), ("Total due", "€1,488.00")],
        "stamp": "REMINDER - SECOND COPY",
    },
    {
        "file": "04_orbital_inv-559.pdf",
        "expect": "review — supplier is not in the vendor list",
        "supplier": "Orbital Office Furniture SARL",
        "address": ["22 Rue des Lilas", "69003 Lyon", "France"],
        "fields": [
            ("Invoice number", "OOF-559"),
            ("Invoice date", "05 Oct 2026"),
            ("Due date", "04 Nov 2026"),
        ],
        "lines": [("Ergonomic chair, model K2 (x2)", "€640.00")],
        "totals": [("Subtotal", "€640.00"), ("VAT 20%", "€128.00"), ("Total due", "€768.00")],
    },
    {
        "file": "05_coastline_inv-9001.pdf",
        "expect": "review — valid, but above the auto-post limit",
        "supplier": "Coastline Packaging BV",
        "address": ["Kade 31", "3011 Rotterdam", "Netherlands"],
        "fields": [
            ("Invoice number", "CP-9001"),
            ("Invoice date", "07 Oct 2026"),
            ("Due date", "06 Nov 2026"),
        ],
        "lines": [("Pallet wrap film, 23 micron (x400 rolls)", "€10,250.00")],
        "totals": [("Subtotal", "€10,250.00"), ("VAT 21%", "€2,152.50"), ("Total due", "€12,402.50")],
    },
]


def draw(sample: dict) -> None:
    path = OUT / sample["file"]
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setTitle(f"Invoice {sample['fields'][0][1]}")
    c.setAuthor("Sample data - fictional")

    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, sample["supplier"])
    c.setFont("Helvetica", 9)
    for i, line in enumerate(sample["address"]):
        c.drawString(20 * mm, y - (6 + i * 4.5) * mm, line)

    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(w - 20 * mm, y, "INVOICE")
    if sample.get("stamp"):
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.75, 0.1, 0.1)
        c.drawRightString(w - 20 * mm, y - 7 * mm, sample["stamp"])
        c.setFillColorRGB(0, 0, 0)

    y -= 32 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, "Bill to: Example Trading Co., 1 Sample Street, Springfield")
    y -= 10 * mm
    c.drawString(20 * mm, y, f"Supplier: {sample['supplier']}")
    for label, value in sample["fields"]:
        y -= 6 * mm
        c.drawString(20 * mm, y, f"{label}: {value}")

    y -= 14 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Description")
    c.drawRightString(w - 20 * mm, y, "Amount")
    c.line(20 * mm, y - 2 * mm, w - 20 * mm, y - 2 * mm)
    c.setFont("Helvetica", 10)
    for desc, amount in sample["lines"]:
        y -= 8 * mm
        c.drawString(20 * mm, y, desc)
        c.drawRightString(w - 20 * mm, y, amount)

    y -= 14 * mm
    for label, value in sample["totals"]:
        is_total = label.lower().startswith("total")
        c.setFont("Helvetica-Bold" if is_total else "Helvetica", 11 if is_total else 10)
        # one "Label: value" text run so text extraction keeps them on one line
        c.drawRightString(w - 20 * mm, y, f"{label}: {value}")
        y -= 7 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, 15 * mm, "Sample document for software testing. All names and figures are fictional.")
    c.showPage()
    c.save()


if __name__ == "__main__":
    for s in SAMPLES:
        draw(s)
        print(f"{s['file']:<34} expect: {s['expect']}")
