"""Gatekeeper -> Vault bridge (PBI-04 L24, SR-3).

This is the only place the Gatekeeper touches the Vault. It turns a request into
Vault calls and turns Vault errors into contract error codes.

The "one record + one audit entry, together or not at all" rule is enforced by the
Vault, not here: every Vault write (fitness.open_event, fitness.record_component,
fitness.submit) runs inside one `db.transaction()` that inserts the record AND calls
`audit.append`. If either step fails, SQLite rolls the whole transaction back. The
Gatekeeper must therefore call exactly one Vault write function per POST and never
wrap two of them in its own transaction. tests/test_live_vault.py proves it, including
a real database failure on the audit insert.

Raw barcodes and raw EDIPIs live only inside parse_barcode and hash_barcode. They are
never stored, logged, or returned.
"""

import re
import sys
from pathlib import Path

try:
    import muster_vault  # noqa: F401
except ImportError:  # running from the repo: the Vault package sits in <repo>/vault
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vault"))
    import muster_vault  # noqa: F401

import scoring
from muster_vault import attendance, audit, db, fitness, identity, ioct
from muster_vault.errors import (ChallengeError, InvalidTransition, NotFound,
                                 PermissionDenied, SignatureInvalid, TimingError, VaultError)


# Test pack format only. The real CAC PDF417 parser is not built (contract section 11).
_SYNTHETIC_BARCODE = re.compile(r"^PDF417_TEST_DATA_(\d{10})_END$")


class BadBarcode(ValueError):
    """The barcode string is not one we can read. Never carries the string itself."""


def parse_barcode(raw):
    m = _SYNTHETIC_BARCODE.match((raw or "").strip())
    if not m:
        raise BadBarcode("barcode is not in a format the Gatekeeper can read")
    return m.group(1)


def hash_barcode(raw):
    """Raw barcode in, keyed EDIPI hash out. The raw strings go out of scope here."""
    edipi = parse_barcode(raw)
    return identity.hash_edipi(edipi, identity.load_edipi_key())


# ---- connection -------------------------------------------------------------

def connect(db_path):
    """Open the Vault database through the Vault's own helper (SQLCipher-encrypted,
    manual transactions, rows as mappings, foreign keys on, schema in place).
    The database key comes from the MUSTER_DB_KEY environment variable."""
    return db.connect(db_path)


def init_schema(conn):
    db.init_schema(conn)


def role_of(conn, actor_id):
    """The caller's role AS THE VAULT HAS IT, or None if the user is missing or inactive.
    The role is never taken from the request."""
    row = conn.execute("SELECT role, active FROM app_user WHERE id = ?", (actor_id,)).fetchone()
    return row["role"] if row is not None and row["active"] else None


# ---- error mapping ----------------------------------------------------------

def map_error(exc):
    """Vault exception -> (http status, contract code, message). Order matters:
    specific errors first, VaultError (the base) last."""
    if isinstance(exc, BadBarcode):
        return 400, "validation_failed", "Field 'barcode': " + str(exc) + "."
    if isinstance(exc, PermissionDenied):
        return 403, "forbidden_role", str(exc)
    if isinstance(exc, NotFound):
        return 404, "not_found", str(exc)
    if isinstance(exc, InvalidTransition):
        return 409, "invalid_transition", str(exc)
    if isinstance(exc, ChallengeError):
        return 400, "challenge_invalid", str(exc)
    if isinstance(exc, SignatureInvalid):
        return 400, "signature_invalid", str(exc)
    if isinstance(exc, TimingError):
        return 422, "timing_error", str(exc)
    if isinstance(exc, ValueError):
        return 400, "validation_failed", str(exc)
    if isinstance(exc, VaultError):
        return 500, "internal_error", "Internal error. Nothing was written."
    return 500, "internal_error", "Internal error. Nothing was written."


# ---- the three live writes ---------------------------------------------------
# Each makes exactly ONE Vault call. Each Vault call is one transaction that writes
# the record and its audit entry together.

def _view(conn, event_id):
    ev = fitness.get_event(conn, event_id)
    card = fitness.scorecard(conn, event_id)
    view = {"event_id": event_id, "status": ev["status"], "scorecard": card}
    if card["test_type"] == "AFT":
        # Contract v1: total and pass or fail, worked out from the points the Vault holds.
        view["grading"] = scoring.summarize(
            {c["component"]: c["points"] for c in card["components"]})
    elif card.get("ioct"):
        # IOCT: the Vault's computed time, graded with the DPE grade scale.
        view["grading"] = scoring.ioct_grade(card["ioct"]["elapsed_ms"])
    return view


def open_event(conn, actor_id, session_id, barcode, client_ref=None):
    edipi_hash = hash_barcode(barcode)
    event_id = fitness.open_event(conn, actor_id, session_id, edipi_hash, client_ref)
    return _view(conn, event_id)


