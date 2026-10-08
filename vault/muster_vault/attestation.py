"""Challenges and attestations — the "sign here" step, done safely.

Flow (same for a cadet accepting a score, a grader submitting one, or an
arms-room sign-out):

  1. issue_challenge()  The Vault builds the exact bytes to sign:
                        purpose + a one-time random nonce + time + the record.
  2. (Edge)             The station shows the record, the cadet enters their PIN
                        into the CAC, and the card signs those bytes.
  3. consume()          The Vault checks: challenge exists, not expired, not used,
                        is for THIS record, the record hasn't changed since, and
                        the signature matches the signer's ENROLLED key.
                        Then it stores the bytes + signature together.

Because the nonce is single-use, a captured signature can't be replayed.
Because the record is inside the signed bytes, a signature can't be moved to a
different score. Because we keep the bytes, anyone can re-verify later.
"""
from __future__ import annotations

import secrets
import sqlite3

from . import audit, clock, identity, signatures
from .canonical import sha256_hex, signing_payload
from .db import one, transaction
from .errors import ChallengeError, NotFound, SignatureInvalid

DEFAULT_TTL_SECONDS = 300  # 5 minutes to review and sign


def issue_challenge(conn: sqlite3.Connection, *, purpose: str, entity_type: str,
                    entity_id: int | None, record: dict,
                    ttl_seconds: int = DEFAULT_TTL_SECONDS) -> tuple[int, bytes]:
    """Create a one-time challenge. Must run inside db.transaction()."""
    nonce = secrets.token_hex(16)
    now = clock.now_ms()
    issued_at = clock.iso(now)
    payload = signing_payload(purpose, nonce, issued_at, record)
    cur = conn.execute(
        "INSERT INTO signing_challenge(purpose, entity_type, entity_id, nonce, payload, "
        "issued_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (purpose, entity_type, entity_id, nonce, payload, issued_at,
         clock.iso(now + ttl_seconds * 1000)),
    )
    return cur.lastrowid, payload


def consume(conn: sqlite3.Connection, *, challenge_id: int, purpose: str,
            entity_type: str, entity_id: int | None, current_record: dict,
            signer_edipi_hash: str, signer_role: str, signature: bytes) -> int:
    """Verify a signature against a challenge and store it. Inside db.transaction().

    Returns the new attestation id. Raises ChallengeError / SignatureInvalid.
    """
    ch = one(conn, "SELECT * FROM signing_challenge WHERE id = ?", (challenge_id,))
    if ch is None:
        raise ChallengeError("unknown challenge")
    if ch["used_at"] is not None:
        raise ChallengeError("challenge already used (possible replay)")
    if (ch["purpose"], ch["entity_type"], ch["entity_id"]) != (purpose, entity_type, entity_id):
        raise ChallengeError("challenge was issued for a different record or purpose")
    if clock.now_ms() > clock.parse_iso(ch["expires_at"]):
        raise ChallengeError("challenge expired; request a new one")

    # The record must be byte-for-byte what was shown when the challenge was issued.
    rebuilt = signing_payload(purpose, ch["nonce"], ch["issued_at"], current_record)
    if rebuilt != bytes(ch["payload"]):
        raise ChallengeError("record changed after the challenge was issued; re-review")

    try:
        key = identity.active_key(conn, signer_edipi_hash)
    except NotFound as exc:
        raise SignatureInvalid("signer has no enrolled key") from exc
    signatures.verify_signature(key["public_key_pem"], rebuilt, signature, key["algorithm"])

    now = clock.now_iso()
    conn.execute("UPDATE signing_challenge SET used_at = ? WHERE id = ?", (now, challenge_id))
    cur = conn.execute(
        "INSERT INTO attestation(purpose, signer_edipi_hash, signer_role, key_id, "
        "challenge_id, payload, payload_sha256, signature, verified_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (purpose, signer_edipi_hash, signer_role, key["id"], challenge_id, rebuilt,
         sha256_hex(rebuilt), signature, now),
    )
    return cur.lastrowid


def log_rejection(conn: sqlite3.Connection, *, function: str, actor_id: int | None,
                  entity_type: str, entity_id: int | None, purpose: str,
                  reason: str) -> None:
    """Record a failed signing attempt in its own transaction (the failed one rolled back)."""
    with transaction(conn):
        audit.append(conn, function=function, action="SIGNATURE_REJECTED",
                     actor_id=actor_id, entity_type=entity_type, entity_id=entity_id,
                     data={"purpose": purpose, "reason": reason})


def reverify(conn: sqlite3.Connection, attestation_id: int) -> bool:
    """Re-check a stored signature from scratch (bytes + signature + enrolled key)."""
    row = one(
        conn,
        "SELECT a.*, k.public_key_pem, k.algorithm FROM attestation a "
        "JOIN enrolled_key k ON k.id = a.key_id WHERE a.id = ?",
        (attestation_id,),
    )
    if row is None:
        raise NotFound(f"attestation {attestation_id} not found")
    payload = bytes(row["payload"])
    if sha256_hex(payload) != row["payload_sha256"]:
        return False
    try:
        signatures.verify_signature(row["public_key_pem"], payload, bytes(row["signature"]),
                                    row["algorithm"])
    except SignatureInvalid:
        return False
    return True
