"""Tamper-evident audit log (SR-3).

Think of it as a chain of sealed envelopes. Each envelope holds one event plus
the seal (fingerprint) of the envelope before it, and gets its own seal over
both. Change anything in envelope #40 and its seal no longer matches — and
#41 points at a seal that no longer exists. The verifier walks the chain and
names the first envelope that doesn't check out.

What this does and doesn't catch (stated honestly — MVP scope):
  * Catches: editing or deleting any past entry, reordering entries, inserting
    a fake entry in the middle.
  * Doesn't catch on its own: someone with direct file access who edits an entry
    AND recomputes every seal after it, or who chops entries off the END.
    Countermeasure in this MVP: the cadet's receipt carries the seq + hash of their
    entry, and `head()` can be printed/exported, so an outside copy exists to compare
    against. Signed checkpoints are the named future upgrade.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import clock
from .canonical import canonical_bytes, canonical_text, sha256_hex

GENESIS_HASH = "0" * 64

FUNCTIONS = {"CORE", "FITNESS", "IOCT", "ATTENDANCE", "ARMS"}


def compute_entry_hash(
    seq: int,
    ts: str,
    function: str,
    action: str,
    actor_id: int | None,
    entity_type: str,
    entity_id: int | None,
    data: str,
    prev_hash: str,
) -> str:
    return sha256_hex(
        canonical_bytes(
            {
                "seq": seq,
                "ts": ts,
                "function": function,
                "action": action,
                "actor_id": actor_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "data": data,
                "prev_hash": prev_hash,
            }
        )
    )


def append(
    conn: sqlite3.Connection,
    *,
    function: str,
    action: str,
    actor_id: int | None,
    entity_type: str,
    entity_id: int | None,
    data: dict,
) -> sqlite3.Row:
    """Add one entry to the chain. Must be called inside db.transaction()."""
    if not conn.in_transaction:
        raise RuntimeError("audit.append must run inside db.transaction()")
    if function not in FUNCTIONS:
        raise ValueError(f"unknown audit function {function!r}")

    last = conn.execute(
        "SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = 1 if last is None else last["seq"] + 1
    prev_hash = GENESIS_HASH if last is None else last["entry_hash"]
    ts = clock.now_iso()
    data_text = canonical_text(data)
    entry_hash = compute_entry_hash(
        seq, ts, function, action, actor_id, entity_type, entity_id, data_text, prev_hash
    )
    conn.execute(
        """INSERT INTO audit_log
           (seq, ts, function, action, actor_id, entity_type, entity_id, data,
            prev_hash, entry_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (seq, ts, function, action, actor_id, entity_type, entity_id, data_text,
         prev_hash, entry_hash),
    )
    return conn.execute("SELECT * FROM audit_log WHERE seq = ?", (seq,)).fetchone()


@dataclass
class VerifyResult:
    ok: bool
    entries_checked: int
    first_bad_seq: int | None = None
    reason: str | None = None
    head_hash: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"PASS — {self.entries_checked} entries, head {self.head_hash}"
        return f"FAIL at seq {self.first_bad_seq}: {self.reason}"


def verify(conn: sqlite3.Connection) -> VerifyResult:
    """Walk the whole chain. Returns PASS, or the first seq that is broken."""
    expected_prev = GENESIS_HASH
    expected_seq = 1
    count = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
        if row["seq"] != expected_seq:
            return VerifyResult(False, count, expected_seq,
                                f"entry missing (found seq {row['seq']} next)")
        if row["prev_hash"] != expected_prev:
            return VerifyResult(False, count, row["seq"],
                                "prev_hash does not match the entry before it")
        recomputed = compute_entry_hash(
            row["seq"], row["ts"], row["function"], row["action"], row["actor_id"],
            row["entity_type"], row["entity_id"], row["data"], row["prev_hash"],
        )
        if recomputed != row["entry_hash"]:
            return VerifyResult(False, count, row["seq"],
                                "contents were changed after the entry was written")
        expected_prev = row["entry_hash"]
        expected_seq += 1
        count += 1
    return VerifyResult(True, count, head_hash=expected_prev)


def head(conn: sqlite3.Connection) -> dict:
    """Latest seq + hash. Print or export this as an outside anchor."""
    row = conn.execute(
        "SELECT seq, entry_hash, ts FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {"seq": 0, "entry_hash": GENESIS_HASH, "ts": None}
    return dict(row)


def check_anchor(conn: sqlite3.Connection, seq: int, entry_hash: str) -> bool:
    """Does the log still contain this exact entry? (Used with a cadet's receipt.)"""
    row = conn.execute("SELECT entry_hash FROM audit_log WHERE seq = ?", (seq,)).fetchone()
    return row is not None and row["entry_hash"] == entry_hash


def entries_for(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> list[dict]:
    """History of one record, oldest first — the on-demand report for SR-3."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = ? AND entity_id = ? ORDER BY seq",
            (entity_type, entity_id),
        )
    ]
