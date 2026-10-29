"""MUSTER Vault — Zone 3, the trusted record store.

Usage (from the Gatekeeper):

    from muster_vault import db, identity, fitness, ioct, attendance, arms, audit

    conn = db.connect("muster.db")
    event_id = fitness.open_event(conn, grader_id, session_id, cadet_edipi_hash)
    ...
    print(audit.verify(conn))   # PASS, or the first broken entry

Every function that changes data writes its record AND an audit_log entry in
one transaction. Nothing outside this package should write SQL to the database.
"""

from . import (  # noqa: F401
    arms,
    attendance,
    attestation,
    audit,
    canonical,
    clock,
    db,
    errors,
    export,
    fitness,
    identity,
    ioct,
    receipts,
    retention,
    signatures,
)

__version__ = "0.1.0"
