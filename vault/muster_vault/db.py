"""Opening the database and running writes safely.

Every public Vault function that changes data runs inside `transaction()`.
That gives two guarantees:
  1. The working-table change and its audit_log entry land together or not at all.
  2. `BEGIN IMMEDIATE` takes the write lock up front, so two writers can never
     both read the same "last audit entry" and fork the hash chain.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from typing import Iterator

from . import clock

SCHEMA_VERSION = 1


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open (or create) a Vault database and make sure the schema is in place."""
    conn = sqlite3.connect(path, isolation_level=None)  # we manage transactions ourselves
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")   # readers don't block the writer
        conn.execute("PRAGMA synchronous = FULL")   # survive power loss
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files("muster_vault").joinpath("schema.sql").read_text("utf-8")
    conn.executescript(sql)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, clock.now_iso()),
    )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
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


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()
