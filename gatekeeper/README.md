# Gatekeeper — Verification Layer

**Zone 2 — verifies.** Owner: Alena (primary), Rhemmy (alternate).

The API that every Edge device talks to. It is the **only** thing allowed to call the Vault.

## Responsibilities
- **`api/`** — HTTPS (TLS 1.3) endpoints for the Edge apps.
- **`auth/`** — role checks (grader, compiler station, instructor, NCO, admin);
  CAC barcode parsing (`jkusner/CACBarcode`) and EDIPI hashing; CAC signature
  verification against the cadet's enrolled key.
- **`offline-sync/`** — buffer locally during an outage, sync within 24 h,
  no duplicates or missing records (SR-5).

## How to use the Vault
Import `muster_vault` (see `vault/README.md`). Every Vault function writes its record
**and** its audit-log entry in one transaction, so the Gatekeeper never touches SQL.

`muster_vault.signatures.verify_signature()` is available so the Gatekeeper and the
Vault check signatures the same way.
