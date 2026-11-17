"""Synthetic CAC test cards in the REAL barcode layouts (PBI-12). SYNTHETIC DATA ONLY.

Writes two things:
  edge/scanner/tests/synthetic_front.json   barcode strings + the fields the parser must
                                            return (used by the parser tests)
  test_front_cards.pdf                       printable cards: front (PDF417) and back
                                            (Code 39) for each synthetic cadet

The layouts copy what real CAC barcodes look like (see edge/scanner/cac_parse.js), but
every value is fake: DoD IDs 9000000001-9000000014, invented names, zeroed dates. Never
put a real card's barcode string in this repo.

    pip install zxing-cpp reportlab pillow
    python scripts/make_front_test_cards.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "edge" / "scanner" / "tests" / "synthetic_front.json"
B32 = "0123456789ABCDEFGHIJKLMNOPQRSTUV"

# (first, middle initial or "", last, note). Last names match the synthetic data pack.
PEOPLE = [
    ("Avery", "Q", "Alpha", ""), ("Blake", "R", "Bravo", ""), ("Casey", "S", "Charlie", ""),
    ("Devon", "T", "Delta", ""), ("Emerson", "U", "Echo", ""), ("Finley", "V", "Foxtrot", ""),
    ("Gray", "W", "Golf", ""), ("Harper", "X", "Hotel", ""), ("Indigo", "Y", "India", ""),
    ("Jordan", "Z", "Juliet", ""),
    # Edge cases the layout makes hard. The expected result is what the parser returns.
    ("Kai", "", "Kilo", "no middle initial"),
    ("Lane", "M", "De La Lima", "last name with spaces"),
    ("Morgan", "N", "Mike-Smith", "hyphenated last name"),
    ("Lee Ann", "O", "Oscar", "first name with a space"),
]


def b32(n: int, width: int) -> str:
    out = ""
    for _ in range(width):
        out = B32[n % 32] + out
        n //= 32
    assert n == 0
    return out


def front_string(edipi: str, first: str, mi: str, last: str, fixed: bool = True) -> str:
    """fixed=True: space-padded fields (first 20, initial 1, last 26), which real cards use.
    fixed=False: "First MLast" with single spaces, a second layout the parser also accepts."""
    header = "M" + b32(int(edipi), 7) + "00TEST00"            # 16 characters
    name = (first.ljust(20) + (mi or " ") + last.ljust(26)) if fixed else f"{first} {mi}{last}"
    tail = ("0000" "US" " " "J" "A" "00" "CADET " "MC" "01"   # DOB zeroed, Academy student, Army
            "0000" "0000" "X" "TEST01")                       # card dates zeroed, fake card numbers
    assert len(tail) == 36
    return f"{header}{name} {tail}"


def back_string(edipi: str) -> str:
    return "1" + "TEST01" + "X" + b32(int(edipi), 7) + "J" + "A" + "W"


def cards() -> list[dict]:
    out = []
    for i, (first, mi, last, note) in enumerate(PEOPLE, start=1):
        edipi = f"90000000{i:02d}"
        out.append({
            "edipi": edipi, "note": note,
            "front": front_string(edipi, first, mi, last),
            "front_variable": front_string(edipi, first, mi, last, fixed=False)
                              if mi and " " not in first else None,
            "back": back_string(edipi),
            "expect": {"firstName": first, "middleInitial": mi, "lastName": last,
                       "category": "J", "branch": "A"},
        })
    return out


def pdf417_image(text: str, scale: int = 3):
    """A PDF417 symbol as a PIL image (black on white, with quiet zone)."""
    import numpy as np
    import zxingcpp
    from PIL import Image

    bc = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.PDF417)
    img = zxingcpp.write_barcode_to_image(bc, scale=scale, add_quiet_zones=True)
    return Image.fromarray(np.array(img, copy=False))


def write_pdf(items: list[dict], out: Path) -> None:
    from reportlab.graphics.barcode import code39
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=letter)
    c.setTitle("MUSTER synthetic CAC test cards")
    w, h = letter
    per_page, y_top = 4, h - 0.9 * inch
    for i, it in enumerate(items):
        if i % per_page == 0:
            if i:
                c.showPage()
            c.setFont("Helvetica-Bold", 12)
            c.drawString(0.6 * inch, h - 0.55 * inch,
                         "MUSTER synthetic CAC test cards - FAKE DATA, NOT REAL CACs")
        y = y_top - (i % per_page) * 2.45 * inch - 2.2 * inch
        c.setDash(3, 3)
        c.rect(0.5 * inch, y, w - 1.0 * inch, 2.2 * inch)
        c.setDash()
        name = f"{it['expect']['lastName']}, {it['expect']['firstName']}"
        c.setFont("Helvetica-Bold", 11)
        c.drawString(0.65 * inch, y + 1.95 * inch, f"{name}   (DoD ID {it['edipi']}, synthetic)")
        if it["note"]:
            c.setFont("Helvetica-Oblique", 9)
            c.drawString(0.65 * inch, y + 1.78 * inch, f"edge case: {it['note']}")
        pil = pdf417_image(it["front"])
        bw, bh = 3.4 * inch, 3.4 * inch * pil.height / pil.width
        c.drawImage(ImageReader(pil), 0.65 * inch, y + 0.2 * inch, width=bw, height=min(bh, 1.5 * inch))
        c.setFont("Helvetica", 8)
        c.drawString(0.65 * inch, y + 0.08 * inch, "FRONT (PDF417)")
        bc = code39.Standard39(it["back"], barHeight=0.6 * inch, barWidth=0.0135 * inch,
                               checksum=False, stop=True)
        bc.drawOn(c, 4.4 * inch, y + 0.7 * inch)
        c.drawString(4.5 * inch, y + 0.45 * inch, "BACK (Code 39)")
    c.save()


def main(argv: list[str]) -> int:
    items = cards()
    FIXTURES.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURES.relative_to(REPO)} ({len(items)} synthetic cards)")
    if "--json-only" not in argv:
        out = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("--") else Path("test_front_cards.pdf")
        write_pdf(items, out)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
