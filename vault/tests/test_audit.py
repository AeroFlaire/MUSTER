"""SR-3: the audit log is append-only and any edit/deletion is detectable."""
import sqlite3

import pytest

from muster_vault import audit, db

from .conftest import signed_aft


def test_fresh_chain_passes(world):
    result = audit.verify(world.conn)
    assert result.ok, result
    assert result.entries_checked > 10


def test_triggers_block_update_and_delete(world):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        world.conn.execute("UPDATE audit_log SET action = 'X' WHERE seq = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        world.conn.execute("DELETE FROM audit_log WHERE seq = 1")


def _drop_locks(conn):
    """Simulate someone with direct file access removing the app's locks."""
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("DROP TRIGGER audit_log_no_delete")


def test_edited_entry_is_detected(world):
    ev = signed_aft(world)
    _drop_locks(world.conn)
    seq = world.conn.execute(
        "SELECT seq FROM audit_log WHERE action = 'COMPONENT_ENTERED' AND entity_id = ? "
        "LIMIT 1", (ev,)).fetchone()["seq"]
    world.conn.execute("UPDATE audit_log SET data = replace(data, '200', '400') "
                       "WHERE seq = ?", (seq,))
    result = audit.verify(world.conn)
    assert not result.ok
    assert result.first_bad_seq == seq
    assert "changed" in result.reason


def test_deleted_entry_is_detected(world):
    signed_aft(world)
    _drop_locks(world.conn)
    world.conn.execute("DELETE FROM audit_log WHERE seq = 5")
    result = audit.verify(world.conn)
    assert not result.ok and result.first_bad_seq == 5


def test_recomputed_chain_is_caught_by_receipt_anchor(world):
    """The out-of-scope attack (edit + recompute every hash) still fails a receipt check."""
    from muster_vault import receipts
    ev = signed_aft(world)
    anchor = receipts.build(world.conn, ev)["audit_anchor"]
    _drop_locks(world.conn)
    # Attacker edits an early entry and recomputes the whole chain after it.
    world.conn.execute("UPDATE audit_log SET data = '{\"forged\":true}' WHERE seq = 3")
    prev = None
    for row in world.conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall():
        p = audit.GENESIS_HASH if prev is None else prev
        h = audit.compute_entry_hash(row["seq"], row["ts"], row["function"], row["action"],
                                     row["actor_id"], row["entity_type"], row["entity_id"],
                                     row["data"], p)
        world.conn.execute("UPDATE audit_log SET prev_hash = ?, entry_hash = ? WHERE seq = ?",
                           (p, h, row["seq"]))
        prev = h
    assert audit.verify(world.conn).ok            # chain alone is fooled (stated limit)...
    assert not audit.check_anchor(world.conn, anchor["seq"], anchor["entry_hash"])  # ...receipt isn't


def test_append_outside_transaction_refused(conn):
    with pytest.raises(RuntimeError):
        audit.append(conn, function="CORE", action="X", actor_id=None,
                     entity_type="t", entity_id=None, data={})


def test_failed_write_leaves_no_audit_entry(world):
    before = audit.head(world.conn)["seq"]
    with pytest.raises(Exception):
        with db.transaction(world.conn):
            audit.append(world.conn, function="CORE", action="WILL_ROLL_BACK",
                         actor_id=None, entity_type="t", entity_id=None, data={})
            raise RuntimeError("boom")
    assert audit.head(world.conn)["seq"] == before
    assert audit.verify(world.conn).ok
