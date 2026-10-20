# MUSTER API Contract v0

Status: DRAFT for team acknowledgement (L22, Fri 16-Oct-2026)
Owner: Alena (Gatekeeper). Consumers: Rhemmy (Edge), Arjo (Vault), Eliakim (timing gate).
Traces to: SR-1, SR-3, SR-4. Card: PBI-02 L22 Draft API contract v0.

Rule: any change to a field name or endpoint is posted on the contract card, the version is bumped, and the other owners acknowledge it before it is merged.

## 1. Conventions

- JSON, UTF-8. Field names are snake_case. Timestamps are ISO 8601 UTC with milliseconds, for example `2026-10-16T14:05:09.120Z`.
- Every request carries `X-Contract-Version: v0`. Gatekeeper rejects any other value with 400 `unsupported_contract_version`.
- Base path: `/v0`.
- The API is the only writer to the Vault. Edge never talks to the Vault, and the timing gate never talks to the Vault.
- The client never sends a score, a record ID, a sequence number, or a hash of a record. Gatekeeper computes all of them. Edge is untrusted, so anything it claims is verified.
- There is no update endpoint and no delete endpoint for any resource (SR-3, append only). A correction is a new record with `supersedes_record_id` set.
- Cadets are identified only by `cadet_id_hash`. A raw DoD ID or raw barcode string never appears in any request, response, log line, or column.
- Stub note: in the L23 stub API the role is read from the `X-Role` header so the other zones can build immediately. This is a stub only. The real mechanism is an open item (section 8).

## 2. Roles and who may call what

Roles: `grader`, `cadre`, `cadet`, `timing_gate`.

| Endpoint | grader | cadre | cadet | timing_gate |
|---|---|---|---|---|
| POST /v0/fitness-records | yes | no | no | no |
| GET /v0/fitness-records/{record_id} | yes | yes | own record only | no |
| GET /v0/cadets/{cadet_id_hash}/fitness-records | yes | yes | own only | no |
| POST /v0/ioct-timings | no | no | no | yes |
| GET /v0/audit/events | no | yes | no | no |
| GET /v0/audit/verify | no | yes | no | no |
| GET /v0/health | yes | yes | yes | yes |

A caller with a missing role, an unknown role, or the wrong role gets 403 and nothing reaches the Vault. The 403 is itself written to the audit log as `request_rejected` (see section 6).

## 3. Fitness record

### POST /v0/fitness-records (role: grader)

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| idempotency_key | string (uuid) | yes | Same key twice returns the original response with 200, never a second record |
| cadet_id_hash | string, 64 hex | yes | Hashed ID, see open item 4 |
| test_type | string | yes | `AFT` or `IOCT_MANUAL` |
| test_date | string, date | yes | `YYYY-MM-DD` |
| measurements | object | yes | Raw results, shape depends on `test_type` (below) |
| supersedes_record_id | string (uuid) | no | Set only to correct an earlier record |

`measurements` for `AFT` (all required):

| Field | Type | Unit |
|---|---|---|
| deadlift_lbs | integer | pounds |
| hand_release_pushups_reps | integer | repetitions |
| sprint_drag_carry_seconds | number | seconds |
| plank_seconds | integer | seconds |
| two_mile_run_seconds | integer | seconds |

`measurements` for `IOCT_MANUAL`: `{ "elapsed_seconds": number }`. Gate-timed IOCT results use `/v0/ioct-timings` instead.

Example request:

```json
{
  "idempotency_key": "6f1c2b0e-3d44-4a8e-9b7a-2c1d5e8f9a10",
  "cadet_id_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "test_type": "AFT",
  "test_date": "2026-10-16",
  "measurements": {
    "deadlift_lbs": 210,
    "hand_release_pushups_reps": 34,
    "sprint_drag_carry_seconds": 105.4,
    "plank_seconds": 150,
    "two_mile_run_seconds": 1030
  }
}
```

Response 201 (the stored record, with server-computed score and audit link):

| Field | Type | Notes |
|---|---|---|
| record_id | string (uuid) | Assigned by Gatekeeper |
| cadet_id_hash | string | Echo |
| grader_id_hash | string | Hashed ID of the grader from the session, never from the request body |
| test_type | string | Echo |
| test_date | string | Echo |
| measurements | object | Echo |
| score | object | See below |
| supersedes_record_id | string or null | Echo |
| created_at | string, timestamp | Server time |
| audit_seq | integer | Sequence number of the audit entry written in the same transaction |

