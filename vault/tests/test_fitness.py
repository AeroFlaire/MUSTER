"""AFT score lifecycle: enter -> submit -> cadet signs (or disputes) -> correction."""
import pytest

from muster_vault import audit, clock, fitness
from muster_vault.errors import (ChallengeError, InvalidTransition, PermissionDenied,
                                 SignatureInvalid)

from .conftest import FakeCAC, entered_aft, signed_aft, submitted_aft


def test_happy_path_aft(world):
    ev = signed_aft(world)
    row = fitness.get_event(world.conn, ev)
    assert row["status"] == "SIGNED"
    card = fitness.scorecard(world.conn, ev)
    assert card["total_points"] == 70 + 75 + 80 + 72 + 85
    assert fitness.verify_event(world.conn, ev)["ok"]
    assert audit.verify(world.conn).ok


def test_cannot_submit_incomplete(world):
    _, cac = world.cadets["alpha"]
    ev = fitness.open_event(world.conn, world.grader, world.aft_session, cac.edipi_hash)
    fitness.record_component(world.conn, world.grader, ev, "MDL", "200", 70)
    with pytest.raises(InvalidTransition, match="missing"):
        fitness.submit(world.conn, world.grader, ev)


def test_components_locked_after_submit(world):
    ev = submitted_aft(world)
    with pytest.raises(InvalidTransition):
        fitness.record_component(world.conn, world.grader, ev, "MDL", "300", 100)


def test_change_while_entered_is_logged_with_old_value(world):
    ev = entered_aft(world)
    fitness.record_component(world.conn, world.grader, ev, "MDL", "210", 72)
    changes = [e for e in audit.entries_for(world.conn, "fitness_event", ev)
               if e["action"] == "COMPONENT_CHANGED"]
    assert len(changes) == 1 and '"old_raw_value":"200"' in changes[0]["data"]


def test_wrong_cadet_signature_rejected(world):
    ev = submitted_aft(world, "alpha")
    _, other = world.cadets["bravo"]
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    with pytest.raises(SignatureInvalid):
        fitness.accept(world.conn, world.station, ev, ch, other.sign(payload))
    assert fitness.get_event(world.conn, ev)["status"] == "SUBMITTED"
    assert any(e["action"] == "SIGNATURE_REJECTED"
               for e in audit.entries_for(world.conn, "fitness_event", ev))


def test_unenrolled_card_rejected(world):
    ev = submitted_aft(world)
    stranger = FakeCAC("9999999999")
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    with pytest.raises(SignatureInvalid):
        fitness.accept(world.conn, world.station, ev, ch, stranger.sign(payload))


def test_challenge_cannot_be_replayed(world):
    ev = submitted_aft(world)
    _, cac = world.cadets["alpha"]
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    sig = cac.sign(payload)
    fitness.accept(world.conn, world.station, ev, ch, sig)
    with pytest.raises(InvalidTransition):  # already SIGNED
        fitness.accept(world.conn, world.station, ev, ch, sig)


def test_signature_cannot_move_to_another_score(world):
    ev_a = submitted_aft(world, "alpha")
    ev_b = submitted_aft(world, "bravo")
    _, cac_a = world.cadets["alpha"]
    ch_a, payload_a = fitness.request_cadet_challenge(world.conn, world.station, ev_a)
    with pytest.raises(ChallengeError):
        fitness.accept(world.conn, world.station, ev_b, ch_a, cac_a.sign(payload_a))


def test_expired_challenge_rejected(world, monkeypatch):
    ev = submitted_aft(world)
    _, cac = world.cadets["alpha"]
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    real = clock.now_ms
    monkeypatch.setattr(clock, "now_ms", lambda: real() + 10 * 60_000)
    with pytest.raises(ChallengeError, match="expired"):
        fitness.accept(world.conn, world.station, ev, ch, cac.sign(payload))


def test_dispute_reopen_resubmit(world):
    ev = submitted_aft(world)
    fitness.dispute(world.conn, world.station, ev, "Plank time was 2:45, not 2:30")
    assert fitness.get_event(world.conn, ev)["status"] == "DISPUTED"
    with pytest.raises(PermissionDenied):
        fitness.reopen(world.conn, world.grader, ev, "grader can't reopen")
    fitness.reopen(world.conn, world.oic, ev, "re-check plank")
    fitness.record_component(world.conn, world.grader, ev, "PLK", "2:45", 78)
    fitness.submit(world.conn, world.grader, ev)
    _, cac = world.cadets["alpha"]
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    fitness.accept(world.conn, world.station, ev, ch, cac.sign(payload))
    assert fitness.get_event(world.conn, ev)["status"] == "SIGNED"


def test_supersede_keeps_old_and_links_new(world):
    old = signed_aft(world)
    new = fitness.supersede(world.conn, world.oic, old, "Data-entry error on 2MR")
    assert fitness.get_event(world.conn, old)["status"] == "SUPERSEDED"
    row = fitness.get_event(world.conn, new)
    assert row["status"] == "ENTERED" and row["supersedes_event_id"] == old
    assert len(fitness.scorecard(world.conn, new)["components"]) == 5
    assert fitness.verify_event(world.conn, old)["ok"]   # old signature still valid


def test_direct_db_edit_after_signing_is_detected(world):
    """Someone edits points straight in SQL, going around the Vault code."""
    ev = signed_aft(world)
    world.conn.execute("UPDATE fitness_component SET points = 100 WHERE event_id = ? "
                       "AND component = '2MR'", (ev,))
    result = fitness.verify_event(world.conn, ev)
    assert not result["ok"]
    assert "differs from what the cadet signed" in result["problems"][0]


def test_one_live_score_per_cadet_per_session(world):
    entered_aft(world, "alpha")
    with pytest.raises(InvalidTransition, match="already has a live score"):
        entered_aft(world, "alpha")


def test_roles_enforced(world):
    _, cac = world.cadets["alpha"]
    with pytest.raises(PermissionDenied):
        fitness.open_event(world.conn, world.station, world.aft_session, cac.edipi_hash)
    ev = submitted_aft(world)
    with pytest.raises(PermissionDenied):
        fitness.request_cadet_challenge(world.conn, world.grader, ev)


def test_optional_grader_signature(world):
    ev = entered_aft(world)
    ch, payload = fitness.request_grader_challenge(world.conn, world.grader, ev)
    fitness.submit(world.conn, world.grader, ev, ch, world.grader_cac.sign(payload))
    row = fitness.get_event(world.conn, ev)
    assert row["grader_attestation_id"] is not None
    _, cac = world.cadets["alpha"]
    ch2, p2 = fitness.request_cadet_challenge(world.conn, world.station, ev)
    fitness.accept(world.conn, world.station, ev, ch2, cac.sign(p2))
    assert fitness.verify_event(world.conn, ev)["ok"]


def test_ecdsa_cards_supported(world):
    from muster_vault import identity
    cac = FakeCAC("1000000009", kind="ec")
    identity.add_cadet(world.conn, world.admin, cac.edipi_hash, "Delta", "Dee")
    identity.enroll_key(world.conn, world.admin, cac.edipi_hash, cac.public_pem)
    ev = fitness.open_event(world.conn, world.grader, world.aft_session, cac.edipi_hash)
    for comp in fitness.AFT_COMPONENTS:
        fitness.record_component(world.conn, world.grader, ev, comp, "1", 60)
    fitness.submit(world.conn, world.grader, ev)
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    fitness.accept(world.conn, world.station, ev, ch, cac.sign(payload))
    assert fitness.verify_event(world.conn, ev)["ok"]
