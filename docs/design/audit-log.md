# Audit Log Design (SR-3)

## The idea
A chain of sealed envelopes. Each entry holds:
`seq, ts, function, action, actor_id, entity_type, entity_id, data, prev_hash`
and its `entry_hash = SHA-256(canonical JSON of all of the above)`.

Because each entry includes the previous entry's hash, changing or deleting any
entry breaks the chain from that point on. `audit.verify()` walks it and returns
PASS or the first broken `seq`.

## Two layers of protection
1. **Lock (prevention, weak):** SQLite triggers refuse UPDATE/DELETE on `audit_log`.
   Anyone with the database file can drop them — that's expected.
2. **Seal (detection, strong):** the hash chain. You can't stop an edit, but you will see it.

Signed scores get a **third** check: `fitness.verify_event()` rebuilds the scorecard
from the tables and compares it to the bytes the cadet signed. An edit to a score made
straight in SQL is caught even if the attacker never touches the audit log.

## Writing rules
- Every Vault write goes through `db.transaction()` (`BEGIN IMMEDIATE`), so the record
  and its audit entry are written together, and two writers can't fork the chain.
- Corrections are **appended** (`COMPONENT_CHANGED`, `EVENT_REOPENED`,
  `EVENT_SUPERSEDED`), never edits to old entries — like an accounting ledger.
- Failed signature attempts are logged as `SIGNATURE_REJECTED`.

## Honest scope
- **Detected:** editing, deleting, reordering, or inserting entries.
- **Not detected by the chain alone:** an admin who edits an entry *and* recomputes
  every hash after it, or who removes entries from the end.
  - Partial cover today: each cadet's receipt carries the `seq` + `entry_hash` of their
    signing entry; `audit.check_anchor()` will fail if the chain was rewritten.
    `audit.head()` can be printed or exported regularly as another outside copy.
  - Future upgrade: signed checkpoints (periodically sign the head hash with a key the
    database admin doesn't hold).
