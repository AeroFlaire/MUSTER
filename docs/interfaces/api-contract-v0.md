# MUSTER API Contract v0 (revision 2)

Status: DRAFT for team acknowledgement. Revision 2, 20-Oct-2026 (L23).
Owner: Alena (Gatekeeper). Consumers: Rhemmy (Edge), Arjo (Vault), Eliakim (timing gate).
Traces to: SR-1, SR-3, SR-4. Cards: PBI-02 L22 Draft API contract v0, PBI-03 L23 API skeleton.

Rule from the integration rules: a change to field names or endpoints is posted on the contract card, the version is bumped, and the other owners acknowledge it before it is merged.

## What changed since revision 1

Revision 1 was written before the Vault, the README design, and the shared test pack were visible. This revision follows what is already built:

- Cadet identity: Edge sends the raw barcode to Gatekeeper. Gatekeeper parses it, makes the HMAC-SHA256 EDIPI hash (`identity.hash_edipi`), and discards the raw string. Edge never hashes and never holds the key. This settles revision 1 open item 4.
- Roles are the Vault's roles: admin, oic, grader, compiler_station, gate_station, instructor, arms_nco. Revision 1's cadet, cadre, and timing_gate roles are gone. Cadets are not API users: they sign at the score compiler with their CAC.
- A score is a lifecycle (ENTERED, SUBMITTED, SIGNED, plus DISPUTED, SUPERSEDED, EXPORTED), not one POST. Endpoints now follow `fitness.py` and `ioct.py` call for call.
- IOCT elapsed time is computed by the Vault from raw gate reads. A gate sends only tag, gate, and its timestamp.
- The audit entry and its hash formula are now exactly the Vault's (`audit.py`, `canonical.py`).
- Field names follow the Vault: `client_ref` (not idempotency_key), `raw_value`, component codes MDL, HRP, SDC, PLK, 2MR, ids are integers, `_ms` suffix means epoch milliseconds.

## 1. Conventions

- JSON, UTF-8. Field names are snake_case. Timestamps are ISO 8601 UTC with milliseconds and a trailing Z. A field ending in `_ms` is integer milliseconds since the Unix epoch.
- Every request to `/v0/...` carries `X-Contract-Version: v0`. Anything else is 400 `unsupported_contract_version`.
- The API is the only caller of the Vault. Edge never talks to the Vault. Gates never talk to the Vault.
- The client never sends a score total, an event id, a sequence number, or a hash. Gatekeeper and the Vault compute them. Edge is untrusted, so anything it claims is verified.
- No raw EDIPI and no raw barcode appears in any response, log line, or database column. The barcode appears only in the two request bodies that carry it (sections 4 and 5).
- Binary values (the bytes a cadet signs, and the signature) travel as base64 in fields ending `_b64`.
- There is no update or delete endpoint for any resource. A correction to a signed score is a new event that supersedes the old one, and both are kept.
- Retries: `client_ref` is a unique id the caller makes up. Sending the same `client_ref` twice returns the original result instead of creating a second one (SR-5). It is available on open event, ioct pair, and gate reads.
- Stub note: in the stub the role comes from the `X-Role` header. That is a stand-in. The real mechanism maps a signed-in session to a Vault `app_user` id and is an open item (section 12).

## 2. Roles and endpoints

Role sets, copied from the Vault: GRADER = grader, oic. STATION = compiler_station, oic. OIC = oic, admin. A caller with a missing role, an unknown role (for example `cadet`), or the wrong role gets 403 `forbidden_role`. The Vault checks the role again on every write (defense in depth).