def add_component(conn, actor_id, event_id, component, raw_value, age_group, scoring_column):
    """Contract v1: the grader sends the raw result and the scoring context. The points come
    from the official tables (scoring.py), never from the client. One Vault call."""
    points, raw_text = scoring.points(component, raw_value, age_group, scoring_column)
    fitness.record_component(conn, actor_id, event_id, component, raw_text, points)
    return {"event_id": event_id, "component": component, "raw_value": raw_text,
            "points": points, "age_group": age_group, "scoring_column": scoring_column,
            "standards_status": scoring.STANDARDS_STATUS}


def submit(conn, actor_id, event_id):
    fitness.submit(conn, actor_id, event_id)
    return _view(conn, event_id)


# ---- IOCT (timing) writes ----------------------------------------------------
# Same rule: one Vault call per POST, and the Vault writes the record and its audit entry
# in one transaction.

def ioct_pair(conn, actor_id, session_id, barcode, tag_uid, client_ref=None):
    edipi_hash = hash_barcode(barcode)
    event_id = ioct.pair(conn, actor_id, session_id, edipi_hash, tag_uid, client_ref)
    return _view(conn, event_id)


def gate_read(conn, actor_id, session_id, tag_uid, gate, read_at_ms, gate_device,
              client_ref=None):
    read_id = ioct.record_gate_read(conn, actor_id, session_id, tag_uid, gate, read_at_ms,
                                    gate_device, client_ref)
    return {"gate_read_id": read_id}


def compute_run(conn, actor_id, event_id):
    elapsed_ms = ioct.compute_run(conn, actor_id, event_id)
    return {"event_id": event_id, "elapsed_ms": elapsed_ms,
            "elapsed": fitness.format_elapsed(elapsed_ms)}


# ---- corrections (PBI-08 L31) ------------------------------------------------
# A correction never overwrites a saved score. `supersede` keeps the old event, marks it
# SUPERSEDED, and opens a new event whose supersedes_event_id points at the original, with
# its own audit entry. `reopen` sends a disputed or submitted event back to ENTERED (the audit
# log keeps the old scorecard). Each is one Vault call, so one audit entry.

def reopen(conn, actor_id, event_id, note):
    fitness.reopen(conn, actor_id, event_id, note)
    return {"event_id": event_id, "status": fitness.get_event(conn, event_id)["status"]}


def supersede(conn, actor_id, event_id, reason):
    new_id = fitness.supersede(conn, actor_id, event_id, reason)
    return {"superseded_event_id": event_id, "new_event_id": new_id,
            "status": fitness.get_event(conn, new_id)["status"]}


# ---- attendance (PBI-09 L32, SR-2) -------------------------------------------
# The Vault already refuses a mark for someone else's section or a cadet who is not
# enrolled, but a refused write rolls back, so it leaves no trace. SR-2 wants every refused
# attempt logged, so the Gatekeeper writes ONE rejected-attempt audit entry (its own small
# transaction, after the failed write rolled back) and then re-raises the error as a 403.
# The entry names the section, date and reason only: no barcode, no ID, no cadet hash.

def _log_rejected(conn, actor_id, section_id, class_date, reason):
    with db.transaction(conn):
        audit.append(conn, function="ATTENDANCE", action="ATTENDANCE_REJECTED",
                     actor_id=actor_id, entity_type="class_section", entity_id=section_id,
                     data={"class_date": class_date, "reason": reason})


def attendance_mark(conn, actor_id, section_id, class_date, status, barcode, client_ref=None):
    edipi_hash = hash_barcode(barcode)
    try:
        att_id = attendance.mark(conn, actor_id, section_id, edipi_hash, class_date,
                                 status, client_ref)
    except (PermissionDenied, NotFound) as exc:
        reason = str(exc)
        if isinstance(exc, NotFound) and "section" not in reason:
            reason = "cadet is not enrolled in this section"
            exc = PermissionDenied(reason)  # a cadet we do not know is not on any roster
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        _log_rejected(conn, actor_id, section_id, class_date, reason)
        raise exc
    return {"attendance_id": att_id, "section_id": section_id, "class_date": class_date,
            "status": status}


def attendance_roster(conn, actor_id, section_id, class_date):
    sec = conn.execute("SELECT instructor_id FROM class_section WHERE id = ?",
                       (section_id,)).fetchone()
    if sec is None:
        raise NotFound(f"section {section_id} not found")
    if sec["instructor_id"] != actor_id:
        _log_rejected(conn, actor_id, section_id, class_date,
                      "only this section's instructor can view the roster")
        raise PermissionDenied("only this section's instructor can view the roster")
    return {"section_id": section_id, "class_date": class_date,
            "roster": attendance.roster(conn, section_id, class_date)}
