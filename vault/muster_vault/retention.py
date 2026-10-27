"""Retention setting: how long each kind of record is kept (SR-4, Privacy Act).

Plain version: every record has an age. Each function (FITNESS, IOCT, ATTENDANCE,
ARMS) has a number of days it must be kept. This module stores that number, lets
an admin change it (the change itself goes in the audit log), and reports which
records are now older than their retention period.

PLACEHOLDER: until the retention RFI ("Retention period for fitness and attendance
records under USMA records policy") is answered, every function uses
PLACEHOLDER_DAYS. `report()` flags that clearly, so the placeholder can't be
mistaken for policy.

WHAT THIS DOES NOT DO YET (on purpose): delete anything. Two open questions
decide how disposal must work, so it waits for the RFI answer:
  1. Policy: is an expired record destroyed, or transferred (e.g. to AMS) first?
  2. Design: audit_log entries carry full scorecards, and the log is append-only
     and hash-chained. Deleting a score row would leave the score in the log, and
     deleting log entries would break the chain. See docs/design/encryption-and-retention.md
     for the options (the leading one: chain over a hash of each entry's data, so
     the data can be erased later while the chain still verifies).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import audit, clock
from .db import one, transaction
from .identity import require_role

PLACEHOLDER_DAYS = 1825  # PLACEHOLDER (5 years) — NOT POLICY. Replace when the RFI is answered.
PLACEHOLDER_SOURCE = "PLACEHOLDER - pending retention RFI (USMA records policy)"

MIN_DAYS, MAX_DAYS = 1, 36500

# Which table and timestamp column holds each function's records.
_RECORDS = {
    "FITNESS": ("fitness_event", "created_at", "test_type = 'AFT'"),
    "IOCT": ("fitness_event", "created_at", "test_type = 'IOCT'"),
    "ATTENDANCE": ("attendance", "recorded_at", "1 = 1"),
    "ARMS": ("si_event", "occurred_at", "1 = 1"),
}
FUNCTIONS = tuple(_RECORDS)


def _check_function(function: str) -> str:
    if function not in _RECORDS:
        raise ValueError(f"unknown function {function!r} (one of {', '.join(FUNCTIONS)})")
    return function


def get(conn, function: str) -> dict:
    """The retention period in force for one function."""
    row = one(conn, "SELECT * FROM retention_setting WHERE function = ?",
              (_check_function(function),))
    if row is None:
        return {"function": function, "days": PLACEHOLDER_DAYS, "source": PLACEHOLDER_SOURCE,
                "placeholder": True, "set_by": None, "set_at": None}
    return {"function": function, "days": row["days"], "source": row["source"],
            "placeholder": False, "set_by": row["set_by"], "set_at": row["set_at"]}


def settings(conn) -> list[dict]:
    return [get(conn, f) for f in FUNCTIONS]


def set_days(conn, actor_id: int, function: str, days: int, source: str) -> dict:
    """Admin only. `source` says where the number came from (e.g. the policy paragraph)."""
    _check_function(function)
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"days must be a whole number from {MIN_DAYS} to {MAX_DAYS}")
    source = (source or "").strip()
    if not source:
        raise ValueError("source is required: cite where the retention period comes from")
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        before = get(conn, function)
        now = clock.now_iso()
        conn.execute(
            "INSERT INTO retention_setting(function, days, source, set_by, set_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(function) DO UPDATE SET "
            "days = excluded.days, source = excluded.source, "
            "set_by = excluded.set_by, set_at = excluded.set_at",
            (function, days, source, actor_id, now))
        audit.append(conn, function="CORE", action="RETENTION_SET", actor_id=actor_id,
                     entity_type="retention_setting", entity_id=None,
                     data={"function": function, "old_days": before["days"],
                           "old_source": before["source"], "new_days": days,
                           "new_source": source})
    return get(conn, function)


def cutoff_iso(days: int, now_ms: int | None = None) -> str:
    """Records stamped earlier than this are past retention."""
    now = datetime.fromtimestamp((clock.now_ms() if now_ms is None else now_ms) / 1000,
                                 tz=timezone.utc)
    return clock.iso(int((now - timedelta(days=days)).timestamp() * 1000))


def due(conn, function: str, now_ms: int | None = None) -> list[dict]:
    """Records of one function that are older than its retention period, oldest first."""
    table, column, where = _RECORDS[_check_function(function)]
    cutoff = cutoff_iso(get(conn, function)["days"], now_ms)
    # table/column/where come from the fixed _RECORDS map above, never from input.
    rows = conn.execute(
        f"SELECT id, {column} AS stamped_at FROM {table} "
        f"WHERE {where} AND {column} < ? ORDER BY {column}, id", (cutoff,))
    return [{"function": function, "entity_type": table, "entity_id": r["id"],
             "stamped_at": r["stamped_at"]} for r in rows]


def report(conn, now_ms: int | None = None) -> list[dict]:
    """One line per function: the setting in force and how many records are past it."""
    out = []
    for s in settings(conn):
        expired = due(conn, s["function"], now_ms)
        out.append({**s, "past_retention": len(expired),
                    "oldest_past_retention": expired[0]["stamped_at"] if expired else None})
    return out
