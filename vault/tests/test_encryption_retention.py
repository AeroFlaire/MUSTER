"""PBI-05 L25 (SR-4): the database file is unreadable without the key, and retention
is a setting with an audit trail. SYNTHETIC DATA ONLY.

Acceptance criterion this file proves:
  "the database file at rest contains 0 readable cadet identifiers or scores
   when opened with a plain text search"
"""
import sqlite3  # the STANDARD sqlite3 — plays the outsider who has no key
from pathlib import Path

import pytest

from muster_vault import attendance, audit, clock, cli, db, retention
from muster_vault.errors import PermissionDenied, VaultError

from .conftest import TEST_DB_KEY, build_world, dump_all_rows, signed_aft

OTHER_KEY = "ff" * 32
DAY_MS = 86_400_000


# ---- helpers -----------------------------------------------------------------

def _file_bytes(path: Path) -> bytes:
    """Everything SQLite keeps on disk for this database: main file + WAL + shm."""
    out = b""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


def _secrets_in(w, ev) -> list[str]:
    """Values that must never be readable in the file: names, ID hashes, scores, places."""
    card = dict(w.conn.execute(
        "SELECT raw_value FROM fitness_component WHERE event_id = ? AND component = '2MR'",
        (ev,)).fetchone())
    vals = ["Alpha", "Bravo", "Charlie", "Arvin Gym", card["raw_value"]]
    vals += [cac.edipi_hash for _, cac in w.cadets.values()]
    vals += [r["entry_hash"] for r in w.conn.execute("SELECT entry_hash FROM audit_log")]
    # Encrypted bytes are random, so a SHORT string turns up by pure chance: in ~1 MB,
    # any given 3 bytes appear ~6% of the time. At 5+ bytes the odds are ~1 in a million
    # per search, so a hit really means plaintext leaked.
    assert all(len(v) >= 5 for v in vals)
    return vals


@pytest.fixture
def encrypted(tmp_path):
    path = tmp_path / "vault.db"
    conn = db.connect(path, key=TEST_DB_KEY)
    w = build_world(conn)
    ev = signed_aft(w)
    yield path, w, ev
    conn.close()


# ---- encryption at rest ------------------------------------------------------

def test_file_has_no_readable_names_ids_or_scores(encrypted):
    path, w, ev = encrypted
    secrets = _secrets_in(w, ev)
    everything = dump_all_rows(w.conn)
    for s in secrets:  # control: the values really are in the database...
        assert s in everything
    w.conn.execute("PRAGMA wal_checkpoint(FULL)")
    raw = _file_bytes(path)
    assert len(raw) > 10_000
    for s in secrets:  # ...but not in the bytes on disk
        assert s.encode() not in raw, f"{s!r} readable in the file"
    assert b"SQLite format 3" not in raw
    assert b"CREATE TABLE" not in raw


def test_still_unreadable_after_close(encrypted):
    path, w, ev = encrypted
    secrets = _secrets_in(w, ev)
    w.conn.close()
    raw = _file_bytes(path)
    for s in secrets:
        assert s.encode() not in raw


def test_outsider_without_key_cannot_open_it(encrypted):
    path, w, _ = encrypted
    w.conn.close()
    plain = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):
        plain.execute("SELECT * FROM cadet").fetchall()
    plain.close()


def test_reopens_with_right_key_and_chain_still_passes(encrypted):
    path, w, _ = encrypted
    n = w.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    w.conn.close()
    conn = db.connect(path, key=TEST_DB_KEY)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == n
    assert audit.verify(conn).ok
    conn.close()


def test_wrong_key_is_refused(encrypted):
    path, w, _ = encrypted
    w.conn.close()
    with pytest.raises(VaultError, match="wrong MUSTER_DB_KEY"):
        db.connect(path, key=OTHER_KEY)


def test_file_database_needs_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MUSTER_DB_KEY", raising=False)
    with pytest.raises(VaultError, match="set MUSTER_DB_KEY"):
        db.connect(tmp_path / "x.db")
    assert not (tmp_path / "x.db").exists() or (tmp_path / "x.db").stat().st_size == 0


def test_key_comes_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSTER_DB_KEY", TEST_DB_KEY)
    conn = db.connect(tmp_path / "x.db")
    build_world(conn)
    conn.close()
    assert db.connect(tmp_path / "x.db", key=TEST_DB_KEY) is not None


@pytest.mark.parametrize("bad", ["short", "zz" * 32, TEST_DB_KEY + "00", "x'00'; DROP"])
def test_malformed_key_rejected_without_echoing_it(tmp_path, bad):
    with pytest.raises(VaultError) as exc:
        db.connect(tmp_path / "x.db", key=bad)
    assert bad not in str(exc.value)


def test_new_key_is_256_bits_and_random():
    a, b = db.new_key(), db.new_key()
    assert len(a) == 64 and int(a, 16) >= 0 and a != b


