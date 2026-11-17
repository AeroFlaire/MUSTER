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


_EDIPI = re.compile(r"^\d{10}$")


def hash_identity(barcode=None, edipi=None):
    """Who is this card? Either the raw barcode string (test pack format) or the 10 digit DoD ID
    that the scanner page already parsed on the device. Exactly one. Either way the ID is hashed
    with the secret key right here and the raw value goes out of scope."""
    if (barcode is None) == (edipi is None):
        raise ValueError("Field 'barcode': send the card barcode or the DoD ID (edipi), "
                         "not both and not neither.")
    if barcode is not None:
        return hash_barcode(barcode)
    if not isinstance(edipi, str) or not _EDIPI.match(edipi.strip()):
        raise ValueError("Field 'edipi': must be 10 digits.")
    return identity.hash_edipi(edipi.strip(), identity.load_edipi_key())


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


def open_event(conn, actor_id, session_id, barcode, client_ref=None, edipi=None):
    edipi_hash = hash_identity(barcode, edipi)
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

def ioct_pair(conn, actor_id, session_id, barcode, tag_uid, client_ref=None, edipi=None):
    edipi_hash = hash_identity(barcode, edipi)
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


def attendance_mark(conn, actor_id, section_id, class_date, status, barcode=None,
                    client_ref=None, cadet_id=None, edipi=None):
    """Scan a card (`barcode`) to mark a cadet, or send `cadet_id` (from the roster) to correct
    one row by hand. Exactly one of the two. Marking again with a different status is a
    correction: the Vault keeps the old status in the audit entry."""
    if (barcode is None and edipi is None) == (cadet_id is None):
        raise ValueError("Field 'barcode': send the card (barcode or edipi), or cadet_id (from the "
                         "roster) to correct a row, but not both and not neither.")
    if cadet_id is None:
        edipi_hash = hash_identity(barcode, edipi)
    else:
        row = conn.execute("SELECT edipi_hash FROM cadet WHERE id = ?", (cadet_id,)).fetchone()
        edipi_hash = row["edipi_hash"] if row is not None else None  # unknown id: not on a roster
    try:
        if edipi_hash is None:
            raise NotFound("cadet not found")
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
    who = identity.cadet_by_hash(conn, edipi_hash)
    return {"attendance_id": att_id, "section_id": section_id, "class_date": class_date,
            "status": status, "cadet_name": f"{who['last_name']}, {who['first_name']}"}


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


# ---- who is this card? (CAC sign-in, lookup, registration) -------------------------
# The scanner page parses the card on the device and sends the 10 digit DoD ID over HTTPS. It is
# hashed here with the secret key (the page never holds it) and never stored, logged or returned.
# Staff and cadets live in the Vault (app_user and cadet tables, keyed by the hash). A scan
# IDENTIFIES a person, it does not prove it is them: anyone holding a photo of a card barcode could
# present it. Real proof needs the card chip and a reader, which is out of scope.

SIGNIN_ROLES = {"grader": {"grader", "oic"}}   # position -> Vault roles allowed to sign in as it
REGISTER_ROLES = {"grader", "oic", "instructor", "cadet"}


def _log_core(conn, actor_id, action, data):
    with db.transaction(conn):
        audit.append(conn, function="CORE", action=action, actor_id=actor_id,
                     entity_type="app_user", entity_id=actor_id, data=data)


def cac_signin(conn, edipi, position):
    """Returns what the caller needs to issue a session: kind, actor_id or cadet hash, role, name.
    Raises PermissionDenied (403) if this card is not registered for that position. Each refusal
    is written to the audit log (position and reason only, never an ID or hash)."""
    h = hash_identity(None, edipi)
    if position == "grader":
        row = conn.execute("SELECT id, display_name, role FROM app_user WHERE edipi_hash = ? "
                           "AND active = 1", (h,)).fetchone()
        if row is None or row["role"] not in SIGNIN_ROLES["grader"]:
            _log_core(conn, None, "SIGN_IN_REFUSED",
                      {"position": position, "reason": "card is not a registered grader or OIC"})
            raise PermissionDenied("This card is not registered as a grader or OIC.")
        _log_core(conn, row["id"], "SIGNED_IN", {"position": position, "role": row["role"]})
        return {"kind": "staff", "actor_id": row["id"], "role": row["role"],
                "name": row["display_name"], "cadet_hash": None}
    row = conn.execute("SELECT last_name, first_name FROM cadet WHERE edipi_hash = ? AND active = 1",
                       (h,)).fetchone()
    if row is None:
        _log_core(conn, None, "SIGN_IN_REFUSED",
                  {"position": position, "reason": "card is not a registered cadet"})
        raise PermissionDenied("This card is not registered as a cadet.")
    _log_core(conn, None, "SIGNED_IN", {"position": position, "role": "cadet"})
    return {"kind": "cadet", "actor_id": None, "role": "cadet",
            "name": f"{row['last_name']}, {row['first_name']}", "cadet_hash": h}


