"""Cadet receipts.

The receipt is the cadet's own copy of the proof — like a bank deposit slip.
It carries:
  * the score they signed,
  * the SHA-256 of the exact bytes they signed, and
  * the audit-log seq + entry hash where the signing was recorded.

Later, `audit.check_anchor(seq, hash)` tells you whether the log still contains
that exact entry. If someone rewrote the log (even recomputing every hash), the
cadet's receipt won't match. Delivery (print / email) is the Edge's job.
"""
from __future__ import annotations

import sqlite3

from . import audit, clock, fitness
from .db import one, transaction
from .errors import InvalidTransition


def build(conn: sqlite3.Connection, event_id: int) -> dict:
    ev = fitness.get_event(conn, event_id)
    if ev["status"] not in ("SIGNED", "EXPORTED", "SUPERSEDED") or not ev["cadet_attestation_id"]:
        raise InvalidTransition("no receipt until the cadet has signed")
    att = one(conn, "SELECT a.payload_sha256, a.verified_at, k.key_fingerprint "
                    "FROM attestation a JOIN enrolled_key k ON k.id = a.key_id WHERE a.id = ?",
              (ev["cadet_attestation_id"],))
    entry = one(conn, "SELECT seq, entry_hash, ts FROM audit_log WHERE action = 'EVENT_SIGNED' "
                      "AND entity_type = 'fitness_event' AND entity_id = ? "
                      "ORDER BY seq DESC LIMIT 1", (event_id,))
    return {
        "receipt_version": 1,
        "scorecard": fitness.scorecard(conn, event_id),
        "signed_at": att["verified_at"],
        "signed_payload_sha256": att["payload_sha256"],
        "signing_key_fingerprint": att["key_fingerprint"],
        "audit_anchor": {"seq": entry["seq"], "entry_hash": entry["entry_hash"]},
        "issued_at": clock.now_iso(),
    }


def issue(conn: sqlite3.Connection, actor_id: int | None, event_id: int) -> dict:
    """Build a receipt and log that it was handed out."""
    receipt = build(conn, event_id)
    with transaction(conn):
        audit.append(conn, function="FITNESS", action="RECEIPT_ISSUED", actor_id=actor_id,
                     entity_type="fitness_event", entity_id=event_id,
                     data={"audit_anchor": receipt["audit_anchor"]})
    return receipt


def render_text(receipt: dict) -> str:
    """Plain-text version for printing or email."""
    card = receipt["scorecard"]
    lines = [
        "MUSTER — Fitness Score Receipt",
        "=" * 40,
        f"Cadet:     {card['cadet_name']}",
        f"Test:      {card['test_type']}  ({card['session_date']}, {card['location']})",
        f"Event ID:  {card['event_id']}",
    ]
    if card["test_type"] == "AFT":
        for c in card["components"]:
            pts = "-" if c["points"] is None else c["points"]
            lines.append(f"  {c['component']:<4} {c['raw_value']:>10}   {pts:>3} pts")
        lines.append(f"Total:     {card['total_points']}")
    else:
        lines.append(f"Time:      {card['ioct']['elapsed']}")
    lines += [
        "-" * 40,
        f"Signed:    {receipt['signed_at']}",
        f"Signed-data SHA-256: {receipt['signed_payload_sha256']}",
        f"Audit entry: #{receipt['audit_anchor']['seq']}",
        f"Audit hash:  {receipt['audit_anchor']['entry_hash']}",
        "Keep this receipt. It lets anyone check the record wasn't changed.",
    ]
    return "\n".join(lines)
