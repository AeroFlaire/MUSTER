"""Print the synthetic test cadets as scannable "fake CAC" barcodes (PDF).

The USB scanner can't read real CAC barcodes yet (Sprint 2 fix), but it reads
ordinary Code 128 barcodes. Each card here encodes the test pack's
barcode_string (PDF417_TEST_DATA_<fake EDIPI>_END), which the Gatekeeper accepts.
SYNTHETIC DATA ONLY: the EDIPIs are 9000000001..9000000010, not real people.

    pip install reportlab
    python scripts/make_test_barcodes.py            # -> test_barcodes.pdf
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "tests" / "synthetic_test_pack.json"


def main(out: str = "test_barcodes.pdf") -> int:
    cadets = json.loads(PACK.read_text(encoding="utf-8"))["cadets"]
    c = canvas.Canvas(out, pagesize=letter)
    c.setTitle("MUSTER synthetic test barcodes")
    width, height = letter
    card_w, card_h = 3.6 * inch, 1.75 * inch
    cols, x0, y0 = 2, 0.55 * inch, height - 0.9 * inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, height - 0.55 * inch,
                 "MUSTER test barcodes - SYNTHETIC, NOT REAL CACs (Code 128)")
    for i, cadet in enumerate(cadets):
        col, row = i % cols, i // cols
        x = x0 + col * (card_w + 0.2 * inch)
        y = y0 - (row + 1) * (card_h + 0.12 * inch)
        c.setDash(3, 3)
        c.rect(x, y, card_w, card_h)
        c.setDash()
        c.setFont("Helvetica-Bold", 11)
        c.drawString(x + 10, y + card_h - 18, cadet["fake_name"])
        c.setFont("Helvetica", 8)
        c.drawRightString(x + card_w - 10, y + card_h - 18, "TEST CARD - not a CAC")
        bc = code128.Code128(cadet["barcode_string"], barHeight=0.75 * inch, barWidth=0.0095 * inch)
        bc.drawOn(c, x + (card_w - bc.width) / 2, y + 0.42 * inch)
        c.setFont("Courier", 7)
        c.drawCentredString(x + card_w / 2, y + 0.22 * inch, cadet["barcode_string"])
    c.showPage()
    c.save()
    print(f"wrote {out} ({len(cadets)} synthetic cadets)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
