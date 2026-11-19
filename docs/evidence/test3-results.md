# Security Test 3: data in transit and at rest (SR-4)

Run: 2026-11-19 15:04 UTC, by `python scripts/test3_data_protection.py`. Synthetic data only (fake cards 9000000001 to 13). Throwaway keys, made for this run and thrown away.

**Result: PASS** (18 of 18 checks passed)

Pass rule (backlog card Test 3): encrypted, no full DoD ID stored, only needed info kept.

How: the script starts the real Gatekeeper app over HTTPS with the same TLS setup as `start_app.ps1 -Https`, puts a relay in front of it that records every byte on the wire, and uses the app the way the phone does (grader and OIC sign in by card, three AFT scores, one OIC correction, one IOCT run). Then it reads the database file as raw bytes, opens it with and without the key, and runs the AMS export.

## In transit

| Check | Result | Detail |
|---|---|---|
| HTTPS uses TLS 1.3 | PASS | TLSv1.3, TLS_AES_256_GCM_SHA384 |
| A client limited to TLS 1.2 is refused | PASS | SSLError |
| Plain HTTP (no encryption) gets no answer | PASS | ConnectionResetError |
| Recorded traffic (171,199 bytes) has no readable DoD ID | PASS | 0 of 13 IDs found |
| Recorded traffic has no readable cadet or staff name | PASS | 0 of 14 names found |
| Recorded traffic has no readable sign-in token | PASS | 0 of 2 found |
| Recorded traffic has no readable HTTP (headers or JSON) | PASS |  |
| Server log has no DoD ID or token | PASS | 31 log lines checked |

## At rest

| Check | Result | Detail |
|---|---|---|
| Database file has no readable 'SQLite format 3' header | PASS | first bytes b8d7f0429da76201 |
| Raw file bytes (229,376) have no DoD ID | PASS | 0 of 13 IDs found |
| Raw file bytes have no name | PASS | 0 of 14 names found |
| Raw file bytes have no ID hash and no score | PASS | 12 hashes and the 2-mile time checked |
| Opening without the key fails | PASS | file is not a database |
| With the key: no DoD ID in any table, including the audit log | PASS | 1,014 values checked |
| IDs are kept only as keyed hashes (HMAC-SHA256) | PASS | 12 hashes, all 64 hex characters |
| Only needed details kept about a cadet (no date of birth or card dates) | PASS | id, edipi_hash, last_name, first_name, company_id, class_year, active, created_at |
| Audit chain still verifies | PASS | PASS — 63 entries, head faced1e5fd106d9b8d4f2d7fad1c1c2220b1a13f51a53654b36c2ed2cd3bab7c |

## Export file

| Check | Result | Detail |
|---|---|---|
| AMS export has no DoD ID and no ID hash | PASS | aft.csv 3 rows, ioct.csv 1 rows, attendance.csv 3 rows |

## Found and fixed while writing this test

The app's HTTPS setting (`ssl_context="adhoc"`) also accepted TLS 1.2, and SR-4 says TLS 1.3. `live_app.tls_context()` now sets TLS 1.3 as the minimum, and the app uses it. Every phone that can run the scanner supports TLS 1.3.

## Limits, stated plainly

- The certificate is self-signed and made fresh at each start, so phones warn once per start, and the phone cannot tell the real laptop from an impostor on the same network. A real deployment needs a certificate the phones trust (for example a DoD-issued one).
- The relay records traffic on the laptop itself (127.0.0.1). It shows what anyone on the network would see: TLS records only.
- At rest covers the database file. The key file `.app_keys` sits next to it on the same laptop; anyone with both can read everything (see `scripts/admin_tamper.py`, and the audit check catches what they change).

Sample export from this run: `docs/evidence/sample-export/` (batch 1795100641213).
