# Vault evidence for the Sprint 2 review (L33, 19-Nov-2026)

Owner: Arjo (Zone 3, Vault). Card: PBI-11 L33, Vault evidence, Test 3, the tamper demo, and the export file.
Synthetic data only. Every command below runs from the repo root.

## Run it yourself (about 2 minutes)

```powershell
cd vault; python -m pytest -q; cd ..                       # Vault tests: 95 passed
cd gatekeeper\api; python -m pytest -q; cd ..\..           # Gatekeeper tests (include the Vault through the API)
python scripts\test3_data_protection.py                    # Test 3, writes test3-results.md and sample-export\
$env:PYTHONPATH = "vault"; python scripts\vault_demo.py    # tamper demo, output saved in vault-tamper-demo-output.txt
```

## The three things the advisor asked the Vault to prove

| Claim | Evidence |
|---|---|
| **No raw ID is stored.** The scanner's ID is hashed and never kept raw. | Test 3, at rest: 0 of 13 fake IDs in the raw database file, 0 in any table with the key (1,014 values checked, audit log included). IDs exist only as 64-character keyed hashes (HMAC-SHA256, key held by the Gatekeeper). `test3-results.md`. Also `gatekeeper/api/tests/test_cac_identity.py::test_the_database_holds_no_raw_dod_id`. |
| **A score that is entered is in the database with the correct score.** | `gatekeeper/api/tests/test_scoring.py` (25 cases checked by hand against the printed tables) and `test_live_scoring.py` (10 graded records, then the audit check passes and no raw ID is stored). In `sample-export/aft.csv`, each row shows the raw result, the points from the official table, the total, PASS or FAIL, and the audit entry it matches. |
| **It cannot be altered.** A change after submit is refused, and an edit made around MUSTER is caught, with the audit check naming the first changed entry. | Through the app: a change after submit returns 409, and PUT, PATCH and DELETE return 405 (`docs/evidence/gatekeeper-tests-1-2-results.md`, rows 13 to 16). An OIC correction is a new score that points to the original, and the original row does not change (`test_role_permissions.py::test_oic_corrects_a_submitted_score_by_superseding_it_and_the_original_never_moves`). Around the app: `vault-tamper-demo-output.txt`. Attack 1 edits points in SQL: "2MR: database says 15:10 / 100 pts, audit log says 15:10 / 88 pts". Attack 2 rewrites the log: "audit chain: FAIL at seq 20". |

## Test 3: data in transit and at rest (SR-4): PASS, 18 of 18

Full table in `test3-results.md`. In short:

- **In transit:** HTTPS is TLS 1.3 (`TLS_AES_256_GCM_SHA384`). A TLS 1.2 client and plain HTTP are refused. A relay recorded every byte of a full app session (171 KB) and found no readable ID, name, sign-in token or HTTP text. The server log holds no ID or token.
- **At rest:** the database file has no readable header, ID, name, ID hash or score. Plain SQLite cannot open it. Only needed details are kept about a cadet (name, company, class year, hashed ID). There is no date of birth and there are no card dates; the scanner drops them on the phone.
- **Found and fixed while writing the test:** the app's HTTPS also accepted TLS 1.2. `live_app.tls_context()` now sets TLS 1.3 as the minimum.

## PBI-09 L31: attendance tables and write path in the Vault

| Card asks | Evidence |
|---|---|
| Storage: section, cadet, status, instructor | `vault/muster_vault/schema.sql`: `class_section` (course, section, period, term, instructor), `enrollment`, `attendance` (section, cadet number, date, PRESENT, LATE, ABSENT or EXCUSED, recorded by, time). No ID or hash in the attendance table (`test_export_csv.py::test_attendance_stores_no_id_only_the_cadet_number`). |
| Only the section's own instructor, only enrolled cadets | `test_other_functions.py::test_attendance_rejects_unenrolled_and_other_instructor`, and through the API Test 2 (`gatekeeper-tests-1-2-results.md`). |
| Same audit rules as fitness | Every mark and correction writes an audit entry in the same transaction (`test_attendance_mark_correct_and_roster`). **New:** `attendance.check_against_audit` replays the log and catches a row changed, added or deleted with SQL, the same way scores are checked. It is part of `python -m muster_vault integrity` (`test_export_csv.py::test_attendance_edited_added_or_deleted_with_sql_is_caught`). |

## PBI-10 L32: AMS export v0

`python -m muster_vault export-csv --db <db> --actor <OIC user id> --out <folder>` writes `aft.csv`, `ioct.csv`, `attendance.csv` and `manifest.json`. It is OIC only. A sample from the Test 3 run is in `sample-export/`.

| PBI-10 criterion | Evidence |
|---|---|
| Export row count equals the saved record count | It is checked inside the export after the files are written, and the counts are in `manifest.json` (`test_export_writes_three_csvs_and_a_manifest_with_matching_row_counts`). |
| A failed audit check stops the export with a message | A broken chain gives "Export stopped: … Audit chain broken at entry 5". An edited score gives "Score 2: 2MR: database says …". No file is written, and the refusal is logged (`test_a_broken_audit_chain_stops_the_export_with_a_message`, `test_an_edited_score_stops_the_export`). |
| No raw ID on the way out | No ID and no ID hash in any file (`test_no_raw_id_and_no_id_hash_in_any_file`, Test 3 export check). |

Also covered:
- Each row carries its audit entry number and hash, and the export itself is logged with a SHA-256 of every file, so a file changed after it leaves can be caught.
- A correction shows `corrects_event_id`, and the score it replaced is left out.
- Names that start with `=`, `+`, `-` or `@` are neutralized so a spreadsheet will not run them.

Format: the AMS field list is still an open RFI, so per the card this is a CSV of the contract fields. When the RFI is answered, only the column lists in `export.py` change.

## Carry-over to FDR (Vault)

- IOCT typed-time field: a time typed by the grader is not yet labeled manual (PBI-08).
- Decide whether the Vault stores the AFT age group, scoring column, total and result (contract open item 1a). The export notes their absence.
- How AMS matches a record to a cadet without a DoD ID leaving MUSTER (AMS RFI).
- Audit report by cadet or date range (PBI-10 L30). Per-record history and the full integrity report exist; the date-range report does not.
- Staff sign-in takes only a typed ID (see the 17-Nov review): a PIN, or the CAC chip, plus a limit on guesses.
- Real-CAC signing at the score compiler (needs a non-GFE laptop for OpenSC).