def identity_lookup(conn, edipi):
    """A grader scans a cadet's card to confirm who it is. Returns the name the database holds,
    never the hash. Read only."""
    h = hash_identity(None, edipi)
    c = conn.execute("SELECT last_name, first_name FROM cadet WHERE edipi_hash = ? AND active = 1",
                     (h,)).fetchone()
    if c is not None:
        return {"found": True, "kind": "cadet", "name": f"{c['last_name']}, {c['first_name']}",
                "role": "cadet"}
    u = conn.execute("SELECT display_name, role FROM app_user WHERE edipi_hash = ? AND active = 1",
                     (h,)).fetchone()
    if u is not None:
        return {"found": True, "kind": "staff", "name": u["display_name"], "role": u["role"]}
    return {"found": False, "kind": "unknown", "name": None, "role": None}


def register_person(conn, actor_id, edipi, first_name, last_name, role):
    """Admin adds one person. A cadet goes in the cadet table, everyone else in app_user. One Vault
    call (one transaction, one audit entry)."""
    if role not in REGISTER_ROLES:
        raise ValueError("Field 'role': must be one of " + str(sorted(REGISTER_ROLES)) + ".")
    h = hash_identity(None, edipi)
    if (conn.execute("SELECT 1 FROM cadet WHERE edipi_hash = ?", (h,)).fetchone()
            or conn.execute("SELECT 1 FROM app_user WHERE edipi_hash = ?", (h,)).fetchone()):
        raise InvalidTransition("This person is already registered.")
    last, first = last_name.strip(), first_name.strip()
    if role == "cadet":
        identity.add_cadet(conn, actor_id, h, last, first)
    else:
        identity.add_user(conn, actor_id, f"{last}, {first}", role, h)
    return {"registered": True, "name": f"{last}, {first}", "role": role}


def people(conn):
    """Everyone registered with a card, by name and role. No hashes, no IDs."""
    out = [{"name": r["display_name"], "kind": "staff", "role": r["role"], "active": bool(r["active"])}
           for r in conn.execute("SELECT display_name, role, active FROM app_user "
                                 "WHERE edipi_hash IS NOT NULL ORDER BY display_name")]
    out += [{"name": f"{r['last_name']}, {r['first_name']}", "kind": "cadet", "role": "cadet",
             "active": bool(r["active"])}
            for r in conn.execute("SELECT last_name, first_name, active FROM cadet "
                                  "ORDER BY last_name, first_name")]
    return {"count": len(out), "people": out}


# ---- results read (Demo 3, PBI-08) -------------------------------------------
# Read only: nothing here writes to the Vault. Staff roles that may grade (grader, oic) see every
# score. A cadet sees only the scores that belong to the hash on their own credential. The
# caller never names a cadet, so there is no parameter a cadet could change to see another's.
# The cadet's ID hash is left out of the response for everyone.

def results(conn, cadet_hash=None, test_type=None, session_id=None):
    sql = ("SELECT e.id FROM fitness_event e JOIN cadet c ON c.id = e.cadet_id WHERE 1 = 1")
    params = []
    if cadet_hash is not None:
        sql += " AND c.edipi_hash = ?"
        params.append(cadet_hash)
    if test_type is not None:
        sql += " AND e.test_type = ?"
        params.append(test_type)
    if session_id is not None:
        sql += " AND e.session_id = ?"
        params.append(session_id)
    out = []
    for row in conn.execute(sql + " ORDER BY e.id", params).fetchall():
        view = _view(conn, row["id"])
        view["scorecard"].pop("cadet_edipi_hash", None)
        out.append(view)
    return {"scope": "own" if cadet_hash is not None else "all", "count": len(out),
            "results": out}