`score` object: `{ "event_points": { "<measurement_field>": integer, ... }, "total_points": integer, "pass": boolean, "table_version": string }`. For `IOCT_MANUAL`, `event_points` is empty, `total_points` is null, and `pass` compares against the IOCT standard.

Example response:

```json
{
  "record_id": "b2d1a9c4-0f6e-4e35-8f02-77a5c3e10d21",
  "cadet_id_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "grader_id_hash": "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
  "test_type": "AFT",
  "test_date": "2026-10-16",
  "measurements": {
    "deadlift_lbs": 210,
    "hand_release_pushups_reps": 34,
    "sprint_drag_carry_seconds": 105.4,
    "plank_seconds": 150,
    "two_mile_run_seconds": 1030
  },
  "score": {
    "event_points": {
      "deadlift_lbs": 70,
      "hand_release_pushups_reps": 72,
      "sprint_drag_carry_seconds": 68,
      "plank_seconds": 75,
      "two_mile_run_seconds": 66
    },
    "total_points": 351,
    "pass": true,
    "table_version": "PLACEHOLDER-v0"
  },
  "supersedes_record_id": null,
  "created_at": "2026-10-16T14:05:09.120Z",
  "audit_seq": 42
}
```

The numbers in `score` above are invented. Real tables are blocked on the DPE scoring RFI. Until it is answered, `table_version` is `PLACEHOLDER-v0` and every score is labeled as a placeholder in code and in the data pack.

### GET /v0/fitness-records/{record_id}