def test_plaintext_database_converts_to_encrypted(tmp_path):
    old = tmp_path / "old.db"
    plain = sqlite3.connect(old)
    plain.executescript((Path(db.__file__).parent / "schema.sql").read_text("utf-8"))
    plain.execute("INSERT INTO company(name) VALUES ('Plaintext-Company-Zulu')")
    plain.commit()
    plain.close()
    assert b"Plaintext-Company-Zulu" in old.read_bytes()

    new = tmp_path / "new.db"
    db.encrypt_plaintext_db(old, new, key=TEST_DB_KEY)
    assert b"Plaintext-Company-Zulu" not in new.read_bytes()
    conn = db.connect(new, key=TEST_DB_KEY)
    assert conn.execute("SELECT name FROM company").fetchone()[0] == "Plaintext-Company-Zulu"
    conn.close()
    with pytest.raises(VaultError, match="already exists"):
        db.encrypt_plaintext_db(old, new, key=TEST_DB_KEY)


# ---- retention setting -------------------------------------------------------

def test_default_is_a_flagged_placeholder(world):
    for s in retention.settings(world.conn):
        assert s["days"] == retention.PLACEHOLDER_DAYS
        assert s["placeholder"] is True
        assert "PLACEHOLDER" in s["source"]


def test_admin_sets_retention_and_it_is_audited(world):
    before = audit.head(world.conn)["seq"]
    r = retention.set_days(world.conn, world.admin, "FITNESS", 1095, "Test citation, para 1")
    assert (r["days"], r["placeholder"], r["set_by"]) == (1095, False, world.admin)
    assert retention.get(world.conn, "ATTENDANCE")["placeholder"] is True  # others untouched
    last = world.conn.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    assert last["seq"] == before + 1 and last["action"] == "RETENTION_SET"
    assert '"old_days":1825' in last["data"] and '"new_days":1095' in last["data"]
    retention.set_days(world.conn, world.admin, "FITNESS", 730, "Changed again")
    assert retention.get(world.conn, "FITNESS")["days"] == 730
    assert audit.verify(world.conn).ok


def test_non_admin_cannot_change_retention(world):
    before = audit.head(world.conn)["seq"]
    for who in (world.grader, world.oic, world.instructor):
        with pytest.raises(PermissionDenied):
            retention.set_days(world.conn, who, "FITNESS", 1, "trying to shorten it")
    assert retention.get(world.conn, "FITNESS")["placeholder"] is True
    assert audit.head(world.conn)["seq"] == before


@pytest.mark.parametrize("days", [0, -5, 36501, "30", 30.0, True])
def test_bad_days_rejected(world, days):
    with pytest.raises(ValueError):
        retention.set_days(world.conn, world.admin, "FITNESS", days, "x")


def test_source_and_function_required(world):
    with pytest.raises(ValueError, match="source"):
        retention.set_days(world.conn, world.admin, "FITNESS", 30, "  ")
    with pytest.raises(ValueError, match="unknown function"):
        retention.set_days(world.conn, world.admin, "PAYROLL", 30, "x")


def test_records_past_retention_are_reported(world):
    ev = signed_aft(world)
    sec = attendance.add_section(world.conn, world.admin, "XE401", "G1", "B hour",
                                 "2027-1", world.instructor)
    attendance.enroll(world.conn, world.admin, sec, world.cadets["alpha"][0])
    attendance.mark(world.conn, world.instructor, sec,
                    world.cadets["alpha"][1].edipi_hash, "2026-10-08")
    now = clock.now_ms()

    assert retention.due(world.conn, "FITNESS", now) == []
    later = now + (retention.PLACEHOLDER_DAYS + 1) * DAY_MS
    fit = retention.due(world.conn, "FITNESS", later)
    assert [(d["entity_type"], d["entity_id"]) for d in fit] == [("fitness_event", ev)]
    assert len(retention.due(world.conn, "ATTENDANCE", later)) == 1
    assert retention.due(world.conn, "IOCT", later) == []  # AFT is not counted as IOCT

    retention.set_days(world.conn, world.admin, "FITNESS", 30, "Test citation")
    assert retention.due(world.conn, "FITNESS", now + 29 * DAY_MS) == []
    assert len(retention.due(world.conn, "FITNESS", now + 31 * DAY_MS)) == 1

    rep = {r["function"]: r for r in retention.report(world.conn, now + 31 * DAY_MS)}
    assert rep["FITNESS"]["past_retention"] == 1 and not rep["FITNESS"]["placeholder"]
    assert rep["ATTENDANCE"]["past_retention"] == 0 and rep["ATTENDANCE"]["placeholder"]


def test_reporting_deletes_nothing(world):
    signed_aft(world)
    far = clock.now_ms() + 100 * 365 * DAY_MS
    rows_before = dump_all_rows(world.conn)
    retention.report(world.conn, far)
    assert dump_all_rows(world.conn) == rows_before


# ---- command line --------------------------------------------------------------

def test_cli_new_key_and_retention(tmp_path, monkeypatch, capsys):
    assert cli.main(["new-key"]) == 0
    key = capsys.readouterr().out.strip()
    assert len(key) == 64
    monkeypatch.setenv("MUSTER_DB_KEY", key)
    path = str(tmp_path / "cli.db")
    conn = db.connect(path)
    admin = build_world(conn).admin
    conn.close()
    assert cli.main(["set-retention", "--db", path, "--actor", str(admin), "--function",
                     "ATTENDANCE", "--days", "365", "--source", "Test citation"]) == 0
    assert cli.main(["retention", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "ATTENDANCE" in out and "365 days" in out and "PLACEHOLDER, not policy" in out
    monkeypatch.setenv("MUSTER_DB_KEY", OTHER_KEY)
    assert cli.main(["verify", "--db", path]) == 1  # wrong key: clean error, not a traceback