| Method and path | Roles | Vault call |
|---|---|---|
| GET /v0/health | any role | none |
| POST /v0/sessions | oic, admin | fitness.open_session |
| POST /v0/sessions/{session_id}/close | oic, admin | fitness.close_session |
| POST /v0/sessions/{session_id}/events | grader, oic | fitness.open_event (AFT) |
| POST /v0/events/{event_id}/components | grader, oic | fitness.record_component |
| POST /v0/events/{event_id}/submit | grader, oic | fitness.submit |
| POST /v0/events/{event_id}/cadet-challenge | compiler_station, oic | fitness.request_cadet_challenge |
| POST /v0/events/{event_id}/accept | compiler_station, oic | fitness.accept |
| POST /v0/events/{event_id}/dispute | compiler_station, oic | fitness.dispute |
| POST /v0/events/{event_id}/reopen | oic, admin | fitness.reopen |
| POST /v0/events/{event_id}/supersede | oic, admin | fitness.supersede |
| GET /v0/events/{event_id} | grader, compiler_station, oic, admin | fitness.scorecard |
| GET /v0/events/{event_id}/verify | oic, admin | fitness.verify_event |
| POST /v0/sessions/{session_id}/ioct/pair | grader, oic | ioct.pair |
| POST /v0/sessions/{session_id}/gate-reads | gate_station, oic | ioct.record_gate_read |
| POST /v0/events/{event_id}/ioct/compute | grader, compiler_station, oic | ioct.compute_run |
| GET /v0/sessions/{session_id}/ioct/tags/{tag_uid}/event | compiler_station, oic | ioct.active_event_for_tag |
| GET /v0/audit/verify | oic, admin | audit.verify |
| GET /v0/audit/head | oic, admin | audit.head |
| GET /v0/audit/history?entity_type=&entity_id= | oic, admin | audit.entries_for |

The audit and verify read roles are a proposal: the Vault does not restrict reads, so this is a Gatekeeper decision for the team to confirm.

## 3. Identity: barcode in, hash inside

1. A grader scans the CAC back barcode. Edge sends the raw barcode string in the `barcode` field of open event or ioct pair, over TLS.
2. Gatekeeper parses the barcode, takes the 10 digit EDIPI, computes `identity.hash_edipi(edipi, key)` (HMAC-SHA256, key from `MUSTER_EDIPI_KEY`, held only by Gatekeeper), and throws the raw strings away.
3. Gatekeeper passes only the 64 character hash to the Vault (`cadet_edipi_hash`). The Vault looks the cadet up with `identity.cadet_by_hash`. An unknown cadet is 404 `not_found`.

Why Edge cannot do the hashing: the HMAC key must stay out of untrusted Edge, and a plain unkeyed hash of a 10 digit ID can be reversed by brute force. Edge may parse the barcode for the screen if it wants, but it must still send the barcode string to Gatekeeper.

Gatekeeper's barcode parser is not built yet. The test pack barcode format `PDF417_TEST_DATA_<EDIPI>_END` is what the stub and the tests assume.

## 4. AFT flow

Status path: ENTERED, then SUBMITTED, then SIGNED. A cadet who will not sign sets DISPUTED, and an OIC reopens it back to ENTERED. A signed score that is wrong is SUPERSEDED by a new ENTERED event.

Every event response has the same wrapper: the event id, its status, and the `scorecard`. The scorecard is exactly the object the Vault builds in `fitness.scorecard`, and it is the exact record the cadet signs (section 6). It does not contain the status.

`total_points` is the sum of the component points, or null if there are no components or any component has no points. Points are entered by the grader. AFT scoring tables are not built yet (RFI to DPE), so every `points` value in these examples is an invented placeholder.

### 4.1 Open a test session: POST /v0/sessions (oic, admin)

Request body: `test_type` (`AFT` or `IOCT`), `session_date` (`YYYY-MM-DD`), `location`.

```json
{
  "test_type": "AFT",
  "session_date": "2026-10-22",
  "location": "Test Gym"
}
```

Response 201:

```json
{
  "session_id": 7,
  "test_type": "AFT",
  "session_date": "2026-10-22",
  "location": "Test Gym",
  "oic_id": 2,
  "opened_at": "2026-10-22T12:55:00.000Z",
  "closed_at": null
}
```

### 4.2 Close a session: POST /v0/sessions/{session_id}/close (oic, admin)

Response 200:

