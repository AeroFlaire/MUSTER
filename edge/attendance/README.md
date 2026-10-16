# Edge — Attendance (Classroom)

**Zone 1 — untrusted.** Owner: Edge team. Priority: secondary stakeholder (Dean).

## Flow
1. Cadet's CAC barcode is scanned with a 2-D USB scanner (acts as a keyboard).
   Prefer the **back (Code39)** barcode, which exposes less PII.
2. The raw string goes to the Gatekeeper, which parses it with `jkusner/CACBarcode`,
   hashes the EDIPI, and discards the raw string.
3. The instructor confirms the cadet is physically present.

## Assurance
Low by design. The barcode automates the roster; the instructor's eyes are the
presence check. No remote or self-reported check-in.
