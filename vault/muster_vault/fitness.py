"""Fitness scores — AFT and IOCT (primary stakeholder: DPE).

A score moves between desks like a paper scorecard, and every move is logged:

    ENTERED ──submit──▶ SUBMITTED ──cadet signs──▶ SIGNED ──export──▶ EXPORTED
       ▲                  │    ▲                     │
       │               dispute │ (OIC reopens)       │ supersede (correction)
       │                  ▼    │                     ▼
       └──── reopen ─── DISPUTED                  SUPERSEDED  + a new ENTERED event

Rules that keep it honest:
  * Components can only be changed while ENTERED. Once submitted they're locked.
  * The cadet signs the exact scorecard (canonical bytes). If anything changed
    between showing it and signing it, the signature is refused.
  * After SIGNED nothing is edited. A correction creates a NEW event that points
    at the old one; the old one stays in the record as SUPERSEDED.
  * `verify_event` rebuilds the scorecard from the tables and compares it to what
    the cadet actually signed — so an edit made directly in the database (going
    around this code) is caught even if the audit log were untouched.
"""
from __future__ import annotations

import json
import sqlite3

from . import attestation, audit, clock, identity
from .canonical import canonical_bytes
from .db import IntegrityError, one, transaction
from .errors import ChallengeError, InvalidTransition, NotFound, SignatureInvalid, VaultError

# AFT events (Army Fitness Test, replaced the ACFT in 2025).
# TODO(DPE / LTC Inman): confirm codes, names, and the scoring table source.
AFT_COMPONENTS: dict[str, str] = {
    "MDL": "3-Repetition Maximum Deadlift",
    "HRP": "Hand-Release Push-Up",
    "SDC": "Sprint-Drag-Carry",
    "PLK": "Plank",
    "2MR": "2-Mile Run",
}

# Team decision point: should the grader also sign with their CAC when submitting?
# Two signatures = "grader says X" AND "cadet agrees X". Off by default for the MVP.
REQUIRE_GRADER_SIGNATURE = False

GRADER_ROLES = {"grader", "oic"}
STATION_ROLES = {"compiler_station", "oic"}
OIC_ROLES = {"oic", "admin"}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def open_session(conn: sqlite3.Connection, actor_id: int, test_type: str,
                 session_date: str, location: str) -> int:
    if test_type not in ("AFT", "IOCT"):
        raise ValueError("test_type must be 'AFT' or 'IOCT'")
    with transaction(conn):
        identity.require_role(conn, actor_id, OIC_ROLES)
        cur = conn.execute(
            "INSERT INTO test_session(test_type, session_date, location, oic_id, opened_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (test_type, session_date, location, actor_id, clock.now_iso()),
        )
        audit.append(conn, function="FITNESS", action="SESSION_OPENED", actor_id=actor_id,
                     entity_type="test_session", entity_id=cur.lastrowid,
                     data={"test_type": test_type, "session_date": session_date,
                           "location": location})
        return cur.lastrowid


def close_session(conn: sqlite3.Connection, actor_id: int, session_id: int) -> None:
    with transaction(conn):
        identity.require_role(conn, actor_id, OIC_ROLES)
        _session(conn, session_id, must_be_open=True)
        conn.execute("UPDATE test_session SET closed_at = ? WHERE id = ?",
                     (clock.now_iso(), session_id))
        audit.append(conn, function="FITNESS", action="SESSION_CLOSED", actor_id=actor_id,
                     entity_type="test_session", entity_id=session_id, data={})


def _session(conn, session_id: int, must_be_open: bool = False) -> sqlite3.Row:
    row = one(conn, "SELECT * FROM test_session WHERE id = ?", (session_id,))
    if row is None:
        raise NotFound(f"test session {session_id} not found")
    if must_be_open and row["closed_at"]:
        raise InvalidTransition("test session is closed")
    return row


# ---------------------------------------------------------------------------
# Events (one cadet, one test)
# ---------------------------------------------------------------------------

def get_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    row = one(conn, "SELECT * FROM fitness_event WHERE id = ?", (event_id,))
    if row is None:
        raise NotFound(f"fitness event {event_id} not found")
    return row


def _function(test_type: str) -> str:
    return "IOCT" if test_type == "IOCT" else "FITNESS"


