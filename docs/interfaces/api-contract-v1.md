# MUSTER API Contract v1

Status: DRAFT for team acknowledgement. Version v1, 5-Nov-2026 (card: PBI-07 L28, API contract v1). It replaces v0 revision 2.1 (27-Oct, L25). v0 revision 2 was 20-Oct (L23).
Owner: Alena (Gatekeeper). Consumers: Rhemmy (Edge), Arjo (Vault), Eliakim (timing gate).
Traces to: SR-1, SR-3, SR-4. Cards: PBI-07 AFT scoring, PBI-02 contract v0 (earlier), PBI-03 API skeleton (earlier).

Rule from the integration rules: a change to field names or endpoints is posted on the contract card, the version is bumped, and the other owners acknowledge it before it is merged.

## What changed in v1 (PBI-07, five-event AFT record with scoring)

Everything in v0 revision 2.1 still holds (bearer tokens, role read from the Vault, 401 and 403, IOCT, signing, audit). v1 changes only the AFT scoring path:

1. **Points are computed, not typed.** The grader sends the raw result plus the cadet's `age_group` and `scoring_column`. The Gatekeeper looks up the points in the official AFT score tables and the Vault saves them. A request that includes `points` is 400 `validation_failed` and writes nothing. Section 4.4.
2. **Two new request fields on enter a component:** `age_group` (one of `17-21`, `22-26`, `27-31`, `32-36`, `37-41`, `42-46`, `47-51`, `52-56`, `57-61`, `62+`) and `scoring_column` (`M|C` or `F`, the two columns of the printed table). Both required.
3. **Component response grows:** it now also returns `age_group`, `scoring_column` and `standards_status`.
4. **New `grading` block** on open event, submit, and read event (AFT only): `total_points`, `result` (`PASS`, `FAIL`, or `INCOMPLETE`), `failed_events`, `min_points_per_event`, `standards_status`. The total is the sum of the five event scores (500 at most). Section 4.7.
5. **New endpoint** GET /v0/scoring/standards (grader, oic, admin) so the form can build its age group and column choices and label the standards honestly. Section 4.7.
6. **Value formats and limits** (a value outside is 400 `validation_failed` naming `raw_value`): MDL whole pounds 0 to 600; HRP whole repetitions 0 to 150; SDC `m:ss` from 0:30 to 10:00; PLK `m:ss` up to 10:00; 2MR `m:ss` from 5:00 to 60:00. Times are stored in one form, `m:ss`, so `03:40` is saved as `3:40`.
7. **Version header:** send `X-Contract-Version: v1`. `v0` is still accepted so Edge and the gate keep working until Rhemmy and Eliakim acknowledge v1. The URL prefix stays `/v0/` for now, so no path changes.
8. **Standards are labeled.** Every graded response carries `standards_status: "confirmed"`. The tables are the official published ones (approved 15 May 2025, effective 1 June 2025). The pass rule (at least 60 points on every event, so 300 is the lowest passing total) and the column for each sex (men M|C, all women F) were confirmed by the team on 17-Nov-2026. The age group is chosen by the grader for each cadet.

Security: every new or changed endpoint lists its roles in section 2. A cadet, a request with no token, and a role sent by the client (`X-Role`) are all refused on the live endpoints. Each graded write is still one record and one audit entry in one transaction.

What v1 does not change: IOCT scoring is still the L31 card. The cadet signing, dispute, reopen, and supersede endpoints are still canned.

## What changed in revision 2.1 (L25, role check), still in force

