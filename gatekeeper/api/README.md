# Gatekeeper stub API (contract v0, revision 2)

PBI-03 L23. Owner: Alena. Zone 2 (Gatekeeper). Lives in `gatekeeper/api/`.

A running API where every v0 endpoint returns its canned example from
`docs/interfaces/api-contract-v0.md`, so Edge, Vault, and the timing gate can
build against it right away. The endpoints follow the Vault call for call
(`fitness.py`, `ioct.py`, `audit.py`).

It is a stub:

- Nothing is stored, and responses do not change with the request body.
- The caller's role comes from the `X-Role` header, using the Vault's role names.
  This is a stand-in. The real mechanism is open item 2 in the contract. Edge must
  never be the source of a role.
- Requests are validated and rejected the way the real API will reject them, so
  Edge can build its error screens now.

## Run

You have to start it and leave it running. Open its own terminal window for it:

    cd gatekeeper/api
    pip install -r requirements.txt
    python app.py            # http://127.0.0.1:5000

When it works you see `Running on http://127.0.0.1:5000`. Keep that window open while you
use the web app, and press Ctrl+C to stop it. Check it from a browser or another terminal:

    curl.exe -H "X-Contract-Version: v0" -H "X-Role: oic" http://127.0.0.1:5000/v0/health

The web app should call `http://127.0.0.1:5000` (or `http://localhost:5000`). Browsers may
call it from a page opened straight from disk: the stub allows any origin (CORS).

## Test (run from gatekeeper/api/)

    python -m unittest discover -s tests -v
    # or, if you use pytest like the Vault does:
    python -m pytest -v

The tests check every endpoint against its example, the role table, validation,
the audit hash formula, and that every example appears in the contract.

## Headers

| Header | Needed | Notes |
|---|---|---|
| X-Contract-Version | always | Must be `v0`, else 400 `unsupported_contract_version` |
| X-Role | always | admin, oic, grader, compiler_station, gate_station, instructor, or arms_nco. Missing, unknown (for example `cadet`), or not allowed is 403 `forbidden_role` |
| X-Stub-Scenario | optional | Forces an error after the role and body checks pass: not_found, invalid_transition, forbidden_role, challenge_invalid, signature_invalid, timing_error, internal_error. For building error screens |

## Ids in the examples

AFT session 7, IOCT session 8, AFT event 11, IOCT event 12, wristband `TAG-017`.
Any other id returns 404 `not_found`.

## Endpoints

See contract section 2 for the full table with roles. In order of use:

| AFT flow | |
|---|---|
| POST /v0/sessions | open a session (oic, admin) |
| POST /v0/sessions/7/events | scan a cadet, open an AFT event (grader, oic) |
| POST /v0/events/11/components | enter MDL, HRP, SDC, PLK, 2MR (grader, oic) |
| POST /v0/events/11/submit | send to the score compiler (grader, oic) |
| POST /v0/events/11/cadet-challenge | get the bytes the cadet signs (compiler_station, oic) |
| POST /v0/events/11/accept | cadet signature in, event SIGNED (compiler_station, oic) |

| IOCT flow | |
|---|---|
| POST /v0/sessions/8/ioct/pair | pair a wristband to a cadet (grader, oic) |
| POST /v0/sessions/8/gate-reads | raw gate read (gate_station, oic) |
| POST /v0/events/12/ioct/compute | the Vault computes the time (grader, compiler_station, oic) |

Also: close session, dispute, reopen, supersede, get event, verify event, tag lookup,
and the three audit endpoints. PUT, PATCH, and DELETE return 405 (append only).

Try it:

    curl.exe -X POST http://127.0.0.1:5000/v0/sessions/7/events \
      -H 'Content-Type: application/json' \
      -H 'X-Contract-Version: v0' -H 'X-Role: grader' \
      --data @examples/event_open_request.json

## What the stub does not do

- No Vault, no real audit chain, no real signature check, no barcode parsing or hashing.
  The audit example was made with the Vault's own hashing code, so it is a valid chain.
- Points in the examples are invented placeholders until the DPE scoring RFI closes.
- No 401. Until real auth exists, no role means 403.

## Live mode: the API writes to the Vault (PBI-04 L24, PBI-05 L25)

`live_app.py` is the same API with three endpoints made real. They write to a Vault
database. Each POST writes the record and its audit entry together or not at all (the Vault
does both in one transaction), and only a caller with the right role can make them.