Returns the same object as the 201 response above. 404 `record_not_found` if it does not exist or the caller may not see it (a cadet asking for someone else's record gets 404, not 403, so existence is not leaked).

### GET /v0/cadets/{cadet_id_hash}/fitness-records

Returns `{ "cadet_id_hash": "...", "records": [ <record>, ... ] }`, newest first. Superseded records are included and each carries its `supersedes_record_id`.

## 4. IOCT timing result from the gate

### POST /v0/ioct-timings (role: timing_gate)

| Field | Type | Required | Notes |
|---|---|---|---|
| idempotency_key | string (uuid) | yes | Retries must not create a second record |
| tag_id | string | yes | RFID tag the cadet wore |
| cadet_id_hash | string, 64 hex | yes | Gatekeeper checks the tag to cadet assignment is known, else 400 `unknown_tag` |
| start_timestamp | string, timestamp | yes | First valid start gate read |
| finish_timestamp | string, timestamp | yes | First valid finish gate read, must be after start |
| gate_id | string | yes | Which gate unit reported |
| raw_read_count | integer | no | Reads seen before de-duplication, for diagnostics |

Gatekeeper computes `elapsed_seconds` itself from the two timestamps and ignores any value the gate might send.

Example request:

```json
{
  "idempotency_key": "0c9e7a52-5b1d-4f0a-8a47-1d3b6e2f7c88",
  "tag_id": "TAG-0007",
  "cadet_id_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "start_timestamp": "2026-10-22T13:00:02.310Z",
  "finish_timestamp": "2026-10-22T13:03:41.980Z",
  "gate_id": "GATE-A",
  "raw_read_count": 4
}
```

Response 201: a fitness record with `test_type` `IOCT_GATE`, `measurements` `{ "elapsed_seconds": 219.67, "tag_id": "TAG-0007", "gate_id": "GATE-A" }`, a `score` object as in section 3, and `audit_seq`. `grader_id_hash` is null for gate records and `reported_by` is `timing_gate`.

## 5. Errors

Every error uses one shape, and the `message` is safe to show on the Edge error screen (PBI-05 needs the reason for a rejection):

```json
{
  "error": {
    "code": "forbidden_role",
    "message": "Role 'cadet' may not create fitness records.",
    "request_id": "req_01HV7Z3KQ8"
  }
}
```

| HTTP | code | When |
|---|---|---|
| 400 | validation_failed | Missing field, wrong type, out of range, or malformed hash. Message names the field |
| 400 | unsupported_contract_version | Header missing or not `v0` |
| 400 | unknown_tag | Tag has no known cadet |
| 401 | unauthenticated | No session or credential |
| 403 | forbidden_role | Role missing, unknown, or not allowed for this endpoint |
| 403 | cadet_not_gradeable | Target cadet may not be graded |
| 404 | record_not_found | No such record, or caller may not see it |
| 405 | method_not_allowed | Any attempt to PUT, PATCH, or DELETE a record or audit entry |
| 409 | supersede_conflict | `supersedes_record_id` does not exist or is already superseded |
| 500 | internal_error | A forced failure writes 0 records and 0 audit entries |

A rejected request never writes a fitness record. It does write one `request_rejected` audit entry.

## 6. Audit event

Every write, and every rejection, produces exactly one audit entry, written in the same database transaction as the record (no record exists without its entry, and no entry exists for a record that was not stored).

Fields:

| Field | Type | Notes |
|---|---|---|
| seq | integer | Starts at 1, increases by exactly 1 |
| event_id | string (uuid) | |
| timestamp | string, timestamp | Server time |
| actor_role | string | `grader`, `cadre`, `cadet`, `timing_gate`, or `unknown` |
| actor_id_hash | string or null | Null when the caller could not be identified |
| action | string | `record_created`, `record_superseded`, `timing_received`, `request_rejected` |
| target_type | string | `fitness_record` or `request` |
| target_id | string | `record_id`, or `request_id` for a rejection |
| payload_hash | string, 64 hex | SHA-256 of the canonical JSON of the stored record or the rejected request metadata |
| prev_hash | string, 64 hex | `entry_hash` of seq minus 1. For seq 1, 64 zeros |
| entry_hash | string, 64 hex | See below |

`entry_hash` is the SHA-256, as lowercase hex, of this string, with the pipe character as the separator and fields in this exact order:

`seq|timestamp|actor_role|actor_id_hash|action|target_type|target_id|payload_hash|prev_hash`

A null `actor_id_hash` is written as the empty string. The Vault exposes an append function and no update or delete function.

Example:

```json
{
  "seq": 42,
  "event_id": "e7a1d3c0-92b4-4c1a-bb3e-5d8f60a14c2b",
  "timestamp": "2026-10-16T14:05:09.120Z",
  "actor_role": "grader",
  "actor_id_hash": "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
  "action": "record_created",
  "target_type": "fitness_record",
  "target_id": "b2d1a9c4-0f6e-4e35-8f02-77a5c3e10d21",
  "payload_hash": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
  "prev_hash": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
  "entry_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92"
}
```

### GET /v0/audit/events (role: cadre)

Query: `from_seq` (default 1), `limit` (default 100, max 500). Returns `{ "events": [ <audit event>, ... ], "next_seq": integer or null }`.

### GET /v0/audit/verify (role: cadre)

Recomputes the whole chain. Returns `{ "result": "PASS", "entries_checked": 42 }` on an untouched log. On a break, returns `{ "result": "FAIL", "first_broken_seq": 17, "reason": "entry_hash_mismatch" }`. Reasons: `entry_hash_mismatch` (edited entry), `prev_hash_mismatch` or `sequence_gap` (deleted entry).

## 7. Field to Vault column map

PBI-02 requires 0 mismatches between contract field names and Vault column names. Rule: the Vault column is named exactly like the contract field.

| Table | Columns |
|---|---|
| cadets | cadet_id_hash (primary key), created_at |
| fitness_records | record_id, cadet_id_hash, grader_id_hash, test_type, test_date, measurements (JSON text), score (JSON text), supersedes_record_id, created_at, audit_seq |
| audit_entries | seq, event_id, timestamp, actor_role, actor_id_hash, action, target_type, target_id, payload_hash, prev_hash, entry_hash |

No column anywhere holds a raw DoD ID or a raw barcode string.

## 8. Open items for the team (decide before v1)

1. Scoring tables and the IOCT pass standard: RFI to DPE, owner Alena, due 22-Oct. Until answered, `table_version` is `PLACEHOLDER-v0`.
2. IOCT timing accuracy and gate positions: RFI to DPE, owner Eliakim, due 20-Oct. The timestamp format and `gate_id` here may change after it.
3. Role mechanism: v0 stub trusts `X-Role`. The real mechanism (session or CAC derived, issued by Gatekeeper) is not decided. Edge must never be the source of the role.
4. How `cadet_id_hash` is computed: a plain SHA-256 of a 10 digit DoD ID can be reversed by brute force in seconds. A keyed hash (HMAC with a secret held by Gatekeeper) is stronger, but then Gatekeeper must see the raw ID once over TLS and hash it there, which differs from the current plan of hashing in Edge. Needs a team decision with Rhemmy and Arjo.
5. Whether SR-1 and SR-3 wording changes from the 8-Oct focus update are approved (the L21 requirements card). This contract already covers fitness records in the audit log.
6. Attendance and arms room endpoints are out of scope for v0.