def _create_event(conn, actor_id: int, session_id: int, cadet_id: int, test_type: str,
                  client_ref: str | None, supersedes: int | None = None) -> int:
    """Insert a new ENTERED event. Caller holds the transaction and checked roles."""
    try:
        cur = conn.execute(
            "INSERT INTO fitness_event(session_id, cadet_id, test_type, status, grader_id, "
            "supersedes_event_id, client_ref, created_at) "
            "VALUES (?, ?, ?, 'ENTERED', ?, ?, ?, ?)",
            (session_id, cadet_id, test_type, actor_id, supersedes, client_ref,
             clock.now_iso()),
        )
    except IntegrityError as exc:
        if "ux_fitness_event_live" in str(exc) or "fitness_event.session_id" in str(exc):
            raise InvalidTransition(
                "this cadet already has a live score in this session") from exc
        raise
    audit.append(conn, function=_function(test_type), action="EVENT_OPENED",
                 actor_id=actor_id, entity_type="fitness_event", entity_id=cur.lastrowid,
                 data={"session_id": session_id, "cadet_id": cadet_id,
                       "test_type": test_type, "supersedes_event_id": supersedes,
                       "client_ref": client_ref})
    return cur.lastrowid


def open_event(conn: sqlite3.Connection, actor_id: int, session_id: int,
               cadet_edipi_hash: str, client_ref: str | None = None) -> int:
    """Grader scanned a cadet's CAC barcode: start their AFT scorecard.

    `client_ref` is a unique ID the Edge app makes up; sending the same one twice
    (e.g. a retry after a network drop) returns the same event instead of a duplicate.
    """
    if client_ref:
        existing = one(conn, "SELECT id FROM fitness_event WHERE client_ref = ?", (client_ref,))
        if existing:
            return existing["id"]
    with transaction(conn):
        identity.require_role(conn, actor_id, GRADER_ROLES)
        session = _session(conn, session_id, must_be_open=True)
        if session["test_type"] != "AFT":
            raise InvalidTransition("IOCT events are opened by pairing a wristband (ioct.pair)")
        cadet = identity.cadet_by_hash(conn, cadet_edipi_hash)
        return _create_event(conn, actor_id, session_id, cadet["id"], "AFT", client_ref)


def record_component(conn: sqlite3.Connection, actor_id: int, event_id: int,
                     component: str, raw_value: str, points: int | None = None) -> None:
    """Enter (or, while still ENTERED, re-enter) one AFT event result."""
    if component not in AFT_COMPONENTS:
        raise ValueError(f"unknown AFT component {component!r}; "
                         f"expected one of {sorted(AFT_COMPONENTS)}")
    if points is not None and not (0 <= int(points) <= 100):
        raise ValueError("points must be between 0 and 100")
    with transaction(conn):
        identity.require_role(conn, actor_id, GRADER_ROLES)
        ev = get_event(conn, event_id)
        if ev["test_type"] != "AFT":
            raise InvalidTransition("components are for AFT events only")
        if ev["status"] != "ENTERED":
            raise InvalidTransition(f"scorecard is {ev['status']}; it can't be changed now")
        old = one(conn, "SELECT raw_value, points FROM fitness_component "
                        "WHERE event_id = ? AND component = ?", (event_id, component))
        now = clock.now_iso()
        if old is None:
            conn.execute(
                "INSERT INTO fitness_component(event_id, component, raw_value, points, "
                "entered_by, entered_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, component, str(raw_value), points, actor_id, now),
            )
            action, data = "COMPONENT_ENTERED", {}
        else:
            conn.execute(
                "UPDATE fitness_component SET raw_value = ?, points = ?, entered_by = ?, "
                "entered_at = ? WHERE event_id = ? AND component = ?",
                (str(raw_value), points, actor_id, now, event_id, component),
            )
            action = "COMPONENT_CHANGED"
            data = {"old_raw_value": old["raw_value"], "old_points": old["points"]}
        data.update({"component": component, "raw_value": str(raw_value), "points": points})
        audit.append(conn, function="FITNESS", action=action, actor_id=actor_id,
                     entity_type="fitness_event", entity_id=event_id, data=data)


# ---------------------------------------------------------------------------
# The scorecard — the exact record that gets signed
# ---------------------------------------------------------------------------