```json
{
  "session_id": 7,
  "closed_at": "2026-10-22T14:30:00.000Z"
}
```

### 4.3 Open an AFT event: POST /v0/sessions/{session_id}/events (grader, oic)

Request body: `barcode` (required), `client_ref` (optional, see conventions). Only for an AFT session. On an IOCT session it is 409 `invalid_transition`, because IOCT events are opened by pairing a wristband. A cadet who already has a live score in this session is also 409.

```json
{
  "barcode": "PDF417_TEST_DATA_9000000001_END",
  "client_ref": "6f1c2b0e-3d44-4a8e-9b7a-2c1d5e8f9a10"
}
```

Response 201:

```json
{
  "event_id": 11,
  "status": "ENTERED",
  "scorecard": {
    "event_id": 11,
    "test_type": "AFT",
    "session_id": 7,
    "session_date": "2026-10-22",
    "location": "Test Gym",
    "cadet_edipi_hash": "36b5084b6d28f0a06f98200ce4934df8857944b7870d7b96e7d4cf2fb369d510",
    "cadet_name": "Alpha, Cadet",
    "grader_id": 3,
    "supersedes_event_id": null,
    "components": [],
    "total_points": null
  }
}
```

### 4.4 Enter a component: POST /v0/events/{event_id}/components (grader, oic)

Request body: `component` (MDL, HRP, SDC, PLK, or 2MR), `raw_value` (string, as measured), `points` (integer 0 to 100, or null). Only while the event is ENTERED. Posting the same component again replaces it, and the Vault writes a COMPONENT_CHANGED audit entry that keeps the old value. Once submitted, components are locked.

```json
{
  "component": "MDL",
  "raw_value": "340",
  "points": 100
}
```

Response 200:

```json
{
  "event_id": 11,
  "component": "MDL",
  "raw_value": "340",
  "points": 100
}
```

### 4.5 Submit: POST /v0/events/{event_id}/submit (grader, oic)

No body. The scorecard must be complete (all five components, or a computed IOCT run), else 409 `invalid_transition`. The Vault supports an optional grader signature on submit, switched off for now (`REQUIRE_GRADER_SIGNATURE = False`), so v0 has no grader signature fields.

Response 200 (AFT event 11 shown):

```json
{
  "event_id": 11,
  "status": "SUBMITTED",
  "scorecard": {
    "event_id": 11,
    "test_type": "AFT",
    "session_id": 7,
    "session_date": "2026-10-22",
    "location": "Test Gym",
    "cadet_edipi_hash": "36b5084b6d28f0a06f98200ce4934df8857944b7870d7b96e7d4cf2fb369d510",
    "cadet_name": "Alpha, Cadet",
    "grader_id": 3,
    "supersedes_event_id": null,
    "components": [
      {
        "component": "2MR",
        "raw_value": "13:15",
        "points": 100
      },
      {
        "component": "HRP",
        "raw_value": "60",
        "points": 100
      },
      {
        "component": "MDL",
        "raw_value": "340",
        "points": 100
      },
      {
        "component": "PLK",
        "raw_value": "3:40",
        "points": 100
      },
      {
        "component": "SDC",
        "raw_value": "1:30",
        "points": 100
      }
    ],
    "total_points": 500
  }
}
```

### 4.6 Read, verify, dispute, reopen, supersede

- GET /v0/events/{event_id} returns the same wrapper as above.
- GET /v0/events/{event_id}/verify rebuilds the scorecard from the tables and compares it to what the cadet signed, so an edit made directly in the database is caught.

```json
{
  "event_id": 11,
  "status": "SIGNED",
  "signed": true,
  "ok": true,
  "problems": []
}
```

- POST /v0/events/{event_id}/dispute with body `{"reason": "..."}` (compiler_station, oic). The reason is required. Only from SUBMITTED.

```json
{
  "event_id": 11,
  "status": "DISPUTED"
}
```

- POST /v0/events/{event_id}/reopen with body `{"note": "..."}` (oic, admin). Only from DISPUTED or SUBMITTED.