- Live write endpoints no longer trust a role sent by the client. A caller sends `Authorization: Bearer <token>`. Gatekeeper maps the token to a Vault `app_user` id and reads the role from the Vault's own user record. `X-Role` is ignored on live endpoints (the stub still uses it).
- New error: 401 `unauthenticated` for a missing, malformed, or unknown token. A known caller who may not do this (a cadet, an inactive user, the wrong role) is still 403 `forbidden_role`. Edge error screens need one new case for 401.
- Time format settled: the official AFT score tables write times as `m:ss` with no leading zero (for example `1:29`). Use `m:ss`.
- Scoring tables: the AFT Score Tables (approved 15 May 2025, effective 1 June 2025) are public and are in hand. What is still open is which age group and column (M|C or F) applies to each cadet.

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
- Every request to `/v0/...` carries `X-Contract-Version: v1`. `v0` is still accepted during the move to v1. Anything else is 400 `unsupported_contract_version`.
- The API is the only caller of the Vault. Edge never talks to the Vault. Gates never talk to the Vault.
- The client never sends a score total, an event id, a sequence number, or a hash. Gatekeeper and the Vault compute them. Edge is untrusted, so anything it claims is verified.
- No raw EDIPI and no raw barcode appears in any response, log line, or database column. The barcode appears only in the two request bodies that carry it (sections 4 and 5).
- Binary values (the bytes a cadet signs, and the signature) travel as base64 in fields ending `_b64`.
- There is no update or delete endpoint for any resource. A correction to a signed score is a new event that supersedes the old one, and both are kept.
- Retries: `client_ref` is a unique id the caller makes up. Sending the same `client_ref` twice returns the original result instead of creating a second one (SR-5). It is available on open event, ioct pair, and gate reads.
- Who is calling: live write endpoints (open event, add component, submit, ioct pair, gate reads, ioct compute) require `Authorization: Bearer <token>`. Gatekeeper looks the token up (it stores only hashes of tokens), gets a Vault `app_user` id, and reads the role from the Vault, so a role change or deactivation in the Vault applies on the next request. The client never states its own role. Cadets are not Vault users, so a cadet credential has no user id and every write is refused. Stub note: the stub, and the endpoints that are still canned, read the role from the `X-Role` header. That is a stand-in until those endpoints go live. How staff obtain tokens (CAC sign in) is a later card.

## 2. Roles and endpoints

Role sets, copied from the Vault: GRADER = grader, oic. STATION = compiler_station, oic. OIC = oic, admin. On live endpoints, a request with no valid token gets 401 `unauthenticated`, and a caller with an unknown role (for example `cadet`), an inactive account, or the wrong role gets 403 `forbidden_role`. Either way nothing reaches the Vault. In the stub a missing or unknown `X-Role` is 403 `forbidden_role`. The Vault checks the role again on every write (defense in depth).

| Method and path | Roles | Vault call |
|---|---|---|
| GET /v0/health | any role | none |
| GET /v0/sessions | grader, oic, admin | (read, newest first) |
| POST /v0/sessions | grader, oic, admin | fitness.open_session |
| POST /v0/sessions/{session_id}/close | oic, admin | fitness.close_session |
| POST /v0/sessions/{session_id}/events | grader, oic | fitness.open_event (AFT) |
| POST /v0/events/{event_id}/components | grader, oic | fitness.record_component (points computed by the Gatekeeper) |
| POST /v0/events/{event_id}/submit | grader, oic | fitness.submit |
| GET /v0/scoring/standards | grader, oic, admin | none (read from the scoring data file) |
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
| POST /v0/attendance/marks | instructor | attendance.mark (own section only) |
| GET /v0/attendance/sections/{section_id}/roster?class_date= | instructor | attendance.roster (own section only) |
| GET /v0/results?test_type=&session_id= | grader, oic (all scores); a cadet token (own scores only) | read only: fitness.scorecard for each event |
| GET /v0/cadets | oic, admin | read only: every cadet with company, class year, latest AFT and IOCT summary, attendance counts |
| GET /v0/cadets/{cadet_id} | oic, admin | read only: one cadet's full record (details, every score, attendance). Writes one `CADET_RECORD_VIEWED` audit entry |
| GET /v0/me | any signed-in person | read only: a cadet's own record, or a staff member's name and role |
| POST /v0/auth/cac-signin | none (this is how you get a token) | app_user or cadet lookup by hashed ID, audit entry |
| POST /v0/identity/lookup | grader, oic | read only: the name the database holds for a scanned card |
| POST /v0/admin/register | admin | identity.add_user or identity.add_cadet (one audit entry) |
| GET /v0/admin/people | admin | read only: names and roles, no hashes |
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

`total_points` is the sum of the component points, or null if there are no components or any component has no points. Points are entered by the grader. Points are worked out by the Gatekeeper from the official AFT score tables (see 4.7), not typed.