def scorecard(conn: sqlite3.Connection, event_id: int) -> dict:
    """Build the canonical scorecard from the tables. Deterministic: same data, same dict."""
    ev = get_event(conn, event_id)
    session = _session(conn, ev["session_id"])
    cadet = identity.get_cadet(conn, ev["cadet_id"])
    card = {
        "event_id": ev["id"],
        "test_type": ev["test_type"],
        "session_id": session["id"],
        "session_date": session["session_date"],
        "location": session["location"],
        "cadet_edipi_hash": cadet["edipi_hash"],
        "cadet_name": f"{cadet['last_name']}, {cadet['first_name']}",
        "grader_id": ev["grader_id"],
        "supersedes_event_id": ev["supersedes_event_id"],
    }
    if ev["test_type"] == "AFT":
        comps = [
            {"component": r["component"], "raw_value": r["raw_value"], "points": r["points"]}
            for r in conn.execute(
                "SELECT component, raw_value, points FROM fitness_component "
                "WHERE event_id = ? ORDER BY component", (event_id,))
        ]
        card["components"] = comps
        pts = [c["points"] for c in comps]
        card["total_points"] = (sum(pts) if comps and all(p is not None for p in pts)
                                else None)
    else:
        run = one(conn,
                  "SELECT r.elapsed_ms, s.read_at_ms AS start_ms, e.read_at_ms AS end_ms, "
                  "s.tag_uid FROM ioct_run r "
                  "JOIN gate_read s ON s.id = r.start_read_id "
                  "JOIN gate_read e ON e.id = r.end_read_id WHERE r.event_id = ?",
                  (event_id,))
        card["ioct"] = None if run is None else {
            "tag_uid": run["tag_uid"], "start_read_at_ms": run["start_ms"],
            "end_read_at_ms": run["end_ms"], "elapsed_ms": run["elapsed_ms"],
            "elapsed": format_elapsed(run["elapsed_ms"]),
        }
    return card


def format_elapsed(ms: int) -> str:
    minutes, rem = divmod(ms, 60_000)
    return f"{minutes}:{rem // 1000:02d}.{rem % 1000 // 10:02d}"


def _check_complete(conn, ev: sqlite3.Row) -> None:
    if ev["test_type"] == "AFT":
        have = {r["component"] for r in conn.execute(
            "SELECT component FROM fitness_component WHERE event_id = ?", (ev["id"],))}
        missing = sorted(set(AFT_COMPONENTS) - have)
        if missing:
            raise InvalidTransition(f"scorecard incomplete; missing {missing}")
    else:
        if one(conn, "SELECT 1 FROM ioct_run WHERE event_id = ?", (ev["id"],)) is None:
            raise InvalidTransition("no IOCT run time computed yet (ioct.compute_run)")


# ---------------------------------------------------------------------------
# Grader submits
# ---------------------------------------------------------------------------

def request_grader_challenge(conn: sqlite3.Connection, actor_id: int,
                             event_id: int) -> tuple[int, bytes]:
    """Optional: bytes for the grader to sign when submitting."""
    with transaction(conn):
        grader = identity.require_role(conn, actor_id, GRADER_ROLES)
        ev = get_event(conn, event_id)
        if ev["status"] != "ENTERED":
            raise InvalidTransition(f"scorecard is {ev['status']}, not ENTERED")
        if not grader["edipi_hash"]:
            raise VaultError("grader account has no CAC on file")
        _check_complete(conn, ev)
        return attestation.issue_challenge(
            conn, purpose="GRADER_SUBMIT", entity_type="fitness_event", entity_id=event_id,
            record=scorecard(conn, event_id))


