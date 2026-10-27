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

from muster_vault import db, fitness, identity, ioct
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
    return {"event_id": event_id, "status": ev["status"],
            "scorecard": fitness.scorecard(conn, event_id)}


def open_event(conn, actor_id, session_id, barcode, client_ref=None):
    edipi_hash = hash_barcode(barcode)
    event_id = fitness.open_event(conn, actor_id, session_id, edipi_hash, client_ref)
    return _view(conn, event_id)


def add_component(conn, actor_id, event_id, component, raw_value, points=None):
    fitness.record_component(conn, actor_id, event_id, component, raw_value, points)
    return {"event_id": event_id, "component": component, "raw_value": str(raw_value),
            "points": points}


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
