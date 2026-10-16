# Vault — Zone 3 (Trusted Records)

**Owner:** Arjo (primary), Q (alternate)

The Vault is MUSTER's records office. It holds every score, attendance mark, and
weapon sign-out, plus an append-only, hash-chained audit log of every change.
The Gatekeeper is its only caller. **Nothing else writes SQL to the database.**

Python 3.10+ · SQLite · `cryptography` for signature checks.

---

## Quick start

```bash
cd vault
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                                 # 46 tests, synthetic data only
python ../scripts/vault_demo.py                        # end-to-end demo + tamper attacks
```

Command line:

```bash
python -m muster_vault init     --db muster.db
python -m muster_vault verify   --db muster.db        # PASS, or first broken seq
python -m muster_vault head     --db muster.db        # latest entry (outside anchor)
python -m muster_vault history  --db muster.db --entity fitness_event --id 3
python -m muster_vault check    --db muster.db --event 3   # score vs. what cadet signed
```

The Gatekeeper must set `MUSTER_EDIPI_KEY` (a secret of 32+ characters) to hash EDIPIs.
Keep it out of the repo (`.env` is already git-ignored).

---

## Modules

| Module | What it does |
|---|---|
| `schema.sql` | All tables. Working tables + `audit_log` (with append-only triggers). |
| `db.py` | Opens SQLite; `transaction()` uses `BEGIN IMMEDIATE` so the chain can't fork. |
| `audit.py` | `append` (inside a transaction) and `verify` (walks the chain). |
| `canonical.py` | One exact way to turn a record into bytes for hashing/signing. |
| `identity.py` | Cadets, staff, roles, EDIPI HMAC, CAC key enrollment/revocation. |
| `signatures.py` | Verify RSA (PKCS#1 v1.5/SHA-256) or ECDSA signatures vs. the enrolled key. |
| `attestation.py` | One-time challenges -> verified, stored signatures. |
| `fitness.py` | AFT/IOCT score lifecycle (primary stakeholder: DPE). |
| `ioct.py` | Wristband pairing, raw gate reads, Vault-computed run time. |
| `attendance.py` | Sections, enrollment, marking (secondary: Dean). |
| `arms.py` | Weapon sign-out / return with CAC signature (tertiary: arms room). |
| `receipts.py` | Cadet receipt with an audit anchor. |
| `export.py` | Adapter interface for DPE/AMS export (file stand-in until the RFI closes). |
| `cli.py` | The commands above. |

---

## The fitness flow, in Vault calls

**AFT**
```
fitness.open_event(grader, session, cadet_edipi_hash)       # grader scans CAC barcode
fitness.record_component(grader, event, "MDL", "210", 72)   # x5 AFT events
fitness.submit(grader, event)                               # -> score compiler
ch, payload = fitness.request_cadet_challenge(station, event)
#   station shows the scorecard; cadet inserts CAC, enters PIN; card signs `payload`
fitness.accept(station, event, ch, signature)               # verified vs enrolled key
receipts.issue(station, event)                              # cadet's copy
```

**IOCT**
```
ioct.pair(grader, session, cadet_edipi_hash, "TAG-017")     # grader watching
ioct.record_gate_read(gate, session, "TAG-017", "START", t, "G1")   # raw reads only
ioct.record_gate_read(gate, session, "TAG-017", "END",   t, "G2")
ioct.compute_run(grader, event)                             # Vault computes time
fitness.submit(grader, event)
event = ioct.active_event_for_tag(session, "TAG-017")       # cadet taps band at compiler
...same request_cadet_challenge / accept / receipt as AFT
```

Cadet disagrees → `fitness.dispute` → OIC `fitness.reopen` → fix → resubmit.
Already signed and wrong → OIC `fitness.supersede` (old score kept, new one linked).

---

## What's done vs. not (honest status)

| Item | Status |
|---|---|
| Hash-chained audit log + verifier + append-only triggers | Built, tested |
| Score lifecycle, disputes, corrections | Built, tested |
| Signature checks vs. enrolled key, one-time nonces, replay/swap protection | Built, tested **with software keys** |
| IOCT timing from raw gate reads | Built, tested with simulated reads |
| Detect edits to a signed score made directly in SQL | Built, tested |
| Idempotent writes (`client_ref`) for offline resync | Built (Vault side). Buffering is Gatekeeper's job. |
| **Real CAC signing** | **Not yet tested.** Needs non-GFE laptop + OpenSC. |
| **Encryption at rest (AES-256)** | **Not built.** Options: SQLCipher, or full-disk encryption on the server. Team decision. |
| AFT scoring tables (raw -> points) | Not built. Points entered by grader; confirm source with DPE. |
| AMS / Azimuth export | Stub (`JsonFileExporter`). Waiting on G6 RFI. |
| Signed checkpoints (stop a full chain rewrite) | Out of MVP scope. Receipts give a partial outside anchor. |