def submit(conn: sqlite3.Connection, actor_id: int, event_id: int,
           grader_challenge_id: int | None = None,
           grader_signature: bytes | None = None) -> None:
    """Grader sends the finished scorecard to the score compiler."""
    try:
        with transaction(conn):
            grader = identity.require_role(conn, actor_id, GRADER_ROLES)
            ev = get_event(conn, event_id)
            if ev["status"] != "ENTERED":
                raise InvalidTransition(f"scorecard is {ev['status']}, not ENTERED")
            _check_complete(conn, ev)
            att_id = None
            if grader_signature is not None:
                att_id = attestation.consume(
                    conn, challenge_id=grader_challenge_id, purpose="GRADER_SUBMIT",
                    entity_type="fitness_event", entity_id=event_id,
                    current_record=scorecard(conn, event_id),
                    signer_edipi_hash=grader["edipi_hash"], signer_role="grader",
                    signature=grader_signature)
            elif REQUIRE_GRADER_SIGNATURE:
                raise SignatureInvalid("policy requires the grader to sign on submit")
            now = clock.now_iso()
            conn.execute(
                "UPDATE fitness_event SET status = 'SUBMITTED', submitted_at = ?, "
                "grader_attestation_id = ? WHERE id = ?", (now, att_id, event_id))
            audit.append(conn, function=_function(ev["test_type"]), action="EVENT_SUBMITTED",
                         actor_id=actor_id, entity_type="fitness_event", entity_id=event_id,
                         data={"scorecard": scorecard(conn, event_id),
                               "grader_attestation_id": att_id})
    except (SignatureInvalid, ChallengeError) as exc:
        attestation.log_rejection(conn, function="FITNESS", actor_id=actor_id,
                                  entity_type="fitness_event", entity_id=event_id,
                                  purpose="GRADER_SUBMIT", reason=str(exc))
        raise


# ---------------------------------------------------------------------------
# Cadet signs (or disputes) at the score compiler
# ---------------------------------------------------------------------------

def request_cadet_challenge(conn: sqlite3.Connection, actor_id: int,
                            event_id: int) -> tuple[int, bytes]:
    """Compiler station: get the exact bytes the cadet will sign. Show the scorecard too."""
    with transaction(conn):
        identity.require_role(conn, actor_id, STATION_ROLES)
        ev = get_event(conn, event_id)
        if ev["status"] != "SUBMITTED":
            raise InvalidTransition(f"scorecard is {ev['status']}, not SUBMITTED")
        return attestation.issue_challenge(
            conn, purpose="FITNESS_ACCEPT", entity_type="fitness_event", entity_id=event_id,
            record=scorecard(conn, event_id))


def accept(conn: sqlite3.Connection, actor_id: int, event_id: int, challenge_id: int,
           signature: bytes) -> int:
    """Cadet signed their scorecard with their CAC. Returns the audit seq of the signing."""
    try:
        with transaction(conn):
            identity.require_role(conn, actor_id, STATION_ROLES)
            ev = get_event(conn, event_id)
            if ev["status"] != "SUBMITTED":
                raise InvalidTransition(f"scorecard is {ev['status']}, not SUBMITTED")
            cadet = identity.get_cadet(conn, ev["cadet_id"])
            card = scorecard(conn, event_id)
            att_id = attestation.consume(
                conn, challenge_id=challenge_id, purpose="FITNESS_ACCEPT",
                entity_type="fitness_event", entity_id=event_id, current_record=card,
                signer_edipi_hash=cadet["edipi_hash"], signer_role="cadet",
                signature=signature)
            conn.execute(
                "UPDATE fitness_event SET status = 'SIGNED', signed_at = ?, "
                "cadet_attestation_id = ? WHERE id = ?", (clock.now_iso(), att_id, event_id))
            if ev["test_type"] == "IOCT":
                conn.execute("UPDATE tag_assignment SET released_at = ? "
                             "WHERE event_id = ? AND released_at IS NULL",
                             (clock.now_iso(), event_id))
            att = one(conn, "SELECT payload_sha256 FROM attestation WHERE id = ?", (att_id,))
            entry = audit.append(
                conn, function=_function(ev["test_type"]), action="EVENT_SIGNED",
                actor_id=actor_id, entity_type="fitness_event", entity_id=event_id,
                data={"attestation_id": att_id, "payload_sha256": att["payload_sha256"]})
            return entry["seq"]
    except (SignatureInvalid, ChallengeError) as exc:
        attestation.log_rejection(conn, function="FITNESS", actor_id=actor_id,
                                  entity_type="fitness_event", entity_id=event_id,
                                  purpose="FITNESS_ACCEPT", reason=str(exc))
        raise


def dispute(conn: sqlite3.Connection, actor_id: int, event_id: int, reason: str) -> None:
    """Cadet won't sign — record it. Nothing gets silently dropped."""
    if not reason.strip():
        raise ValueError("a dispute needs a reason")
    with transaction(conn):
        identity.require_role(conn, actor_id, STATION_ROLES)
        ev = get_event(conn, event_id)
        if ev["status"] != "SUBMITTED":
            raise InvalidTransition(f"scorecard is {ev['status']}, not SUBMITTED")
        conn.execute("UPDATE fitness_event SET status = 'DISPUTED', dispute_reason = ? "
                     "WHERE id = ?", (reason, event_id))
        audit.append(conn, function=_function(ev["test_type"]), action="EVENT_DISPUTED",
                     actor_id=actor_id, entity_type="fitness_event", entity_id=event_id,
                     data={"reason": reason, "scorecard": scorecard(conn, event_id)})


