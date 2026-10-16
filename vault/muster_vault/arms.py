"""Arms-room Sensitive Item accountability (tertiary stakeholder).

Same "sign here" mechanism as a fitness score: the cadet's CAC signs the exact
sign-out record (weapon serial + cadet + action + nonce). That proves the real
card was present, the holder knew the PIN, and the cadet can't later deny it.
"""
from __future__ import annotations

import sqlite3

from . import attestation, audit, clock, identity
from .db import one, transaction
from .errors import ChallengeError, InvalidTransition, NotFound, SignatureInvalid

NCO_ROLES = {"arms_nco"}


def add_weapon(conn: sqlite3.Connection, actor_id: int, serial: str, kind: str,
               company_id: int | None = None) -> int:
    with transaction(conn):
        identity.require_role(conn, actor_id, {"admin", "arms_nco"})
        cur = conn.execute("INSERT INTO weapon(serial, kind, company_id) VALUES (?, ?, ?)",
                           (serial, kind, company_id))
        conn.execute("INSERT INTO weapon_status(weapon_id, state) VALUES (?, 'IN')",
                     (cur.lastrowid,))
        audit.append(conn, function="ARMS", action="WEAPON_ADDED", actor_id=actor_id,
                     entity_type="weapon", entity_id=cur.lastrowid,
                     data={"serial": serial, "kind": kind, "company_id": company_id})
        return cur.lastrowid


def _weapon(conn, serial: str) -> sqlite3.Row:
    row = one(conn, "SELECT w.*, s.state, s.holder_cadet_id FROM weapon w "
                    "JOIN weapon_status s ON s.weapon_id = w.id WHERE w.serial = ?", (serial,))
    if row is None:
        raise NotFound(f"weapon {serial!r} not found")
    return row


def _record(weapon: sqlite3.Row, cadet: sqlite3.Row, action: str, nco_id: int) -> dict:
    return {"weapon_serial": weapon["serial"], "weapon_kind": weapon["kind"],
            "cadet_edipi_hash": cadet["edipi_hash"],
            "cadet_name": f"{cadet['last_name']}, {cadet['first_name']}",
            "action": action, "nco_id": nco_id}


def _check_state(weapon: sqlite3.Row, cadet: sqlite3.Row, action: str) -> None:
    if action == "OUT" and weapon["state"] != "IN":
        raise InvalidTransition(f"{weapon['serial']} is already signed out")
    if action == "IN":
        if weapon["state"] != "OUT":
            raise InvalidTransition(f"{weapon['serial']} is not signed out")
        if weapon["holder_cadet_id"] != cadet["id"]:
            raise InvalidTransition(f"{weapon['serial']} is signed out to a different cadet")


def request_challenge(conn: sqlite3.Connection, actor_id: int, weapon_serial: str,
                      cadet_edipi_hash: str, action: str) -> tuple[int, bytes]:
    if action not in ("OUT", "IN"):
        raise ValueError("action must be 'OUT' or 'IN'")
    with transaction(conn):
        identity.require_role(conn, actor_id, NCO_ROLES)
        weapon = _weapon(conn, weapon_serial)
        cadet = identity.cadet_by_hash(conn, cadet_edipi_hash)
        _check_state(weapon, cadet, action)
        return attestation.issue_challenge(
            conn, purpose=f"SI_{action}", entity_type="weapon", entity_id=weapon["id"],
            record=_record(weapon, cadet, action, actor_id))


def complete(conn: sqlite3.Connection, actor_id: int, challenge_id: int, weapon_serial: str,
             cadet_edipi_hash: str, action: str, signature: bytes,
             client_ref: str | None = None) -> int:
    """Cadet signed — record the sign-out/return. Returns the si_event id."""
    if client_ref:
        existing = one(conn, "SELECT id FROM si_event WHERE client_ref = ?", (client_ref,))
        if existing:
            return existing["id"]
    weapon_id = None
    try:
        with transaction(conn):
            identity.require_role(conn, actor_id, NCO_ROLES)
            weapon = _weapon(conn, weapon_serial)
            weapon_id = weapon["id"]
            cadet = identity.cadet_by_hash(conn, cadet_edipi_hash)
            _check_state(weapon, cadet, action)
            att_id = attestation.consume(
                conn, challenge_id=challenge_id, purpose=f"SI_{action}",
                entity_type="weapon", entity_id=weapon["id"],
                current_record=_record(weapon, cadet, action, actor_id),
                signer_edipi_hash=cadet["edipi_hash"], signer_role="cadet",
                signature=signature)
            cur = conn.execute(
                "INSERT INTO si_event(weapon_id, cadet_id, action, nco_id, occurred_at, "
                "attestation_id, client_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (weapon["id"], cadet["id"], action, actor_id, clock.now_iso(), att_id,
                 client_ref))
            conn.execute(
                "UPDATE weapon_status SET state = ?, holder_cadet_id = ?, last_event_id = ? "
                "WHERE weapon_id = ?",
                ("OUT" if action == "OUT" else "IN",
                 cadet["id"] if action == "OUT" else None, cur.lastrowid, weapon["id"]))
            att = one(conn, "SELECT payload_sha256 FROM attestation WHERE id = ?", (att_id,))
            audit.append(conn, function="ARMS", action=f"SI_{action}", actor_id=actor_id,
                         entity_type="si_event", entity_id=cur.lastrowid,
                         data={"weapon_serial": weapon_serial, "cadet_id": cadet["id"],
                               "attestation_id": att_id,
                               "payload_sha256": att["payload_sha256"]})
            return cur.lastrowid
    except (SignatureInvalid, ChallengeError) as exc:
        attestation.log_rejection(conn, function="ARMS", actor_id=actor_id,
                                  entity_type="weapon", entity_id=weapon_id,
                                  purpose=f"SI_{action}", reason=str(exc))
        raise


def status_report(conn: sqlite3.Connection) -> list[dict]:
    """Every weapon, IN or OUT, and who has it."""
    return [dict(r) for r in conn.execute(
        "SELECT w.serial, w.kind, s.state, c.last_name, c.first_name "
        "FROM weapon w JOIN weapon_status s ON s.weapon_id = w.id "
        "LEFT JOIN cadet c ON c.id = s.holder_cadet_id ORDER BY w.serial")]
