"""IOCT timing with RFID wristbands.

Like a race-day timing chip: the chip only says "band #17 crossed the mat at
10:02:13.40". It doesn't know who's wearing it. So:

  1. pair()            A grader, watching, scans the cadet's CAC barcode and the
                       wristband. "Band #17 = Cadet Basu, this run."
  2. record_gate_read() Each gate sends RAW reads only (tag, gate, its timestamp).
                       Stored as evidence even if they match nobody.
  3. compute_run()     The Vault works out the time itself:
                         end   = first END read after pairing
                         start = last START read before that end
                       (A runner standing at the start gate gets read many times;
                       the last read is when they actually left.)
  4. fitness.submit / request_cadet_challenge / accept — same as AFT.
     The band is released when the cadet signs.

Clock note: gate timestamps come from the gate's clock. Elapsed time only uses
START and END reads from the gates, so both gates must share a clock (best: one
controller timestamps both) or be synced. See GATE_CLOCK_TOLERANCE_MS.
"""
from __future__ import annotations

import sqlite3

from . import audit, clock, fitness, identity
from .db import one, transaction
from .errors import InvalidTransition, NotFound, TimingError

# Reads up to this long BEFORE the Vault recorded the pairing still count
# (covers small differences between the gate clock and the Vault clock).
GATE_CLOCK_TOLERANCE_MS = 2_000

# After a run finishes, the band keeps getting read while the cadet stands near
# the END gate. Reads this soon after a previous run's finish are ignored.
REREAD_WINDOW_MS = 10_000

# Sanity limits. Anything outside these needs an OIC to look at it.
# TODO(DPE): confirm realistic bounds for the IOCT.
MIN_ELAPSED_MS = 60_000          # 1:00
MAX_ELAPSED_MS = 15 * 60_000     # 15:00


def register_tag(conn: sqlite3.Connection, actor_id: int, tag_uid: str,
                 label: str | None = None) -> int:
    with transaction(conn):
        identity.require_role(conn, actor_id, {"admin", "oic"})
        cur = conn.execute("INSERT INTO rfid_tag(tag_uid, label) VALUES (?, ?)",
                           (tag_uid, label))
        audit.append(conn, function="IOCT", action="TAG_REGISTERED", actor_id=actor_id,
                     entity_type="rfid_tag", entity_id=cur.lastrowid,
                     data={"tag_uid": tag_uid, "label": label})
        return cur.lastrowid


def pair(conn: sqlite3.Connection, actor_id: int, session_id: int, cadet_edipi_hash: str,
         tag_uid: str, client_ref: str | None = None) -> int:
    """Hand a wristband to a cadet for one run. Returns the new IOCT fitness_event id."""
    if client_ref:
        existing = one(conn, "SELECT id FROM fitness_event WHERE client_ref = ?", (client_ref,))
        if existing:
            return existing["id"]
    with transaction(conn):
        identity.require_role(conn, actor_id, fitness.GRADER_ROLES)
        session = fitness._session(conn, session_id, must_be_open=True)
        if session["test_type"] != "IOCT":
            raise InvalidTransition("this is not an IOCT session")
        tag = one(conn, "SELECT * FROM rfid_tag WHERE tag_uid = ? AND active = 1", (tag_uid,))
        if tag is None:
            raise NotFound(f"wristband {tag_uid!r} is not registered")
        busy = one(conn, "SELECT event_id FROM tag_assignment "
                         "WHERE tag_id = ? AND released_at IS NULL", (tag["id"],))
        if busy:
            raise InvalidTransition(
                f"wristband {tag_uid!r} is still assigned (event {busy['event_id']})")
        cadet = identity.cadet_by_hash(conn, cadet_edipi_hash)
        event_id = fitness._create_event(conn, actor_id, session_id, cadet["id"], "IOCT",
                                         client_ref)
        now = clock.now_ms()
        cur = conn.execute(
            "INSERT INTO tag_assignment(tag_id, event_id, paired_by, paired_at, paired_at_ms) "
            "VALUES (?, ?, ?, ?, ?)", (tag["id"], event_id, actor_id, clock.iso(now), now))
        audit.append(conn, function="IOCT", action="TAG_PAIRED", actor_id=actor_id,
                     entity_type="fitness_event", entity_id=event_id,
                     data={"assignment_id": cur.lastrowid, "tag_uid": tag_uid,
                           "cadet_id": cadet["id"]})
        return event_id


def record_gate_read(conn: sqlite3.Connection, actor_id: int, session_id: int,
                     tag_uid: str, gate: str, read_at_ms: int, gate_device: str,
                     client_ref: str | None = None) -> int:
    """Store one raw gate read. Idempotent on client_ref (gate retries are safe)."""
    if gate not in ("START", "END"):
        raise ValueError("gate must be 'START' or 'END'")
    if client_ref:
        existing = one(conn, "SELECT id FROM gate_read WHERE client_ref = ?", (client_ref,))
        if existing:
            return existing["id"]
    with transaction(conn):
        identity.require_role(conn, actor_id, {"gate_station", "oic"})
        fitness._session(conn, session_id)
        cur = conn.execute(
            "INSERT INTO gate_read(session_id, tag_uid, gate, read_at_ms, gate_device, "
            "received_at, recorded_by, client_ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, tag_uid, gate, int(read_at_ms), gate_device, clock.now_iso(),
             actor_id, client_ref))
        audit.append(conn, function="IOCT", action="GATE_READ", actor_id=actor_id,
                     entity_type="gate_read", entity_id=cur.lastrowid,
                     data={"session_id": session_id, "tag_uid": tag_uid, "gate": gate,
                           "read_at_ms": int(read_at_ms), "gate_device": gate_device})
        return cur.lastrowid


