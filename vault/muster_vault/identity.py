"""People, roles, and enrolled keys.

EDIPI handling (SR-4): the Vault never receives a raw DoD ID. The Gatekeeper
parses the CAC barcode, calls `hash_edipi`, throws the raw string away, and
passes only the hash here.

Why a KEYED hash (HMAC) and not plain SHA-256: there are only ~10 billion
possible 10-digit EDIPIs. Plain SHA-256 of each could be precomputed in an
afternoon, turning every stored hash back into an ID. With HMAC, an attacker
also needs the secret key, which lives outside the database.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3

from . import audit, clock, signatures
from .db import one, transaction
from .errors import NotFound, PermissionDenied, VaultError

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EDIPI_RE = re.compile(r"^\d{10}$")

ROLES = {"admin", "oic", "grader", "compiler_station", "gate_station",
         "instructor", "arms_nco"}


# ---------------------------------------------------------------------------
# EDIPI hashing (run by the Gatekeeper; lives here so both sides agree)
# ---------------------------------------------------------------------------

def load_edipi_key() -> bytes:
    key = os.environ.get("MUSTER_EDIPI_KEY")
    if not key or len(key) < 32:
        raise VaultError("set MUSTER_EDIPI_KEY to a secret of at least 32 characters")
    return key.encode("utf-8")


def hash_edipi(edipi: str, key: bytes) -> str:
    edipi = edipi.strip()
    if not _EDIPI_RE.match(edipi):
        raise ValueError("EDIPI must be exactly 10 digits")
    return hmac.new(key, edipi.encode("ascii"), hashlib.sha256).hexdigest()


def _check_hash(h: str) -> str:
    if not _HASH_RE.match(h or ""):
        raise ValueError("expected a 64-character lowercase hex EDIPI hash, not a raw ID")
    return h


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

def require_role(conn: sqlite3.Connection, actor_id: int, allowed: set[str]) -> sqlite3.Row:
    """Defense in depth: the Gatekeeper checks roles too, but the Vault re-checks."""
    user = one(conn, "SELECT * FROM app_user WHERE id = ?", (actor_id,))
    if user is None or not user["active"]:
        raise PermissionDenied(f"user {actor_id} does not exist or is inactive")
    if user["role"] not in allowed:
        raise PermissionDenied(
            f"role {user['role']!r} may not do this (needs one of {sorted(allowed)})"
        )
    return user


# ---------------------------------------------------------------------------
# Users, companies, cadets
# ---------------------------------------------------------------------------

def bootstrap_admin(conn: sqlite3.Connection, display_name: str,
                    edipi_hash: str | None = None) -> int:
    """Create the very first admin. Refuses if an admin already exists."""
    with transaction(conn):
        if one(conn, "SELECT 1 FROM app_user WHERE role = 'admin'"):
            raise PermissionDenied("an admin already exists; use add_user")
        cur = conn.execute(
            "INSERT INTO app_user(edipi_hash, display_name, role, created_at) "
            "VALUES (?, ?, 'admin', ?)",
            (_check_hash(edipi_hash) if edipi_hash else None, display_name, clock.now_iso()),
        )
        audit.append(conn, function="CORE", action="ADMIN_BOOTSTRAPPED", actor_id=None,
                     entity_type="app_user", entity_id=cur.lastrowid,
                     data={"display_name": display_name})
        return cur.lastrowid


def add_user(conn: sqlite3.Connection, actor_id: int, display_name: str, role: str,
             edipi_hash: str | None = None) -> int:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        cur = conn.execute(
            "INSERT INTO app_user(edipi_hash, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (_check_hash(edipi_hash) if edipi_hash else None, display_name, role,
             clock.now_iso()),
        )
        audit.append(conn, function="CORE", action="USER_ADDED", actor_id=actor_id,
                     entity_type="app_user", entity_id=cur.lastrowid,
                     data={"display_name": display_name, "role": role})
        return cur.lastrowid


def set_user_active(conn: sqlite3.Connection, actor_id: int, user_id: int,
                    active: bool) -> None:
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        conn.execute("UPDATE app_user SET active = ? WHERE id = ?", (int(active), user_id))
        audit.append(conn, function="CORE", action="USER_ACTIVE_SET", actor_id=actor_id,
                     entity_type="app_user", entity_id=user_id, data={"active": active})


def add_company(conn: sqlite3.Connection, actor_id: int, name: str) -> int:
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        cur = conn.execute("INSERT INTO company(name) VALUES (?)", (name,))
        audit.append(conn, function="CORE", action="COMPANY_ADDED", actor_id=actor_id,
                     entity_type="company", entity_id=cur.lastrowid, data={"name": name})
        return cur.lastrowid


def add_cadet(conn: sqlite3.Connection, actor_id: int, edipi_hash: str, last_name: str,
              first_name: str, company_id: int | None = None,
              class_year: int | None = None) -> int:
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        cur = conn.execute(
            "INSERT INTO cadet(edipi_hash, last_name, first_name, company_id, class_year, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_check_hash(edipi_hash), last_name, first_name, company_id, class_year,
             clock.now_iso()),
        )
        audit.append(conn, function="CORE", action="CADET_ADDED", actor_id=actor_id,
                     entity_type="cadet", entity_id=cur.lastrowid,
                     data={"last_name": last_name, "first_name": first_name,
                           "company_id": company_id, "class_year": class_year})
        return cur.lastrowid


def cadet_by_hash(conn: sqlite3.Connection, edipi_hash: str) -> sqlite3.Row:
    row = one(conn, "SELECT * FROM cadet WHERE edipi_hash = ? AND active = 1",
              (_check_hash(edipi_hash),))
    if row is None:
        raise NotFound("no active cadet enrolled with that CAC")
    return row


def get_cadet(conn: sqlite3.Connection, cadet_id: int) -> sqlite3.Row:
    row = one(conn, "SELECT * FROM cadet WHERE id = ?", (cadet_id,))
    if row is None:
        raise NotFound(f"cadet {cadet_id} not found")
    return row


# ---------------------------------------------------------------------------
# Key enrollment (the CAC's PIV Authentication public key)
# ---------------------------------------------------------------------------

def enroll_key(conn: sqlite3.Connection, actor_id: int, edipi_hash: str, pem: str,
               algorithm: str | None = None) -> int:
    """Record a person's CAC public key. Re-issued CAC -> revoke_key, then enroll again."""
    pub_pem = signatures.public_key_pem(pem)
    fp = signatures.fingerprint(pub_pem)
    alg = algorithm or signatures.default_algorithm(pub_pem)
    if alg not in signatures.ALGORITHMS:
        raise ValueError(f"unsupported algorithm {alg!r}")
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        cur = conn.execute(
            "INSERT INTO enrolled_key(edipi_hash, public_key_pem, key_fingerprint, "
            "algorithm, enrolled_by, enrolled_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_check_hash(edipi_hash), pub_pem, fp, alg, actor_id, clock.now_iso()),
        )
        audit.append(conn, function="CORE", action="KEY_ENROLLED", actor_id=actor_id,
                     entity_type="enrolled_key", entity_id=cur.lastrowid,
                     data={"edipi_hash": edipi_hash, "key_fingerprint": fp,
                           "algorithm": alg})
        return cur.lastrowid


def revoke_key(conn: sqlite3.Connection, actor_id: int, key_id: int, reason: str) -> None:
    with transaction(conn):
        require_role(conn, actor_id, {"admin"})
        row = one(conn, "SELECT * FROM enrolled_key WHERE id = ?", (key_id,))
        if row is None:
            raise NotFound(f"key {key_id} not found")
        if row["revoked_at"]:
            return
        conn.execute("UPDATE enrolled_key SET revoked_at = ? WHERE id = ?",
                     (clock.now_iso(), key_id))
        audit.append(conn, function="CORE", action="KEY_REVOKED", actor_id=actor_id,
                     entity_type="enrolled_key", entity_id=key_id,
                     data={"reason": reason, "key_fingerprint": row["key_fingerprint"]})


def active_key(conn: sqlite3.Connection, edipi_hash: str) -> sqlite3.Row:
    row = one(conn, "SELECT * FROM enrolled_key WHERE edipi_hash = ? AND revoked_at IS NULL",
              (edipi_hash,))
    if row is None:
        raise NotFound("no enrolled signing key for this person")
    return row