def reopen(conn: sqlite3.Connection, actor_id: int, event_id: int, note: str) -> None:
    """OIC sends a disputed (or wrongly submitted) scorecard back for correction."""
    with transaction(conn):
        identity.require_role(conn, actor_id, OIC_ROLES)
        ev = get_event(conn, event_id)
        if ev["status"] not in ("DISPUTED", "SUBMITTED"):
            raise InvalidTransition(f"can't reopen a {ev['status']} scorecard; use supersede")
        conn.execute("UPDATE fitness_event SET status = 'ENTERED', submitted_at = NULL, "
                     "grader_attestation_id = NULL WHERE id = ?", (event_id,))
        if ev["test_type"] == "IOCT":
            conn.execute("DELETE FROM ioct_run WHERE event_id = ?", (event_id,))
        audit.append(conn, function=_function(ev["test_type"]), action="EVENT_REOPENED",
                     actor_id=actor_id, entity_type="fitness_event", entity_id=event_id,
                     data={"from_status": ev["status"], "note": note})


def supersede(conn: sqlite3.Connection, actor_id: int, event_id: int, reason: str) -> int:
    """Correct an already-signed score: keep the old one, open a new one that replaces it."""
    if not reason.strip():
        raise ValueError("a correction needs a reason")
    with transaction(conn):
        identity.require_role(conn, actor_id, OIC_ROLES)
        ev = get_event(conn, event_id)
        if ev["status"] not in ("SIGNED", "EXPORTED"):
            raise InvalidTransition(f"only SIGNED/EXPORTED scores are superseded "
                                    f"(this one is {ev['status']}; use reopen)")
        conn.execute("UPDATE fitness_event SET status = 'SUPERSEDED' WHERE id = ?",
                     (event_id,))
        audit.append(conn, function=_function(ev["test_type"]), action="EVENT_SUPERSEDED",
                     actor_id=actor_id, entity_type="fitness_event", entity_id=event_id,
                     data={"reason": reason, "was_status": ev["status"]})
        new_id = _create_event(conn, actor_id, ev["session_id"], ev["cadet_id"],
                               ev["test_type"], None, supersedes=event_id)
        if ev["test_type"] == "AFT":  # copy results so the OIC only fixes what's wrong
            for r in conn.execute("SELECT * FROM fitness_component WHERE event_id = ?",
                                  (event_id,)).fetchall():
                conn.execute(
                    "INSERT INTO fitness_component(event_id, component, raw_value, points, "
                    "entered_by, entered_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id, r["component"], r["raw_value"], r["points"], actor_id,
                     clock.now_iso()))
        return new_id


# ---------------------------------------------------------------------------
# Integrity check for one score
# ---------------------------------------------------------------------------

def verify_event(conn: sqlite3.Connection, event_id: int) -> dict:
    """Does the score in the tables still match what the cadet signed?"""
    ev = get_event(conn, event_id)
    problems: list[str] = []
    if ev["cadet_attestation_id"] is None:
        return {"event_id": event_id, "status": ev["status"], "signed": False,
                "ok": ev["status"] not in ("SIGNED", "EXPORTED"),
                "problems": [] if ev["status"] not in ("SIGNED", "EXPORTED")
                else ["marked signed but no cadet signature on file"]}
    att = one(conn, "SELECT payload FROM attestation WHERE id = ?",
              (ev["cadet_attestation_id"],))
    if not attestation.reverify(conn, ev["cadet_attestation_id"]):
        problems.append("cadet signature no longer verifies")
    signed_record = json.loads(bytes(att["payload"]))["record"]
    if canonical_bytes(signed_record) != canonical_bytes(scorecard(conn, event_id)):
        problems.append("scorecard in the database differs from what the cadet signed")
    if ev["grader_attestation_id"] is not None and not attestation.reverify(
            conn, ev["grader_attestation_id"]):
        problems.append("grader signature no longer verifies")
    return {"event_id": event_id, "status": ev["status"], "signed": True,
            "ok": not problems, "problems": problems}