```json
{
  "event_id": 11,
  "status": "ENTERED"
}
```

- POST /v0/events/{event_id}/supersede with body `{"reason": "..."}` (oic, admin). Only from SIGNED or EXPORTED. The old event stays as SUPERSEDED, and a new ENTERED event is opened with the old components copied in. Response 201:

```json
{
  "superseded_event_id": 11,
  "new_event_id": 13,
  "status": "ENTERED"
}
```

## 5. IOCT flow

An RFID band only says "band 17 crossed the mat". It does not know who wears it, so a grader pairs the band to a cadet first. The Vault, not the gate, works out the time.

1. Pair: POST /v0/sessions/{session_id}/ioct/pair (grader, oic). Body: `barcode`, `tag_uid`, `client_ref` (optional). Only for an IOCT session. An unregistered or still assigned wristband is 404 or 409.

```json
{
  "barcode": "PDF417_TEST_DATA_9000000001_END",
  "tag_uid": "TAG-017",
  "client_ref": "0c9e7a52-5b1d-4f0a-8a47-1d3b6e2f7c88"
}
```

Response 201 (the IOCT scorecard has an `ioct` block, null until the run is computed, and no components):

```json
{
  "event_id": 12,
  "status": "ENTERED",
  "scorecard": {
    "event_id": 12,
    "test_type": "IOCT",
    "session_id": 8,
    "session_date": "2026-10-22",
    "location": "Test Gym",
    "cadet_edipi_hash": "36b5084b6d28f0a06f98200ce4934df8857944b7870d7b96e7d4cf2fb369d510",
    "cadet_name": "Alpha, Cadet",
    "grader_id": 3,
    "supersedes_event_id": null,
    "ioct": null
  }
}
```

