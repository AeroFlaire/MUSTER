# Gatekeeper security Tests 1 and 2: results

Run once on 2026-11-17 by `python run_security_tests.py` (gatekeeper/api). Synthetic data only, a throwaway encrypted Vault database, the real Gatekeeper and Vault code. Re-run any time; this file is regenerated.

## Test 1: Impersonation and fake record attempts (SR-1, SR-3)

Pass means the system blocks every attempt, no fake record gets in, and every real record shows up in the audit log.

| # | Attempt | Expected | Got | Result | Pass |
|---|---|---|---|---|---|
| 1 | No role (no token) opens an AFT record | 401 | 401 | 0 records | PASS |
| 2 | A made-up token | 401 | 401 | 0 records | PASS |
| 3 | Role claimed in a header, no token (X-Role: oic) | 401 | 401 | 0 records | PASS |
| 4 | Cadet token | 403 | 403 | 0 records | PASS |
| 5 | Cadet token claiming X-Role: oic | 403 | 403 | 0 records | PASS |
| 6 | Wrong role (gate_station) opens an AFT record | 403 | 403 | 0 records | PASS |
| 7 | Wrong role (compiler_station) opens an AFT record | 403 | 403 | 0 records | PASS |
| 8 | Wrong role (instructor) opens an AFT record | 403 | 403 | 0 records | PASS |
| 9 | Cadet who should not be gradeable: barcode of nobody | 404 | 404 | 0 records | PASS |
| 10 | Cadet who should not be gradeable: not a barcode | 400 | 400 | 0 records | PASS |
| 11 | Second live score for the same cadet and session | 409 | 409 | 0 records | PASS |
| 12 | Grader sends the points themselves (cheating the scoring) | 400 | 400 | 0 records | PASS |
| 13 | Edit a saved record: change a result after submit | 409 | 409 | 0 records | PASS |
| 14 | PUT on a saved record | 405 | 405 | 0 records | PASS |
| 15 | PATCH on a saved record | 405 | 405 | 0 records | PASS |
| 16 | DELETE on a saved record | 405 | 405 | 0 records | PASS |
| 17 | Grader reopens their own submitted record | 403 | 403 | 0 records | PASS |
| 18 | Every real record is in the audit log (1 event, 5 results, 1 opened, 5 entered, 1 submitted entries) | all present, chain PASS | chain PASS | matches | PASS |

Fake records accepted: **0**. Test 1 result: **PASS** (18 of 18 checks).

Not covered here (the arms-room stretch is not built): the wrong-PIN and missing-signature cases.

## Test 2: Attendance impersonation and out-of-section writes (SR-2)

Pass means attempts to mark attendance as someone who is not the section's instructor, or for a cadet not on the roster, are rejected and logged, with zero false attendance records accepted.

| # | Attempt | Expected | Got | Result | Pass |
|---|---|---|---|---|---|
| 1 | Instructor B marks cadet 0 in Instructor A's section | 403 | 403 | 0 records, 1 audit entries | PASS |
| 2 | Instructor B marks cadet 1 in Instructor A's section | 403 | 403 | 0 records, 1 audit entries | PASS |
| 3 | Instructor B marks cadet 2 in Instructor A's section | 403 | 403 | 0 records, 1 audit entries | PASS |
| 4 | Instructor B marks cadet 3, who is not on B's roster | 403 | 403 | 0 records, 1 audit entries | PASS |
| 5 | Instructor B marks cadet 5, who is not on B's roster | 403 | 403 | 0 records, 1 audit entries | PASS |
| 6 | Instructor B marks cadet 7, who is not on B's roster | 403 | 403 | 0 records, 1 audit entries | PASS |
| 7 | Instructor A marks a barcode for nobody | 403 | 403 | 0 records, 1 audit entries | PASS |
| 8 | Instructor B reads the roster of Instructor A's section | 403 | 403 | 0 records, 1 audit entries | PASS |
| 9 | A section that does not exist | 404 | 404 | 0 records, 1 audit entries | PASS |
| 10 | Not an instructor (cadet) marks attendance | 403 | 403 | 0 records, 0 audit entries | PASS |
| 11 | Not an instructor (grader) marks attendance | 403 | 403 | 0 records, 0 audit entries | PASS |
| 12 | Not an instructor (oic) marks attendance | 403 | 403 | 0 records, 0 audit entries | PASS |
| 13 | Not an instructor (admin) marks attendance | 403 | 403 | 0 records, 0 audit entries | PASS |
| 14 | Not an instructor (gate_station) marks attendance | 403 | 403 | 0 records, 0 audit entries | PASS |
| 15 | Impersonation: no token, X-Role: instructor | 401 | 401 | 0 records, 0 audit entries | PASS |
| 16 | Made-up token | 401 | 401 | 0 records, 0 audit entries | PASS |

False attendance records accepted: **0**. Test 2 result: **PASS** (16 of 16 checks).

A rejection that is stopped by the role check at the Gatekeeper (a cadet, a grader, no token) is refused before anything reaches the Vault and writes 0 audit entries by design, so it is not logged. A rejection of a real instructor (wrong section, not on the roster) writes one rejected-attempt audit entry that holds the section, the date and the reason only.

## Honest limits

- Staff sign in with demo bearer tokens. Real CAC sign-in is not built, so "impersonation" here means no token, a made-up token, a cadet token, a wrong role and a role claimed in a header.
- Cadet barcodes use the synthetic test format. The real CAC parser belongs to Edge.
- Cadet signing at the compiler station and exports are not part of these tests.