### 4.1 Open a test session: POST /v0/sessions (grader, oic, admin)

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

A grader may start a test session, because each session holds one live score per cadet and a retake is simply a new session. Once a score is saved only the OIC can reopen or correct it (`reopen`, `supersede`). `oic_id` is the person who opened the session (a grader or the OIC).

`GET /v0/sessions` (grader, oic) lists every session, newest first, so a grading screen can use the latest one that is still open (`closed_at` is null):

```json
{
  "count": 2,
  "sessions": [
    {
      "session_id": 7,
      "test_type": "AFT",
      "session_date": "2026-10-22",
      "location": "Test Gym",
      "oic_id": 2,
      "opened_at": "2026-10-22T12:55:00.000Z",
      "closed_at": null
    },
    {
      "session_id": 6,
      "test_type": "IOCT",
      "session_date": "2026-10-20",
      "location": "Test Track",
      "oic_id": 2,
      "opened_at": "2026-10-20T12:00:00.000Z",
      "closed_at": "2026-10-20T15:00:00.000Z"
    }
  ]
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
  },
  "grading": {
    "total_points": null,
    "result": "INCOMPLETE",
    "failed_events": [],
    "min_points_per_event": 60,
    "standards_status": "confirmed"
  }
}
```

### 4.4 Enter a component: POST /v0/events/{event_id}/components (grader, oic)

Request body (v1): `component` (MDL, HRP, SDC, PLK, or 2MR), `raw_value` (string, as measured: whole pounds or repetitions, or a time as `m:ss`), `age_group` and `scoring_column` (section 4.7). Do not send `points`: the Gatekeeper works it out from the official score tables and the request is refused with 400 if it is present. Only while the event is ENTERED. Posting the same component again replaces it and re-grades it, and the Vault writes a COMPONENT_CHANGED audit entry that keeps the old value. Once submitted, components are locked.

```json
{
  "component": "MDL",
  "raw_value": "340",
  "age_group": "17-21",
  "scoring_column": "F"
}
```

Response 200:

```json
{
  "event_id": 11,
  "component": "MDL",
  "raw_value": "340",
  "points": 100,
  "age_group": "17-21",
  "scoring_column": "F",
  "standards_status": "confirmed"
}
```

### 4.5 Submit: POST /v0/events/{event_id}/submit (grader, oic)