| Endpoint | Writes | Roles |
|---|---|---|
| POST /v0/sessions/{id}/events | one `fitness_event` + one `EVENT_OPENED` audit entry | grader, oic |
| POST /v0/events/{id}/components | one `fitness_component` (points computed from the official tables) + one `COMPONENT_ENTERED` audit entry | grader, oic |
| GET /v0/scoring/standards | none. Reads the scoring data file | grader, oic, admin |
| POST /v0/events/{id}/submit | status change to SUBMITTED + one `EVENT_SUBMITTED` audit entry | grader, oic |
| POST /v0/sessions/{id}/ioct/pair | one IOCT event + a wristband pairing, 2 audit entries | grader, oic |
| POST /v0/sessions/{id}/gate-reads | one raw `gate_read` + one `GATE_READ` audit entry | gate_station, oic |
| POST /v0/events/{id}/ioct/compute | one `ioct_run` (time computed by the Vault) + one `RUN_COMPUTED` audit entry | grader, compiler_station, oic |
| POST /v0/events/{id}/reopen | status back to ENTERED + one `EVENT_REOPENED` audit entry | oic, admin |
| POST /v0/events/{id}/supersede | old event marked SUPERSEDED, a new event that points at it (`supersedes_event_id`) + audit entries | oic, admin |
| POST /v0/attendance/marks | one `attendance` row + one `ATTENDANCE_MARKED` audit entry. A refused write (another instructor's section, or a cadet not on the roster) writes 0 rows and one `ATTENDANCE_REJECTED` entry | instructor (own section only) |
| GET /v0/attendance/sections/{id}/roster?class_date= | none | instructor (own section only) |

Every other endpoint (signing, dispute, reads, audit) still returns its canned example and still reads `X-Role`.

### Scoring (contract v1, PBI-07)

`scoring.py` loads `data/aft_scoring_2025.json`, the official Army Fitness Test score tables (approved 15 May 2025, effective 1 June 2025), for all five events, ten age groups, and both columns (M|C and F). The grader sends the raw result, `age_group`, and `scoring_column`. The Gatekeeper looks up the points and the Vault saves them. A request that sends `points` is refused. Submit returns a `grading` block with the total and PASS, FAIL, or INCOMPLETE.

Every graded result is labeled `pending_dpe`: the tables are official but the DPE has not confirmed which standards apply to cadets, and the pass rule (60 points on every event) is a placeholder. Tests: `tests/test_scoring.py` (25 hand-checked cases from the printed tables, 5 per event) and `tests/test_live_scoring.py`.

### Who may call (SR-1)

Live writes need `Authorization: Bearer <token>`. The role is **never** taken from the
request: the token maps to a Vault user, and the role is read from the Vault's own user record.
`X-Role` is ignored on these three endpoints.

| Caller | Result |
|---|---|
| no token, bad token, wrong scheme | 401 `unauthenticated`. Nothing reaches the Vault |
| cadet token (a cadet is not a Vault user) | 403 `forbidden_role`. Nothing reaches the Vault |
| staff user who is inactive, or whose role is not allowed here (compiler_station, admin) | 403 `forbidden_role`. Nothing reaches the Vault |
| grader or oic | the write goes through (the Vault checks the role again) |

Only SHA-256 hashes of tokens are stored. Tokens are shown once when the demo is built.

### Run it (PowerShell, from `gatekeeper/api/`)

    pip install -r requirements.txt
    $env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
    $env:MUSTER_DB_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
    python live_app.py --reset

The Vault database is encrypted (SQLCipher). `MUSTER_DB_KEY` must be the **same** value every
time you reuse the database. Keep it in your own notes, never in the repo. A new key means
running `--reset` again. An old `muster_demo.db` from before encryption won't open; delete it
and run `--reset`. (`demo_full_run.py` makes its own throwaway key, so it needs nothing extra.)

`--reset` builds `muster_demo.db` and `muster_demo_credentials.json` (git-ignore both), then
prints one demo token per role (admin, oic, grader, compiler_station, cadet). Copy the
**grader** token and send it as `Authorization: Bearer <token>`. Try the cadet token to see the
403. The demo AFT session id is **1** (not 7) and the demo IOCT session is **2**, with wristbands TAG-017 to TAG-019. Barcodes are the test pack's
`PDF417_TEST_DATA_<EDIPI>_END`. Run `python app.py` instead to get the all-canned stub back.

Test, for example, with PowerShell (replace TOKEN):

    curl.exe -X POST http://127.0.0.1:5000/v0/sessions/1/events -H "X-Contract-Version: v0" -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d "{\"barcode\": \"PDF417_TEST_DATA_9000000001_END\"}"

Rules the code follows:
- Gatekeeper makes exactly one Vault write call per POST and never wraps two in its own
  transaction. The Vault's `db.transaction()` does the all-or-nothing part.
- Authentication and the role check run before the body is read or the Vault is touched.
- The barcode is parsed and HMAC hashed in `vault_bridge.hash_barcode` and thrown away. Error text
  never echoes it.
- Vault errors map to contract codes in `vault_bridge.map_error`.

Known gaps (also in the contract's open items): the real CAC barcode parser is not built (test pack
format only), and staff do not yet get tokens by signing in with a CAC. Tests:
`python -m unittest discover -s tests -v`. They need the Vault package (`<repo>/vault`) and its
`cryptography` dependency.

### IOCT scoring, corrections and attendance (L31, L32)

* A computed IOCT run carries a `grading` block. The DPE has not given the IOCT standard, so `result` is `PENDING_STANDARD`. When they answer, type the pass time in seconds into `data/ioct_standard.json` (`max_elapsed_seconds`) and it grades PASS or FAIL. The time is never changed by grading.
* Corrections never overwrite: `supersede` keeps the old event and opens a new one that points at it. Both corrections are OIC only.
* Attendance: the Vault enforces own-section and on-the-roster. The Gatekeeper adds the rejected-attempt audit entry (a refused Vault write rolls back, so on its own it would leave no trace). The entry holds the section, date and reason only.
* Demo accounts: `instructor` owns section 1 (all ten cadets), `instructor_b` owns section 2 (only the first cadet).

## Security Tests 1 and 2

    python run_security_tests.py

Writes the results to `docs/evidence/gatekeeper-tests-1-2-results.md` (Test 1: fake record attempts, Test 2: out-of-section attendance writes). Exit code 1 if any check fails.

## Sprint 1 review demo (one laptop, every access level)

    python demo_full_run.py --pause     # terminal run, waits for Enter between scenes
    python demo_full_run.py --screen    # browser part: then open docs/interfaces/live_demo.html

`demo_full_run.py` builds its own throwaway Vault database and walks the whole path over real HTTP: a grader's AFT scorecard, every access level against every write, a simulated IOCT run, a forced audit failure, and a tamper check. The script, narration and recording notes are in `docs/demo/gatekeeper-demo-script.md`.
