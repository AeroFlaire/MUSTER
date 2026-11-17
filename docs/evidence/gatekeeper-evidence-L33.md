# Gatekeeper evidence for the Sprint 2 review (PBI-11, L33)

Owner: Alena. One piece of evidence per API acceptance criterion. Every command is run from `gatekeeper/api/` after `pip install -r requirements.txt`. Test total: 112 Gatekeeper tests (and the Vault's own 81) pass.

    python -m pytest -q tests

| PBI | Criterion (API side) | Evidence | Status |
|---|---|---|---|
| PBI-07 | Scoring standards for all five events load from a data file; 5 test cases per event grade correctly (25 of 25) | `tests/test_scoring.py` (11 tests, 25 hand-checked cases), data file `data/aft_scoring_2025.json`, labeled `pending_dpe` | Done, placeholders labeled |
| PBI-07 | One submit grades the record and saves exactly 1 record + 1 audit entry in one transaction; the screen shows total and pass or fail | `tests/test_live_scoring.py` (13 tests), `tests/test_live_vault.py` (16 tests, includes the forced audit failure that leaves 0 records), demo scene 1 and scene 4 | Done. Total is in the saved scorecard, PASS or FAIL is worked out from the saved points (see open item 1a in contract v1) |
| PBI-07 | Audit verifier PASS after 10 graded AFT records; raw barcode in 0 rows | `test_live_scoring.py` (whole synthetic pack), `test_vault_holds_no_raw_barcode_or_id` | Done |
| PBI-08 | API receives each run's tokens and timestamps in the contract JSON; each run = 1 IOCT record + 1 audit entry | `tests/test_live_ioct.py` (15 tests), demo scene 3 | Done on simulated reads |
| PBI-08 | Grader sees the time; a typed (manual) time is labeled manual | `grading` block on the IOCT view. The manual typed-time fallback needs a Vault field | **Carry-over** (Arjo schema, then Gatekeeper endpoint) |
| PBI-08 | A correction saves as a new audit entry pointing to the original; original row unchanged | `tests/test_live_ioct_grading.py::test_supersede_keeps_the_original_and_points_the_new_event_at_it`: new event's `supersedes_event_id` = original, original IOCT run unchanged, only the status label moves to SUPERSEDED, the audit chain still verifies | Done (note: the status column changes to SUPERSEDED, everything else is identical) |
| PBI-08 | IOCT scoring: points, grade, PASS (D or better) and TAB (A- or better) from the DPE grade scale | `tests/test_live_ioct_grading.py` (every band edge on the DPE slide, men and women), `data/ioct_standard.json` | Done. Whole-second rule to confirm with the DPE |
| PBI-09 | 10 synthetic cadets marked, 1 record + 1 audit entry each | `tests/test_live_attendance.py::test_instructor_marks_ten_cadets_one_record_and_one_audit_entry_each`, demo scene 5 | Done |
| PBI-09 | Another instructor's section: 403 and 1 rejected-attempt audit entry (3 of 3) | `test_another_instructors_section_is_blocked_and_logged_3_of_3`, Test 2 results rows 1 to 3 | Done. Screen message is Edge's part |
| PBI-09 | Cadet not on the roster rejected (3 of 3) | `test_cadet_not_on_the_roster_is_rejected_and_logged_3_of_3`, Test 2 rows 4 to 6 | Done |
| PBI-09 | 0 raw barcodes or DoD IDs in attendance tables and logs | `test_vault_holds_no_raw_barcode_or_id` | Done |
| PBI-10 | Role checks on every write | `tests/test_role_check.py` (15 tests), demo scene 2 (42 attempts, 0 records) | Done |
| Contract | Contract v1 matches the running API | `tests/test_stub_api.py` (21 tests; every example and every route is in `docs/interfaces/api-contract-v1.md`) | Done |

## Tests 1 and 2

    python run_security_tests.py

Results are written to `docs/evidence/gatekeeper-tests-1-2-results.md`: Test 1 (fake record attempts) 18 of 18 checks pass with 0 fake records, Test 2 (out-of-section attendance) 16 of 16 checks pass with 0 false records. Test 3 (data in transit and at rest) is Arjo's.

## Carry-over list for the Final Design Review (Gatekeeper)

1. DPE answers: which AFT standards and age group and column apply to a cadet, the pass rule, and the IOCT pass time.
2. Manual typed time for a failed gate, labeled manual (needs a Vault field, then an endpoint).
3. The canned endpoints still on `X-Role`: signing at the compiler station (cadet-challenge, accept, dispute), reads (get event, verify, tag lookup) and the audit reads.
4. Staff sign-in with a CAC (demo bearer tokens stand in) and the real CAC barcode parser (Edge and Vault).
5. AMS export endpoint (Arjo's export function exists, the Gatekeeper endpoint is not built).
6. Test 4 (network outage and resync, SR-5) and offline sync.
