"""Opening the database and running writes safely.

ENCRYPTION AT REST (SR-4)
    Every database FILE is encrypted with SQLCipher: SQLite with AES-256 applied
    to every page of the file (and to the WAL and temp files). Think of it as the
    whole filing cabinet being locked, not just some drawers. Without the key the
    file is random-looking bytes: no names, no scores, no hashes, not even the
    "SQLite format 3" header. Encrypting the whole file (instead of single columns)
    also covers the audit log, whose entries carry full scorecards.

    The key is 32 random bytes written as 64 hex characters, read from the
    MUSTER_DB_KEY environment variable. Make one with:
        python -m muster_vault new-key
    The key NEVER goes in the repo, a ticket, or a chat. Lose it and the data is gone.

    An in-memory database (":memory:", used by unit tests) never touches disk, so
    it needs no key.

TRANSACTIONS
    Every public Vault function that changes data runs inside `transaction()`.
    That gives two guarantees:
      1. The working-table change and its audit_log entry land together or not at all.
      2. `BEGIN IMMEDIATE` takes the write lock up front, so two writers can never
         both read the same "last audit entry" and fork the hash chain.
"""
from __future__ import annotations

import os
import re
import secrets
from contextlib import contextmanager
from importlib import resources
from typing import Iterator

import sqlcipher3.dbapi2 as sqlite  # drop-in for the standard sqlite3 module

from . import clock
from .errors import VaultError

SCHEMA_VERSION = 2

DB_KEY_ENV = "MUSTER_DB_KEY"
_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Re-exported so callers catch the right classes. sqlcipher3 raises its own
# exception types, which are NOT the standard library's sqlite3 ones.
Connection = sqlite.Connection
Row = sqlite.Row
Error = sqlite.Error
DatabaseError = sqlite.DatabaseError
IntegrityError = sqlite.IntegrityError


def new_key() -> str:
    """A fresh random 256-bit database key, as 64 hex characters."""
    return secrets.token_hex(32)


def load_db_key() -> str:
    key = os.environ.get(DB_KEY_ENV, "").strip()
    if not key:
        raise VaultError(
            f"set {DB_KEY_ENV} to the database key (make one with: python -m muster_vault new-key)")
    return key


def _check_key(key: str) -> str:
    key = key.strip()
    if not _KEY_RE.match(key):
        # Never echo the key back: it may be a real secret typed wrong.
        raise VaultError(f"{DB_KEY_ENV} must be exactly 64 hex characters (a 256-bit key)")
    return key.lower()


def _apply_key(conn: sqlite.Connection, key: str) -> None:
    # The x'...' form hands SQLCipher the raw 256-bit key, so it skips the slow
    # passphrase-stretching step (the key is already random). The regex check above
    # guarantees only hex digits reach this string.
    conn.execute(f"PRAGMA key = \"x'{_check_key(key)}'\"")


def connect(path: str = ":memory:", key: str | None = None) -> sqlite.Connection:
    """Open (or create) an encrypted Vault database and make sure the schema is in place.

    `key` defaults to the MUSTER_DB_KEY environment variable. A file database will
    not open without a key; ":memory:" needs none.
    """
    path = str(path)
    conn = sqlite.connect(path, isolation_level=None)  # we manage transactions ourselves
    try:
        if path != ":memory:":
            _apply_key(conn, key if key is not None else load_db_key())
            if not conn.execute("PRAGMA cipher_version").fetchone():
                raise VaultError("SQLCipher is not active; refusing to open an unencrypted database")
            try:
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except sqlite.DatabaseError as exc:
                raise VaultError(
                    "cannot open the database: wrong MUSTER_DB_KEY, or the file is not an "
                    "encrypted MUSTER database (convert an old plaintext one with: "
                    "python -m muster_vault encrypt-db)") from exc
            conn.execute("PRAGMA journal_mode = WAL")   # readers don't block the writer
            conn.execute("PRAGMA synchronous = FULL")   # survive power loss
        conn.execute("PRAGMA temp_store = MEMORY")      # sorts/temp tables never hit disk
        conn.row_factory = sqlite.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def encrypt_plaintext_db(plain_path: str, encrypted_path: str, key: str | None = None) -> None:
    """Copy an old unencrypted database into a new encrypted file (one-time migration)."""
    key = _check_key(key if key is not None else load_db_key())
    if os.path.exists(encrypted_path):
        raise VaultError(f"{encrypted_path} already exists; refusing to overwrite it")
    conn = sqlite.connect(str(plain_path), isolation_level=None)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()  # is it really plaintext?
        conn.execute("ATTACH DATABASE ? AS encrypted KEY \"x'" + key + "'\"",
                     (str(encrypted_path),))
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute("DETACH DATABASE encrypted")
    except sqlite.DatabaseError as exc:
        raise VaultError(f"could not convert {plain_path}: {exc}") from exc
    finally:
        conn.close()


def init_schema(conn: sqlite.Connection) -> None:
    sql = resources.files("muster_vault").joinpath("schema.sql").read_text("utf-8")
    conn.executescript(sql)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, clock.now_iso()),
    )


@contextmanager
def transaction(conn: sqlite.Connection) -> Iterator[sqlite.Connection]:
    if conn.in_transaction:
        # Already inside one of our transactions (a Vault function calling another).
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def one(conn: sqlite.Connection, sql: str, params: tuple = ()) -> sqlite.Row | None:
    return conn.execute(sql, params).fetchone()
