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

## Live mode: the API writes to the Vault (PBI-04 L24)

`live_app.py` is the same API with three endpoints made real. They write to a Vault
database, and each POST writes the record and its audit entry together or not at all
(the Vault does both in one transaction, and the tests prove it):

| Endpoint | Writes |
|---|---|
| POST /v0/sessions/{id}/events | one `fitness_event` + one `EVENT_OPENED` audit entry |
| POST /v0/events/{id}/components | one `fitness_component` + one `COMPONENT_ENTERED` audit entry |
| POST /v0/events/{id}/submit | status change to SUBMITTED + one `EVENT_SUBMITTED` audit entry |

Every other endpoint still returns its canned example.

Setup, once (PowerShell, from `gatekeeper/api/`):

    pip install -r requirements.txt
    $env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
    python live_app.py --reset

`--reset` builds `muster_demo.db` (git-ignore it) with an admin, an OIC, a grader, a compiler
station, the ten synthetic cadets from `tests/synthetic_test_pack.json`, and one open AFT
session. **The demo session id is 1, not 7**, so use 1 in Edge's session field when talking
to live mode. Barcodes are the test pack's `PDF417_TEST_DATA_<EDIPI>_END`. Run `python app.py`
instead to get the all-canned stub back.

Rules the code follows:
- Gatekeeper makes exactly one Vault write call per POST and never wraps two in its own
  transaction. The Vault's `db.transaction()` does the all-or-nothing part.
- The barcode is parsed and HMAC hashed inside `vault_bridge.hash_barcode` and thrown away.
  The error text never echoes it.
- Vault errors map to contract codes in `vault_bridge.map_error`.

Known gaps (also in the contract's open items): the real CAC barcode parser is not built
(test pack format only), and the role to Vault user mapping is a dev shortcut (first active
user with that role). Tests: `python -m unittest discover -s tests -v`. They need the Vault
package (`<repo>/vault`) and its `cryptography` dependency.