No body. The scorecard must be complete (all five components, or a computed IOCT run), else 409 `invalid_transition`. The Vault supports an optional grader signature on submit, switched off for now (`REQUIRE_GRADER_SIGNATURE = False`), so there are no grader signature fields. For an AFT event the response also carries the `grading` block (total and pass or fail, section 4.7).

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
  },
  "grading": {
    "total_points": 500,
    "result": "PASS",
    "failed_events": [],
    "min_points_per_event": 60,
    "standards_status": "confirmed"
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

### 4.7 Scoring standards, total, and pass or fail

**How points are found.** For one event, the Gatekeeper picks the cadet's table by `age_group` and `scoring_column`, then takes the largest point row the result meets. For deadlift, push-ups, and plank (higher is better) that means the result is at least the row's number. For sprint-drag-carry and the 2-mile run (lower is better) it means the result is that fast or faster. A row printed as `---` in the official table is skipped, and a result that does not reach the 0 point row scores 0.

**Age group and column.** `age_group` is the printed age band. `scoring_column` is `M|C` or `F`, the two columns of the official table. v1 has the grader choose them once per cadet on the form and send them with each component. The Vault does not store them yet (open item 1). Edge may map an age in years to a band with the same rule as the table (17 to 21, 22 to 26, and so on, 62 and over is `62+`).

**Total and result.** `total_points` is the sum of the five event points, so 500 at most, and is null until all five are entered. `result` is `INCOMPLETE` until all five are entered, then `PASS` if every event is at least `min_points_per_event`, otherwise `FAIL` with the failing codes in `failed_events`. The minimum (60, so 300 is the lowest passing total) is the team's decision of 17-Nov, to be confirmed by the DPE. `GET /v0/scoring/standards` also returns `column_for_sex`: men use `M|C`, all women use `F`, which the form uses after it asks for sex first. `standards_status` is `confirmed` while the tables are not confirmed by the DPE.

`grading` as returned with a submitted AFT event (it sits next to `scorecard`, as shown in 4.5):

```json
{
  "total_points": 500,
  "result": "PASS",
  "failed_events": [],
  "min_points_per_event": 60,
  "standards_status": "confirmed"
}
```

**Read the standards: GET /v0/scoring/standards (grader, oic, admin).** No body. Returns what the form needs to build its choices and show the status label. Response 200:

```json
{
  "name": "Army Fitness Test Score Tables",
  "approved": "2025-05-15",
  "effective": "2025-06-01",
  "standards_status": "confirmed",
  "status_note": "Official published tables (AFT_Scoring_Scales_250601). Pass rule: at least 60 points on every event, so 300 is the lowest passing total. Column: men use M|C, all women use F. Confirmed by the team on 17-Nov-2026.",
  "age_groups": [
    "17-21",
    "22-26",
    "27-31",
    "32-36",
    "37-41",
    "42-46",
    "47-51",
    "52-56",
    "57-61",
    "62+"
  ],
  "scoring_columns": [
    "M|C",
    "F"
  ],
  "pass_rule": {
    "min_points_per_event": 60,
    "total_minimum": 300,
    "status": "confirmed 17-Nov: 60 points on every event (300 total is the lowest possible pass)"
  },
  "column_for_sex": {
    "male": "M|C",
    "female": "F",
    "status": "confirmed 17-Nov: men use M|C, all women use F"
  },
  "events": [
    {
      "component": "MDL",
      "unit": "lbs",
      "better": "higher"
    },
    {
      "component": "HRP",
      "unit": "reps",
      "better": "higher"
    },
    {
      "component": "SDC",
      "unit": "m:ss",
      "better": "lower"
    },
    {
      "component": "PLK",
      "unit": "m:ss",
      "better": "higher"
    },
    {
      "component": "2MR",
      "unit": "m:ss",
      "better": "lower"
    }
  ]
}
```

Errors for this section: a missing or unknown `age_group` or `scoring_column`, an out of range or badly formed `raw_value`, or a `points` field in the request is 400 `validation_failed` and the message names the field. Nothing is written.

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
  },
  "grading": {
    "elapsed_ms": 155000,
    "elapsed": "2:35.00",
    "pass_rule": "D (5 points) or better",
    "tab_rule": "A- (40 points) or better",
    "by_column": {
      "M|C": {
        "points": 40,
        "grade": "A-",
        "result": "PASS",
        "tab": true
      },
      "F": {
        "points": 50,
        "grade": "A+",
        "result": "PASS",
        "tab": true
      }
    },
    "standards_status": "dpe_grade_scale"
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

## 5A. IOCT scoring and corrections (L31, live)

A computed IOCT run now carries a `grading` block next to the scorecard, built from the DPE IOCT grade scale (data/ioct_standard.json, entered from the DPE slide, 17-Nov). It holds `elapsed_ms`, `elapsed` (m:ss.cc), `pass_rule`, `tab_rule`, `standards_status` and `by_column`, which gives `points`, `grade`, `result` (PASS or FAIL) and `tab` for `M|C` (men) and `F` (women). The Vault does not store gender, so the web app, which asks for it first, shows its column. Pass is a D (5 points) or better. A- (40 points) or better is a TAB and never has to be retaken. The slide prints whole seconds, so the gate time is cut down to the whole second before the lookup (to confirm with the DPE). The time is always the Vault's computed time from the gate reads.

Corrections never overwrite a saved score. `POST /v0/events/{event_id}/supersede` keeps the old event, marks it SUPERSEDED, and opens a new event whose `supersedes_event_id` points at the original, with its own audit entry. `POST /v0/events/{event_id}/reopen` sends a disputed or submitted event back to ENTERED. Both are live and OIC only, one Vault call each.

## 5B. Attendance (L32, live, secondary function)

An instructor marks a cadet in their own section. The cadet is identified by barcode (same rule as AFT: the Gatekeeper hashes it, the raw string is never stored or logged). `status` is optional and defaults to PRESENT.

POST /v0/attendance/marks, role instructor. Body: `section_id`, `class_date` (YYYY-MM-DD), then exactly one of `barcode` or `cadet_id`, plus optional `status` (PRESENT, LATE, ABSENT, EXCUSED) and `client_ref`.

- Scan (the normal way): send the `barcode` from the card. With no `status` it marks the cadet PRESENT, so a scan updates attendance with nothing else to do. The response carries `cadet_name` so the screen can say who was just marked.
- Correct a row (only if needed): send `cadet_id` from the roster read and the new `status`. This is the same write as a scan. If the cadet already has a different status for that class, the status changes and the audit entry (`ATTENDANCE_CORRECTED`) keeps the old one. Sending both fields, or neither, is 400 `validation_failed`.

```json
{
  "attendance_id": 31,
  "section_id": 1,
  "class_date": "2026-11-17",
  "status": "PRESENT",
  "cadet_name": "Alpha, Cadet"
}
```

GET /v0/attendance/sections/{section_id}/roster?class_date=YYYY-MM-DD, role instructor. Returns everyone enrolled with today's status (null means not yet marked).

```json
{
  "section_id": 1,
  "class_date": "2026-11-17",
  "roster": [
    {
      "cadet_id": 1,
      "last_name": "Alpha",
      "first_name": "Cadet",
      "status": "PRESENT"
    },
    {
      "cadet_id": 2,
      "last_name": "Bravo",
      "first_name": "Cadet",
      "status": null
    }
  ]
}
```

Refused writes (SR-2): a mark for a section that belongs to another instructor, or for a cadet who is not enrolled in that section, returns 403 `forbidden_role` and writes exactly 1 `ATTENDANCE_REJECTED` audit entry (section, date and reason only, no barcode, no ID, no cadet hash) and 0 attendance rows. A roster read for another instructor's section is refused and logged the same way. Every other role is refused at the Gatekeeper with 403 before anything reaches the Vault.

## 5C. Reading results by role (Demo 3, live, read only)

GET /v0/results, optional query `test_type` (AFT or IOCT) and `session_id`. Nothing is written.

- grader and oic: every score. `scope` is `all`.
- a cadet token tied to that cadet's hashed ID: only that cadet's own scores. `scope` is `own`. The caller never names a cadet, so there is no parameter a cadet could change to see someone else's. The filters only narrow within their own scores.
- a cadet credential that is not tied to a cadet record, or any other role (admin, instructor, compiler_station, gate_station, arms_nco): 403 `forbidden_role`.
- no token or a bad token: 401 `unauthenticated`.

Each result is the same view the write endpoints return (status, scorecard, and the `grading` block with the total and pass or fail for AFT, or the points and grade for IOCT). The cadet's ID hash is left out of every result. A cadet also sees events that are still ENTERED or SUPERSEDED, with their `status` shown, so the front end should label drafts. The stub has no cadet identity, so it only answers the staff roles.

```json
{
  "scope": "all",
  "count": 2,
  "results": [
    {
      "event_id": 11,
      "status": "SUBMITTED",
      "scorecard": {
        "event_id": 11,
        "test_type": "AFT",
        "session_id": 7,
        "session_date": "2026-10-22",
        "location": "Test Gym",
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
      },
      "grading": {
        "total_points": 500,
        "result": "PASS",
        "failed_events": [],
        "min_points_per_event": 60,
        "standards_status": "confirmed"
      }
    },
    {
      "event_id": 12,
      "status": "SUBMITTED",
      "scorecard": {
        "event_id": 12,
        "test_type": "IOCT",
        "session_id": 8,
        "session_date": "2026-10-22",
        "location": "Test Gym",
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
      },
      "grading": {
        "elapsed_ms": 155000,
        "elapsed": "2:35.00",
        "pass_rule": "D (5 points) or better",
        "tab_rule": "A- (40 points) or better",
        "by_column": {
          "M|C": {
            "points": 40,
            "grade": "A-",
            "result": "PASS",
            "tab": true
          },
          "F": {
            "points": 50,
            "grade": "A+",
            "result": "PASS",
            "tab": true
          }
        },
        "standards_status": "dpe_grade_scale"
      }
    }
  ]
}
```

## 5D. CAC sign-in, card lookup and registration (live)

Everything that needs to know who a card belongs to now works from a scan. The scanner page (Arjo's `edge/scanner`) reads the CAC and parses it on the device, then sends the 10 digit DoD ID, called `edipi`, over HTTPS. The Gatekeeper hashes it with the secret key at once. The page never holds that key, and the ID is never stored, logged or returned. A request may carry either `barcode` (the synthetic test pack format) or `edipi`, exactly one of them, on open event, ioct pair and attendance marks. Sending both or neither is 400 `validation_failed`.

Sign in: POST /v0/auth/cac-signin. Body `edipi` and `position` (`grader` or `cadet`, the choice the person made on screen). No token is needed. The database decides:
- position `grader`: the card must belong to an active person whose role is `grader` or `oic`.
- position `cadet`: the card must belong to an active cadet.
- anything else is 403 `forbidden_role` ("This card is not registered as ...") and is written to the audit log as `SIGN_IN_REFUSED` with the position and reason only, no ID and no hash. A good sign-in is written as `SIGNED_IN`.

The response carries a session token that expires (4 hours). Use it as `Authorization: Bearer <token>` like any other token. A grader or OIC session is tied to their staff record, so the role comes from the database as always. A cadet session is tied to that cadet's hashed ID, so it can read only their own results (section 5C) and every write is 403. The `name` is the name the database holds, so the screen can compare it to the name on the card.

```json
{
  "token": "example-session-token-shown-once",
  "role": "grader",
  "position": "grader",
  "name": "Basu, Example",
  "expires_in_s": 14400
}
```

Look up a card: POST /v0/identity/lookup, role grader or oic, body `edipi`. A grader scans a cadet's card and the screen shows the name the database holds, to compare with the card. `found` is false and `kind` is `unknown` for a card nobody registered.

```json
{
  "found": true,
  "kind": "cadet",
  "name": "Alpha, Avery",
  "role": "cadet"
}
```

Register a person: POST /v0/admin/register, role admin. Body `edipi`, `first_name`, `last_name`, `role` (`grader`, `oic`, `instructor` or `cadet`). A cadet goes in the cadet table, everyone else in the staff table. A second registration of the same card is 409 `invalid_transition`. Each registration is one audit entry.

```json
{
  "registered": true,
  "name": "Alpha, Avery",
  "role": "cadet"
}
```

List the people: GET /v0/admin/people, role admin. Names and roles only.

```json
{
  "count": 2,
  "people": [
    {
      "name": "Nebesky, Alena",
      "kind": "staff",
      "role": "oic",
      "active": true
    },
    {
      "name": "Alpha, Avery",
      "kind": "cadet",
      "role": "cadet",
      "active": true
    }
  ]
}
```

Limit, stated plainly: a scan identifies a person but does not prove it is them. Anyone holding a photo of a card barcode could present it. Real proof needs the card's chip and a reader, which is not built.


## 5E. Cadet records (live, read only)

The OIC looks a cadet up. A cadet looks up their own record. No ID or hash is ever returned.

`GET /v0/cadets` (oic, admin): `{count, cadets: [{cadet_id, name, company, class_year, active, aft, ioct, attendance}]}`. `aft` is the newest score that still counts (`status`, `total_points`, `result`) or null. `ioct` has `status`, `elapsed` and `grades` for both scoring columns, or null. `attendance` counts PRESENT, LATE, ABSENT and EXCUSED. `cadet_id` is the database's own number, not an identifier from the card.

`GET /v0/cadets/{cadet_id}` (oic, admin): `{cadet, results, sections, attendance: {summary, records}}`. `results` holds every score (the same shape as `GET /v0/results`, including drafts and replaced scores with their status). Each call writes one `CADET_RECORD_VIEWED` audit entry with the OIC's user and the cadet number, so a record of who read a cadet's information exists. 404 for an unknown cadet.

`GET /v0/me`: a cadet session returns that cadet's own record (`kind: "cadet"`, the same fields as above). It takes no cadet number, so a cadet has no way to ask for someone else's. A staff session returns `{kind: "staff", name, role}`. A cadet's own view is not audited.

A grader (not OIC) and a cadet get 403 on `/v0/cadets` and `/v0/cadets/{id}`. No token is 401.

Example `GET /v0/cadets`:

```json
{
  "count": 2,
  "cadets": [
    {
      "cadet_id": 1,
      "name": "Alpha, Avery",
      "company": "E-2",
      "class_year": 2028,
      "active": true,
      "aft": {
        "status": "SUBMITTED",
        "total_points": 499,
        "result": "PASS"
      },
      "ioct": {
        "status": "SUBMITTED",
        "elapsed": "2:35.00",
        "grades": {
          "M|C": "A-",
          "F": "A+"
        }
      },
      "attendance": {
        "PRESENT": 0,
        "LATE": 0,
        "ABSENT": 0,
        "EXCUSED": 0
      }
    },
    {
      "cadet_id": 2,
      "name": "Bravo, Blake",
      "company": "F-1",
      "class_year": 2027,
      "active": true,
      "aft": null,
      "ioct": null,
      "attendance": {
        "PRESENT": 0,
        "LATE": 0,
        "ABSENT": 0,
        "EXCUSED": 0
      }
    }
  ]
}
```

Example `GET /v0/cadets/1`:

```json
{
  "cadet": {
    "cadet_id": 1,
    "name": "Alpha, Avery",
    "company": "E-2",
    "class_year": 2028,
    "active": true
  },
  "results": [
    {
      "event_id": 11,
      "status": "SUBMITTED",
      "scorecard": {
        "event_id": 11,
        "test_type": "AFT",
        "session_id": 7,
        "session_date": "2026-10-22",
        "location": "Test Gym",
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
      },
      "grading": {
        "total_points": 500,
        "result": "PASS",
        "failed_events": [],
        "min_points_per_event": 60,
        "standards_status": "confirmed"
      }
    }
  ],
  "sections": [
    {
      "course": "XE401",
      "section": "G1",
      "period": "B hour"
    }
  ],
  "attendance": {
    "summary": {
      "PRESENT": 1,
      "LATE": 0,
      "ABSENT": 0,
      "EXCUSED": 0
    },
    "records": [
      {
        "course": "XE401",
        "section": "G1",
        "class_date": "2026-11-17",
        "status": "PRESENT"
      }
    ]
  }
}
```

Example `GET /v0/me` for a cadet:

```json
{
  "cadet": {
    "cadet_id": 1,
    "name": "Alpha, Avery",
    "company": "E-2",
    "class_year": 2028,
    "active": true
  },
  "results": [
    {
      "event_id": 11,
      "status": "SUBMITTED",
      "scorecard": {
        "event_id": 11,
        "test_type": "AFT",
        "session_id": 7,
        "session_date": "2026-10-22",
        "location": "Test Gym",
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
      },
      "grading": {
        "total_points": 500,
        "result": "PASS",
        "failed_events": [],
        "min_points_per_event": 60,
        "standards_status": "confirmed"
      }
    }
  ],
  "sections": [
    {
      "course": "XE401",
      "section": "G1",
      "period": "B hour"
    }
  ],
  "attendance": {
    "summary": {
      "PRESENT": 1,
      "LATE": 0,
      "ABSENT": 0,
      "EXCUSED": 0
    },
    "records": [
      {
        "course": "XE401",
        "section": "G1",
        "class_date": "2026-11-17",
        "status": "PRESENT"
      }
    ]
  },
  "kind": "cadet"
}
```

Registration (section 5D) now accepts two optional fields for a cadet: `company` (1 to 20 letters, digits, spaces or dashes, for example "E-2") and `class_year` (an integer such as 2027). Nothing else about a cadet is stored; age and sex stay out of the Vault on purpose.

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
| 400 | unsupported_contract_version | Header missing or not `v0` or `v1` |
| 400 | challenge_invalid | Challenge expired, already used, or for another event (ChallengeError) |
| 400 | signature_invalid | Signature does not verify, or the scorecard changed since it was shown (SignatureInvalid) |
| 401 | unauthenticated | Live endpoints: no token, a malformed Authorization header, or an unknown token. The message does not say which |
| 403 | forbidden_role | Role unknown, inactive, or not allowed, including every cadet write (PermissionDenied) |
| 404 | not_found | No such session, event, cadet, or wristband (NotFound) |
| 405 | method_not_allowed | Any PUT, PATCH, or DELETE |
| 409 | invalid_transition | Wrong status or session type, closed session, or the cadet already has a live score (InvalidTransition) |
| 422 | timing_error | Missing START or END read, or a time outside the allowed range (TimingError) |
| 500 | internal_error | A forced failure writes 0 records and 0 audit entries |

A write that is refused by the signing checks still writes one rejection entry to the audit log.

Stub only: send `X-Stub-Scenario: not_found`, `invalid_transition`, `forbidden_role`, `challenge_invalid`, `signature_invalid`, `timing_error`, or `internal_error` on a request that passes the role and body checks, and the stub returns that error. Use it to build error screens.

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
| component, raw_value, points | fitness_component.component, raw_value, points (points are computed by the Gatekeeper in v1) |
| age_group, scoring_column | not stored in v1 (open item 1) |
| grading (total_points, result) | not stored in v1. Worked out from fitness_component.points. Arjo's schema v1 card may store them |
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
| aft_results.mdl_lbs | component MDL, `raw_value` as a string, for example "340". The pack has no age group or column, so tests use `17-21` and `M\|C` |
| aft_results.hrp_reps | component HRP |
| aft_results.sdc_time | component SDC, for example "1:30" |
| aft_results.plank_time | component PLK, for example "3:40" |
| aft_results.twomr_time | component 2MR, for example "13:15" |
| ioct_time | not an input. It is the expected output of compute: START read at t and END read at t plus 155000 ms gives "2:35" |
| section | attendance, not in v0 |
| fake_name | cadet enrollment (`identity.add_cadet`), not an API call in v0 |
| contract_version "1.0" | this contract uses `v0` in the path and header. One label should win |

## 11. Not in v1

Arms room, receipts, export to AMS, offline buffering and resync, user and cadet enrollment, CAC key enrollment, RFID tag registration, and the optional grader signature. The Vault has most of these. They get endpoints in later sprints.

## 12. Open items for the team

1. Scoring (v1 position, needs the team's yes): the system computes points from the official tables (done in this version). Settled 17-Nov: the AFT pass rule is 60 points on every event, men use the M|C column and all women use F, and the IOCT grade scale (pass at D, TAB at A- or better) is entered from the DPE slide. Still open: where each cadet's age group comes from (the grader chooses it for now) and a typed manual IOCT time.
1a. For Arjo (schema v1, L29): should the Vault store `age_group` and `scoring_column` on the event, and `total_points` and `result`? Storing them once at open event would stop a grader sending a different context on each component and would put the context in the audit entry. v1 sends them on each component so the Gatekeeper needs no schema change today.
1b. For Rhemmy (form, L29): the form selects age group and column once per cadet, sends them with every component, shows `grading.total_points` and `grading.result` after submit, shows the `confirmed` label, and has an error screen for 400 naming the field.
1c. For Rhemmy (results view, Demo 3): call GET /v0/results with the signed-in person's token. Show every row to a grader or OIC. Show a cadet only what the server returns (it is already limited to their own). Do not filter on the page and do not send a cadet id.
1d. Sign-in by CAC scan is built (section 5D). Open for the team: it identifies but does not authenticate (see the limit in 5D), and the Vault gives a person one role, so no one can be both grader and instructor yet (Arjo).
2. Role mechanism: done for the six live writes in 2.1 (bearer token, role read from the Vault). Still open: how staff get a token (CAC sign in), and moving the canned endpoints to the same check as they go live.
3. Rhemmy's L24 card says Edge parses the barcode and hashes the ID. The README and the Vault say Gatekeeper does, with a key Edge must never hold. The card text should change to match this contract.
4. Time format: settled as `m:ss` (the official tables write `1:29`). The Vault schema comment that shows "01:45" should be updated to match.
5. Gate clocks: elapsed time uses only gate timestamps, so the START and END gates must share a clock or be synced. Needs Eliakim's timing spec.
6. Who may read audit and verify endpoints (section 2).
7. Encryption at rest (SR-4) is not built in the Vault yet, and real CAC signing is untested. Both are noted in the Vault README.
8. Version label: the pack says "1.0", Trello says v0.
