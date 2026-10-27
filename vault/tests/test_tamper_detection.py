"""SR-3: an insider who edits score tables directly is caught, signed or not.

Before this check, an edit to a score that was SUBMITTED but not yet signed by the
cadet went unnoticed: the chain only covers the log, and the signature check only
applies once the cadet has signed. Now every score is replayed from its audit
entries and compared with the tables. SYNTHETIC DATA ONLY.
"""
import json

from muster_vault import audit, fitness
from muster_vault.canonical import canonical_text

from .conftest import entered_aft, signed_aft, submitted_aft


def _problems(w, ev):
    r = fitness.verify_event(w.conn, ev)
    return r["ok"], r["problems"]


def test_untouched_scores_pass_at_every_stage(world):
    evs =[entered_aft(world, "alpha"), submitted_aft(world, "bravo"), signed_aft(world, "charlie")]
    for ev in evs:
        assert _problems(world, ev) == (True, [])
    rep = fitness.integrity_report(world.conn)
    assert rep["ok"] and rep["chain"].ok and len(rep["events"]) == 3


def test_edit_to_unsigned_submitted_score_is_caught(world):
    ev = submitted_aft(world)
    world.conn.execute("UPDATE fitness_component SET points = 100 "
                       "WHERE event_id = ? AND component = 'MDL'", (ev,))
    ok, problems = _problems(world, ev)
    assert not ok
    assert problems == ["MDL: database says 200 / 100 pts, audit log says 200 / 70 pts"]
    rep = fitness.integrity_report(world.conn)
    assert rep["chain"].ok and not rep["ok"]  # chain alone would have missed it


def test_edit_while_still_entered_is_caught(world):
    ev = entered_aft(world)
    world.conn.execute("UPDATE fitness_component SET raw_value = '1:20' "
                       "WHERE event_id = ? AND component = 'SDC'", (ev,))
    assert not _problems(world, ev)[0]


def test_legitimate_change_through_muster_is_not_flagged(world):
    ev = entered_aft(world)
    fitness.record_component(world.conn, world.grader, ev, "MDL", "230", 78)
    assert _problems(world, ev) == (True, [])


def test_swapping_the_cadet_is_caught(world):
    ev = submitted_aft(world, "alpha")
    world.conn.execute("UPDATE fitness_event SET cadet_id = ? WHERE id = ?",
                       (world.cadets["bravo"][0], ev))
    ok, problems = _problems(world, ev)
    assert not ok and any("different cadet" in p for p in problems)


def test_deleted_and_injected_results_are_caught(world):
    ev = submitted_aft(world)
    world.conn.execute("DELETE FROM fitness_component WHERE event_id = ? AND component = 'PLK'",
                       (ev,))
    ok, problems = _problems(world, ev)
    assert not ok and any("PLK: in the audit log but missing" in p for p in problems)

    ev2 = entered_aft(world, "bravo")
    world.conn.execute("DELETE FROM fitness_component WHERE event_id = ? AND component = 'HRP'",
                       (ev2,))
    # put back a row with no audit entry behind it
    world.conn.execute("INSERT INTO fitness_component(event_id, component, raw_value, points, "
                       "entered_by, entered_at) VALUES (?, 'HRP', '60', 100, ?, 'x')",
                       (ev2, world.grader))
    ok, problems = _problems(world, ev2)
    assert not ok and any("HRP: database says 60 / 100 pts" in p for p in problems)


def test_covering_tracks_in_the_log_breaks_the_chain(world):
    ev = submitted_aft(world)
    world.conn.execute("UPDATE fitness_component SET points = 100 "
                       "WHERE event_id = ? AND component = 'MDL'", (ev,))
    seq, data = next(
        (e["seq"], json.loads(e["data"])) for e in audit.entries_for(world.conn, "fitness_event", ev)
        if e["action"] == "COMPONENT_ENTERED" and json.loads(e["data"])["component"] == "MDL")
    data["points"] = 100
    world.conn.execute("DROP TRIGGER audit_log_no_update")
    world.conn.execute("UPDATE audit_log SET data = ? WHERE seq = ?", (canonical_text(data), seq))
    assert _problems(world, ev) == (True, [])  # tables and log agree now...
    rep = fitness.integrity_report(world.conn)
    assert not rep["ok"] and rep["chain"].first_bad_seq == seq  # ...but the chain doesn't


def test_correction_starts_from_the_old_scores_logged_results(world):
    old = signed_aft(world)
    new = fitness.supersede(world.conn, world.oic, old, "Data-entry error on 2MR")
    assert _problems(world, new) == (True, [])
    fitness.record_component(world.conn, world.oic, new, "2MR", "15:00", 87)
    assert _problems(world, new) == (True, [])
    world.conn.execute("UPDATE fitness_component SET points = 99 "
                       "WHERE event_id = ? AND component = 'HRP'", (new,))
    assert not _problems(world, new)[0]
