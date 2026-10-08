# Requirements Traceability Matrix

Customer need → requirement → allocated zone → measurable spec → verification → status.

Stakeholder priority: **DPE (AFT + IOCT)** > **Dean (attendance)** > **arms room**.

> **SR-6 is new and proposed** (IOCT timing, added with the RFID gates). The team
> must agree to it before CDR. SR-1 and SR-3 were widened from arms-room-only to
> cover fitness scores.

| ID | Requirement | Allocated to | Specification (measurable) | Verify | Status |
|----|-------------|--------------|----------------------------|--------|--------|
| **SR-1** | Every record is tied to the right cadet. Accepting a fitness score and signing out a weapon require the cadet's CAC + PIN signature; an identifier alone (barcode, wristband) is never enough. | Score compiler / arms station (contact reader) → Gatekeeper verifies → Vault re-verifies and stores | Signature (RSA PKCS#1 v1.5 SHA-256 or ECDSA) verified against the key recorded at enrollment; one-time nonce, 5-minute expiry; signed bytes bind purpose + record. 0 fake/replayed/swapped signatures accepted. | Test 1 | Vault built + tested with software keys. **Real CAC not yet tested.** |
| **SR-2** | Attendance is captured by CAC barcode and confirmed by the instructor present. No remote or self-reported presence. | Edge attendance + instructor process; Vault enforces instructor-of-section + enrollment | Back (Code39) barcode parsed with `jkusner/CACBarcode`; only the section's instructor can mark; only enrolled cadets. | Test 2 | Vault side built + tested. |
| **SR-3** | Every fitness-score change and every SI event is written to an append-only, hash-chained log; any edit or deletion is detectable. | Vault `audit_log` | SHA-256 chain; verifier returns PASS or first broken `seq`; DB triggers block UPDATE/DELETE; corrections are appended. Signed scores are also re-checked against the signed bytes. 100% of edits/deletions in test detected. | Test 3 | Built + tested. Full chain rewrite by a privileged admin is out of MVP scope (receipt anchors give partial coverage). |
| **SR-4** | Encrypt in transit and at rest; retain no full DoD ID or raw barcode. | All zones; EDIPI hashed at Gatekeeper | TLS 1.3 (RFC 8446); AES-256 at rest (FIPS 197); EDIPI stored only as HMAC-SHA256 with a secret key kept outside the DB. | Test 4 | HMAC built + tested (no raw EDIPI in DB dump). **TLS and at-rest encryption not built yet.** |
| **SR-5** | On network outage, buffer locally and sync within 24 h with no duplicates or missing records. | Gatekeeper offline-sync; Vault idempotency | Every Edge write carries a unique `client_ref`; Vault ignores repeats. | Test 5 | Vault idempotency built + tested. Gatekeeper buffer not built. |
| **SR-6** *(proposed)* | IOCT times come from raw gate reads computed by the Vault, never from a time the gate or app reports. | IOCT gates (raw reads) → Vault computes | Start = last START read, end = first END read after pairing; sanity bounds 1:00–15:00 (confirm with DPE); gate clock sync tolerance TBD with DPE (target ≤ 0.1 s). | Test 6 | Logic built + tested with simulated reads. Gate hardware not chosen. |

---

## Verification tests

- **Test 1 (SR-1):** Sign with the wrong cadet's card, an unenrolled card, a revoked card;
  replay a used signature; move a signature to another score; use a fitness signature to
  sign out a weapon. **Pass:** all refused and logged.
- **Test 2 (SR-2):** Mark a cadet not enrolled; mark from another instructor's account.
  **Pass:** refused.
- **Test 3 (SR-3):** Edit an audit entry, delete one, edit a signed score directly in SQL.
  **Pass:** verifier / score check flags each one.
- **Test 4 (SR-4):** Dump the database and search for the EDIPIs used. **Pass:** none found.
  (Later: packet capture shows TLS 1.3; DB file unreadable without key.)
- **Test 5 (SR-5):** Send the same write twice (simulated resync). **Pass:** one record.
  (Later: cut network mid-session, restore, compare counts.)
- **Test 6 (SR-6):** Simulated gate reads with lingering reads and a reused wristband.
  **Pass:** correct time; impossible times refused for OIC review.

Automated versions: `vault/tests/` (run `pytest`).

---

## Standards

- **FIPS 201-3** — the CAC/PIV credential → SR-1
- **NIST SP 800-73-4** — PIV card interface (contact chip, PIN, keys) → SR-1
- **NIST SP 800-116 Rev 1** — PIV in access control; a static identifier is not
  authentication → justifies barcode/wristband = identify only, signature = prove
- **NIST SP 800-63** — assurance-level vocabulary
- **RFC 8446 (TLS 1.3)** → SR-4
- **FIPS 197 (AES)** → SR-4 · **FIPS 180-4 (SHA-256)** and **FIPS 198-1 (HMAC)** → SR-3, SR-4
- **Privacy Act of 1974** — PII minimization, role-based access, retention → SR-4
- **FAR 52.204-25 / GSA 889** — hardware procurement