def active_event_for_tag(conn: sqlite3.Connection, session_id: int, tag_uid: str) -> int:
    """Score compiler: cadet taps their band -> which run is this?"""
    row = one(conn,
              "SELECT a.event_id FROM tag_assignment a "
              "JOIN rfid_tag t ON t.id = a.tag_id "
              "JOIN fitness_event e ON e.id = a.event_id "
              "WHERE t.tag_uid = ? AND e.session_id = ? AND a.released_at IS NULL",
              (tag_uid, session_id))
    if row is None:
        raise NotFound(f"wristband {tag_uid!r} is not assigned in this session")
    return row["event_id"]


def compute_run(conn: sqlite3.Connection, actor_id: int, event_id: int) -> int:
    """Work out the run time from raw gate reads. Returns elapsed milliseconds."""
    with transaction(conn):
        identity.require_role(conn, actor_id, fitness.GRADER_ROLES | {"compiler_station"})
        ev = fitness.get_event(conn, event_id)
        if ev["test_type"] != "IOCT":
            raise InvalidTransition("not an IOCT event")
        if ev["status"] != "ENTERED":
            raise InvalidTransition(f"run is {ev['status']}; reopen it to recompute")
        if one(conn, "SELECT 1 FROM ioct_run WHERE event_id = ?", (event_id,)):
            raise InvalidTransition("run time already computed")
        asg = one(conn, "SELECT a.*, t.tag_uid FROM tag_assignment a "
                        "JOIN rfid_tag t ON t.id = a.tag_id WHERE a.event_id = ?", (event_id,))
        if asg is None:
            raise NotFound("no wristband paired with this run")
        floor = asg["paired_at_ms"] - GATE_CLOCK_TOLERANCE_MS
        # If this band was used earlier in the session, never reuse that cadet's reads
        # (matters when a gate clock runs ahead of the Vault clock). Repeat reads right
        # after the previous finish are that cadet lingering at the gate, so skip them too.
        prior = one(conn,
                    "SELECT MAX(g.read_at_ms) AS last_end FROM ioct_run r "
                    "JOIN gate_read g ON g.id = r.end_read_id "
                    "WHERE g.session_id = ? AND g.tag_uid = ? AND r.event_id <> ?",
                    (ev["session_id"], asg["tag_uid"], event_id))
        if prior and prior["last_end"] is not None:
            floor = max(floor, prior["last_end"] + REREAD_WINDOW_MS)

        end = one(conn,
                  "SELECT * FROM gate_read WHERE session_id = ? AND tag_uid = ? "
                  "AND gate = 'END' AND read_at_ms >= ? ORDER BY read_at_ms, id LIMIT 1",
                  (ev["session_id"], asg["tag_uid"], floor))
        if end is None:
            raise TimingError("no END gate read for this wristband since pairing")
        start = one(conn,
                    "SELECT * FROM gate_read WHERE session_id = ? AND tag_uid = ? "
                    "AND gate = 'START' AND read_at_ms >= ? AND read_at_ms < ? "
                    "ORDER BY read_at_ms DESC, id DESC LIMIT 1",
                    (ev["session_id"], asg["tag_uid"], floor, end["read_at_ms"]))
        if start is None:
            raise TimingError("no START gate read before the END read")
        elapsed = end["read_at_ms"] - start["read_at_ms"]
        if not (MIN_ELAPSED_MS <= elapsed <= MAX_ELAPSED_MS):
            raise TimingError(
                f"time {fitness.format_elapsed(elapsed)} is outside the allowed range; "
                "OIC must review the gate reads")
        conn.execute(
            "INSERT INTO ioct_run(event_id, assignment_id, start_read_id, end_read_id, "
            "elapsed_ms, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, asg["id"], start["id"], end["id"], elapsed, clock.now_iso()))
        audit.append(conn, function="IOCT", action="RUN_COMPUTED", actor_id=actor_id,
                     entity_type="fitness_event", entity_id=event_id,
                     data={"start_read_id": start["id"], "end_read_id": end["id"],
                           "elapsed_ms": elapsed})
        return elapsed


def release_tag(conn: sqlite3.Connection, actor_id: int, event_id: int, reason: str) -> None:
    """OIC frees a wristband manually (cadet dropped out, band swapped, ...)."""
    with transaction(conn):
        identity.require_role(conn, actor_id, fitness.OIC_ROLES)
        cur = conn.execute("UPDATE tag_assignment SET released_at = ? "
                           "WHERE event_id = ? AND released_at IS NULL",
                           (clock.now_iso(), event_id))
        if cur.rowcount == 0:
            raise NotFound("no active wristband on this run")
        audit.append(conn, function="IOCT", action="TAG_RELEASED", actor_id=actor_id,
                     entity_type="fitness_event", entity_id=event_id,
                     data={"reason": reason})


def unmatched_reads(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """Gate reads from wristbands nobody was paired to — worth an OIC's look."""
    return [dict(r) for r in conn.execute(
        "SELECT g.* FROM gate_read g WHERE g.session_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM tag_assignment a JOIN rfid_tag t ON t.id = a.tag_id "
        "  JOIN fitness_event e ON e.id = a.event_id "
        "  WHERE t.tag_uid = g.tag_uid AND e.session_id = g.session_id) "
        "ORDER BY g.read_at_ms", (session_id,))]