2. Gate reads: POST /v0/sessions/{session_id}/gate-reads (gate_station, oic). Raw reads only: `tag_uid`, `gate` (START or END), `read_at_ms` (the gate's clock), `gate_device`, `client_ref` (optional). Stored as evidence even if the tag matches nobody. Gates must share a clock or be synced (see open items).

```json
{
  "tag_uid": "TAG-017",
  "gate": "START",
  "read_at_ms": 1792674000000,
  "gate_device": "G1",
  "client_ref": "3a7d9e10-6c42-4b8f-9d15-8e2f0a4c7b61"
}
```

Response 201:

```json
{
  "gate_read_id": 21
}
```

3. Compute: POST /v0/events/{event_id}/ioct/compute (grader, compiler_station, oic). No body. The Vault takes the first END read after pairing and the last START read before it. A time outside 1:00 to 15:00, or a missing read, is 422 `timing_error`. Response 200:

```json
{
  "event_id": 12,
  "elapsed_ms": 155000,
  "elapsed": "2:35.00"
}
```

4. Submit, challenge, accept: the same calls as AFT (sections 4.5 and 6). GET /v0/events/{event_id} for the IOCT event:

```json
{
  "event_id": 12,
  "status": "SUBMITTED",
  "scorecard": {
    "event_id": 12,
    "test_type": "IOCT",
    "session_id": 8,
    "session_date": "2026-10-22",
    "location": "Test Gym",
    "cadet_edipi_hash": "36b5084b6d28f0a06f98200ce4934df8857944b7870d7b96e7d4cf2fb369d510",
    "cadet_name": "Alpha, Cadet",
    "grader_id": 3,
    "supersedes_event_id": null,
    "ioct": {
      "tag_uid": "TAG-017",
      "start_read_at_ms": 1792674000000,
      "end_read_at_ms": 1792674155000,
      "elapsed_ms": 155000,
      "elapsed": "2:35.00"
    }
  }
}
```

5. At the score compiler the cadet taps their band: GET /v0/sessions/{session_id}/ioct/tags/{tag_uid}/event (compiler_station, oic) returns which run it is. The band is released when the cadet signs.

```json
{
  "session_id": 8,
  "tag_uid": "TAG-017",
  "event_id": 12
}
```

## 6. Signing at the score compiler

The cadet signs the exact scorecard with their CAC. MUSTER never sees the PIN. The signature is checked against the public key recorded at enrollment, so no live DoD PKI lookup is needed. The signed bytes include a one-time nonce and the purpose (`FITNESS_ACCEPT`), so a signature cannot be replayed or reused for another purpose.

1. POST /v0/events/{event_id}/cadet-challenge (compiler_station, oic). No body. Only from SUBMITTED. Returns the challenge id and the exact bytes to sign, base64 encoded. In this example the bytes decode to the Vault's canonical JSON for `muster-sig-v1` with the scorecard from section 4.5 inside it.

```json
{
  "event_id": 11,
  "challenge_id": 5,
  "payload_b64": "eyJpc3N1ZWRfYXQiOiIyMDI2LTEwLTIyVDEzOjIwOjAwLjAwMFoiLCJtdXN0ZXIiOiJtdXN0ZXItc2lnLXYxIiwibm9uY2UiOiJuLTJmOWMxYTc3IiwicHVycG9zZSI6IkZJVE5FU1NfQUNDRVBUIiwicmVjb3JkIjp7ImNhZGV0X2VkaXBpX2hhc2giOiIzNmI1MDg0YjZkMjhmMGEwNmY5ODIwMGNlNDkzNGRmODg1Nzk0NGI3ODcwZDdiOTZlN2Q0Y2YyZmIzNjlkNTEwIiwiY2FkZXRfbmFtZSI6IkFscGhhLCBDYWRldCIsImNvbXBvbmVudHMiOlt7ImNvbXBvbmVudCI6IjJNUiIsInBvaW50cyI6MTAwLCJyYXdfdmFsdWUiOiIxMzoxNSJ9LHsiY29tcG9uZW50IjoiSFJQIiwicG9pbnRzIjoxMDAsInJhd192YWx1ZSI6IjYwIn0seyJjb21wb25lbnQiOiJNREwiLCJwb2ludHMiOjEwMCwicmF3X3ZhbHVlIjoiMzQwIn0seyJjb21wb25lbnQiOiJQTEsiLCJwb2ludHMiOjEwMCwicmF3X3ZhbHVlIjoiMzo0MCJ9LHsiY29tcG9uZW50IjoiU0RDIiwicG9pbnRzIjoxMDAsInJhd192YWx1ZSI6IjE6MzAifV0sImV2ZW50X2lkIjoxMSwiZ3JhZGVyX2lkIjozLCJsb2NhdGlvbiI6IlRlc3QgR3ltIiwic2Vzc2lvbl9kYXRlIjoiMjAyNi0xMC0yMiIsInNlc3Npb25faWQiOjcsInN1cGVyc2VkZXNfZXZlbnRfaWQiOm51bGwsInRlc3RfdHlwZSI6IkFGVCIsInRvdGFsX3BvaW50cyI6NTAwfX0="
}
```

2. The station shows the scorecard, and the cadet signs the decoded bytes with their CAC.

3. POST /v0/events/{event_id}/accept (compiler_station, oic). Body: `challenge_id` and `signature_b64`. If the scorecard changed between showing it and signing it, the signature is refused (400 `signature_invalid`). Rejections are written to the audit log. Response 200 returns the audit sequence number of the signing:

```json
{
  "challenge_id": 5,
  "signature_b64": "U1lOVEhFVElDLVNJR05BVFVSRQ=="
}
```

```json
{
  "event_id": 11,
  "status": "SIGNED",
  "audit_seq": 9
}
```

The stub does not check signatures. Real CAC signing has only been tested with software keys.

## 7. Audit log

Every Vault change writes one audit entry in the same transaction, so no record exists without its entry. The log is append only (database triggers refuse update and delete) and hash chained.

An entry has these fields: `seq` (1, 2, 3, no gaps), `ts`, `function` (CORE, FITNESS, IOCT, ATTENDANCE, ARMS), `action` (for example EVENT_OPENED), `actor_id` (an app_user id, or null for the system), `entity_type`, `entity_id`, `data`, `prev_hash`, `entry_hash`.

`data` is a string: the canonical JSON text of what happened. Canonical means keys sorted, no spaces (separators `,` and `:`), UTF-8, no NaN.

`entry_hash` is the SHA-256, as lowercase hex, of the canonical JSON bytes of this object: `{seq, ts, function, action, actor_id, entity_type, entity_id, data, prev_hash}`, where `data` is the string above. For seq 1, `prev_hash` is 64 zeros. This is `audit.compute_entry_hash` in the Vault. The example below was produced with that exact code.

- GET /v0/audit/history?entity_type=fitness_event&entity_id=11 returns the history of one record, oldest first:

```json
{
  "entity_type": "fitness_event",
  "entity_id": 11,
  "entries": [
    {
      "seq": 2,
      "ts": "2026-10-22T13:02:11.480Z",
      "function": "FITNESS",
      "action": "EVENT_OPENED",
      "actor_id": 3,
      "entity_type": "fitness_event",
      "entity_id": 11,
      "data": "{\"cadet_id\":1,\"client_ref\":\"6f1c2b0e-3d44-4a8e-9b7a-2c1d5e8f9a10\",\"session_id\":7,\"supersedes_event_id\":null,\"test_type\":\"AFT\"}",
      "prev_hash": "cafa8e1bc7ae4898d85ef8db7cb177e4363faedc49fd29f83934acd63ec3df90",
      "entry_hash": "f93810c238f56f20c5d61e16cc9fd7c937f0f3861719b55088f341b3d8c5b00a"
    },
    {
      "seq": 3,
      "ts": "2026-10-22T13:04:30.915Z",
      "function": "FITNESS",
      "action": "COMPONENT_ENTERED",
      "actor_id": 3,
      "entity_type": "fitness_event",
      "entity_id": 11,
      "data": "{\"component\":\"MDL\",\"points\":100,\"raw_value\":\"340\"}",
      "prev_hash": "f93810c238f56f20c5d61e16cc9fd7c937f0f3861719b55088f341b3d8c5b00a",
      "entry_hash": "f2c429a7ceeb4fd5ee0cc4910ed1643656438faa8c4f182384b3456ec33528da"
    }
  ]
}
```

- GET /v0/audit/verify walks the whole chain and returns PASS (`ok` true) or the first broken sequence number and a reason:

```json
{
  "ok": true,
  "entries_checked": 3,
  "first_bad_seq": null,
  "reason": null,
  "head_hash": "f2c429a7ceeb4fd5ee0cc4910ed1643656438faa8c4f182384b3456ec33528da"
}
```

- GET /v0/audit/head returns the latest seq and hash. Print or export it as an outside anchor:

```json
{
  "seq": 3,
  "entry_hash": "f2c429a7ceeb4fd5ee0cc4910ed1643656438faa8c4f182384b3456ec33528da",
  "ts": "2026-10-22T13:04:30.915Z"
}
```

## 8. Errors

One shape for every error. The `message` is safe to show on an Edge error screen.

```json
{
  "error": {
    "code": "forbidden_role",
    "message": "Role 'compiler_station' may not call this endpoint.",
    "request_id": "req_9A1C3E7B20"
  }
}
```

| HTTP | code | When (Vault error it maps from) |
|---|---|---|
| 400 | validation_failed | Missing field, wrong type, out of range. The message names the field (ValueError) |
| 400 | unsupported_contract_version | Header missing or not `v0` |
| 400 | challenge_invalid | Challenge expired, already used, or for another event (ChallengeError) |
| 400 | signature_invalid | Signature does not verify, or the scorecard changed since it was shown (SignatureInvalid) |
| 403 | forbidden_role | Role missing, unknown, or not allowed (PermissionDenied) |
| 404 | not_found | No such session, event, cadet, or wristband (NotFound) |
| 405 | method_not_allowed | Any PUT, PATCH, or DELETE |
| 409 | invalid_transition | Wrong status or session type, closed session, or the cadet already has a live score (InvalidTransition) |
| 422 | timing_error | Missing START or END read, or a time outside the allowed range (TimingError) |
| 500 | internal_error | A forced failure writes 0 records and 0 audit entries |

A write that is refused by the signing checks still writes one rejection entry to the audit log.

Stub only: send `X-Stub-Scenario: not_found`, `invalid_transition`, `challenge_invalid`, `signature_invalid`, `timing_error`, or `internal_error` on a request that passes the role and body checks, and the stub returns that error. Use it to build error screens.

## 9. Field names versus the Vault

| API field | Vault name |
|---|---|
| barcode | not stored. Gatekeeper turns it into `cadet.edipi_hash` |
| cadet_edipi_hash (in scorecard) | cadet.edipi_hash |
| session_id | test_session.id |
| test_type, session_date, location | test_session.test_type, session_date, location |
| oic_id | test_session.oic_id (an app_user id) |
| event_id | fitness_event.id |
| status | fitness_event.status |
| grader_id | fitness_event.grader_id |
| supersedes_event_id | fitness_event.supersedes_event_id |
| client_ref | fitness_event.client_ref, gate_read.client_ref |
| component, raw_value, points | fitness_component.component, raw_value, points |
| tag_uid | rfid_tag.tag_uid, gate_read.tag_uid |
| gate, read_at_ms, gate_device | gate_read.gate, read_at_ms, gate_device |
| elapsed_ms | ioct_run.elapsed_ms |
| seq, ts, function, action, actor_id, entity_type, entity_id, data, prev_hash, entry_hash | audit_log columns of the same names |

## 10. Mapping the shared test pack

`tests/synthetic_test_pack.json` uses names that are not the API's. A test harness maps it like this:

| Test pack | API |
|---|---|
| barcode_string | `barcode` in open event and ioct pair |
| edipi | never sent. Only for checking the hash on the Gatekeeper side |
| aft_results.mdl_lbs | component MDL, `raw_value` as a string, for example "340" |
| aft_results.hrp_reps | component HRP |
| aft_results.sdc_time | component SDC, for example "1:30" |
| aft_results.plank_time | component PLK, for example "3:40" |
| aft_results.twomr_time | component 2MR, for example "13:15" |
| ioct_time | not an input. It is the expected output of compute: START read at t and END read at t plus 155000 ms gives "2:35" |
| section | attendance, not in v0 |
| fake_name | cadet enrollment (`identity.add_cadet`), not an API call in v0 |
| contract_version "1.0" | this contract uses `v0` in the path and header. One label should win |

## 11. Not in v0

Attendance, arms room, receipts, export to AMS, offline buffering and resync, user and cadet enrollment, CAC key enrollment, RFID tag registration, and the optional grader signature. The Vault has most of these. They get endpoints in later sprints.

## 12. Open items for the team

1. Scoring tables and the IOCT pass standard: RFI to DPE, owner Alena, due 22-Oct. Until then points come from the grader and are placeholders.
2. Role mechanism: the stub trusts `X-Role`. The real one must map a session to a Vault `app_user` id and must never take the role from Edge.
3. Rhemmy's L24 card says Edge parses the barcode and hashes the ID. The README and the Vault say Gatekeeper does, with a key Edge must never hold. The card text should change to match this contract.
4. Time format: the test pack has "1:30" and the Vault schema comment shows "01:45". The Vault stores `raw_value` as given. One format (`m:ss` or `mm:ss`) should be agreed before scoring code reads it.
5. Gate clocks: elapsed time uses only gate timestamps, so the START and END gates must share a clock or be synced. Needs Eliakim's timing spec.
6. Who may read audit and verify endpoints (section 2).
7. Encryption at rest (SR-4) is not built in the Vault yet, and real CAC signing is untested. Both are noted in the Vault README.
8. Version label: the pack says "1.0", Trello says v0.
