"""IOCT: wristband pairing, raw gate reads, Vault-computed time, cadet signs."""
import pytest

from muster_vault import audit, clock, fitness, ioct
from muster_vault.errors import InvalidTransition, NotFound, TimingError


def _run(w, who="alpha", tag="TAG-001", seconds=185.25, extra_start_reads=2):
    _, cac = w.cadets[who]
    ev = ioct.pair(w.conn, w.grader, w.ioct_session, cac.edipi_hash, tag)
    t0 = clock.now_ms() + 1_000
    # Cadet lingers at the start gate: several START reads, last one is the real start.
    for k in range(extra_start_reads):
        ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "START", t0 + k * 300, "G1")
    start = t0 + extra_start_reads * 300
    ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "START", start, "G1")
    end = start + int(seconds * 1000)
    ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "END", end, "G2")
    ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "END", end + 400, "G2")
    return ev, start, end


def test_full_ioct_flow(world):
    ev, start, end = _run(world)
    elapsed = ioct.compute_run(world.conn, world.grader, ev)
    assert elapsed == end - start == 185_250
    fitness.submit(world.conn, world.grader, ev)

    # At the compiler the cadet taps their band to find the run.
    assert ioct.active_event_for_tag(world.conn, world.ioct_session, "TAG-001") == ev
    _, cac = world.cadets["alpha"]
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    assert b'"elapsed":"3:05.25"' in payload
    fitness.accept(world.conn, world.station, ev, ch, cac.sign(payload))

    assert fitness.get_event(world.conn, ev)["status"] == "SIGNED"
    with pytest.raises(NotFound):   # band released after signing
        ioct.active_event_for_tag(world.conn, world.ioct_session, "TAG-001")
    assert fitness.verify_event(world.conn, ev)["ok"]
    assert audit.verify(world.conn).ok


def test_band_cannot_be_on_two_cadets(world):
    _, a = world.cadets["alpha"]
    _, b = world.cadets["bravo"]
    ioct.pair(world.conn, world.grader, world.ioct_session, a.edipi_hash, "TAG-001")
    with pytest.raises(InvalidTransition, match="still assigned"):
        ioct.pair(world.conn, world.grader, world.ioct_session, b.edipi_hash, "TAG-001")


def test_band_reused_after_release_uses_only_new_reads(world):
    ev1, _, _ = _run(world, "alpha", seconds=200)
    ioct.compute_run(world.conn, world.grader, ev1)
    fitness.submit(world.conn, world.grader, ev1)
    _, a = world.cadets["alpha"]
    ch, p = fitness.request_cadet_challenge(world.conn, world.station, ev1)
    fitness.accept(world.conn, world.station, ev1, ch, a.sign(p))

    import time
    time.sleep(0.01)
    # Second cadet gets the same band; reads must be well after the first run.
    _, b = world.cadets["bravo"]
    ev2 = ioct.pair(world.conn, world.grader, world.ioct_session, b.edipi_hash, "TAG-001")
    t = clock.now_ms() + 300_000
    ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-001", "START", t, "G1")
    ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-001", "END",
                          t + 150_000, "G2")
    assert ioct.compute_run(world.conn, world.grader, ev2) == 150_000


def test_missing_end_read(world):
    _, cac = world.cadets["alpha"]
    ev = ioct.pair(world.conn, world.grader, world.ioct_session, cac.edipi_hash, "TAG-002")
    ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-002", "START",
                          clock.now_ms() + 1000, "G1")
    with pytest.raises(TimingError, match="no END"):
        ioct.compute_run(world.conn, world.grader, ev)


def test_impossible_time_needs_oic(world):
    ev, _, _ = _run(world, seconds=20)
    with pytest.raises(TimingError, match="outside the allowed range"):
        ioct.compute_run(world.conn, world.grader, ev)


def test_cannot_submit_without_time(world):
    _, cac = world.cadets["alpha"]
    ev = ioct.pair(world.conn, world.grader, world.ioct_session, cac.edipi_hash, "TAG-003")
    with pytest.raises(InvalidTransition, match="no IOCT run time"):
        fitness.submit(world.conn, world.grader, ev)


def test_gate_retry_is_not_duplicated(world):
    t = clock.now_ms()
    a = ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-001", "START",
                              t, "G1", client_ref="g1-0001")
    b = ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-001", "START",
                              t, "G1", client_ref="g1-0001")
    assert a == b
    assert world.conn.execute("SELECT COUNT(*) FROM gate_read").fetchone()[0] == 1


def test_unmatched_reads_are_kept_and_reported(world):
    ioct.record_gate_read(world.conn, world.gate, world.ioct_session, "TAG-999", "END",
                          clock.now_ms(), "G2")
    assert [r["tag_uid"] for r in ioct.unmatched_reads(world.conn, world.ioct_session)] \
        == ["TAG-999"]


def test_aft_session_rejects_pairing(world):
    _, cac = world.cadets["alpha"]
    with pytest.raises(InvalidTransition):
        ioct.pair(world.conn, world.grader, world.aft_session, cac.edipi_hash, "TAG-001")
