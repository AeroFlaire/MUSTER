# Gatekeeper stub API (contract v0)

PBI-03 L23. Owner: Alena. Zone 2 (Gatekeeper). Lives in `gatekeeper/api/`.

A running API where every v0 endpoint returns its canned example from
`docs/interfaces/api-contract-v0.md`, so Edge, Vault, and the timing gate can
build against it right away. It is a stub:

- Nothing is stored, and responses do not change with the request body.
- The caller's role comes from the `X-Role` header. This is a stand-in. The real
  mechanism is open item 3 in the contract. Edge must never be the source of a role.
- Requests are validated and rejected the way the real API will reject them, so
  Edge can build its error screens now.

## Run

    cd gatekeeper/api
    pip install -r requirements.txt
    python app.py            # http://127.0.0.1:5000

## Test (run from gatekeeper/api/)

    python -m unittest discover -s tests -v
    # or, if you use pytest like the Vault does:
    python -m pytest -v

## Headers

| Header | Needed | Notes |
|---|---|---|
| X-Contract-Version | always | Must be `v0`, else 400 `unsupported_contract_version` |
| X-Role | always | `grader`, `cadre`, `cadet`, or `timing_gate`. Missing, unknown, or wrong is 403 `forbidden_role` |
| X-Cadet-Id-Hash | cadet only | Stub for "own record only". A cadet asking for another cadet's record gets 404 |

## Endpoints

| Method and path | Roles | Returns |
|---|---|---|
| GET /v0/health | all | `{"status":"ok", ...}` |
| POST /v0/fitness-records | grader | 201, `examples/fitness_record_response.json` |
| GET /v0/fitness-records/{record_id} | grader, cadre, cadet (own) | 200, same record. Use id `b2d1a9c4-0f6e-4e35-8f02-77a5c3e10d21` |
| GET /v0/cadets/{cadet_id_hash}/fitness-records | grader, cadre, cadet (own) | 200, list with the example record |
| POST /v0/ioct-timings | timing_gate | 201, `examples/ioct_timing_response.json` |
| GET /v0/audit/events | cadre | 200, list with the example audit event |
| GET /v0/audit/verify | cadre | 200, `{"result":"PASS","entries_checked":42}` |

PUT, PATCH, and DELETE on anything return 405 `method_not_allowed` (append only).

Try it:

    curl -X POST http://127.0.0.1:5000/v0/fitness-records \
      -H 'Content-Type: application/json' \
      -H 'X-Contract-Version: v0' -H 'X-Role: grader' \
      --data @examples/fitness_record_request.json

## What the stub does not do yet

- No Vault, no audit chain, no idempotency handling. Those come in L24 and later.
- Scores are the placeholder values from the contract (`table_version: PLACEHOLDER-v0`).
- No 401. Until the real auth exists, no role means 403, as in contract section 2.
