"""Attendance, arms room, receipts, export, identity, and SR-4 data handling."""
import json

import pytest

from muster_vault import (arms, attendance, audit, export, fitness, identity, receipts)
from muster_vault.errors import (InvalidTransition, PermissionDenied, SignatureInvalid,
                                 VaultError)

from .conftest import TEST_HMAC_KEY, dump_all_rows, signed_aft


# ---- Attendance ------------------------------------------------------------

def test_attendance_mark_correct_and_roster(world):
    sec = attendance.add_section(world.conn, world.admin, "XE401", "G1", "B hour",
                                 "2027-1", world.instructor)
    for name in ("alpha", "bravo"):
        attendance.enroll(world.conn, world.admin, sec, world.cadets[name][0])
    a = world.cadets["alpha"][1].edipi_hash
    attendance.mark(world.conn, world.instructor, sec, a, "2026-10-08", client_ref="s1")
    assert attendance.mark(world.conn, world.instructor, sec, a, "2026-10-08",
                           client_ref="s1")  # retry: no duplicate
    attendance.mark(world.conn, world.instructor, sec, a, "2026-10-08", "LATE")
    roster = {r["last_name"]: r["status"] for r in attendance.roster(world.conn, sec,
                                                                     "2026-10-08")}
    assert roster == {"Alpha": "LATE", "Bravo": None}
    actions = [e["action"] for e in audit.entries_for(world.conn, "attendance", 1)]
    assert actions == ["ATTENDANCE_MARKED", "ATTENDANCE_CORRECTED"]


def test_attendance_rejects_unenrolled_and_other_instructor(world):
    sec = attendance.add_section(world.conn, world.admin, "XE401", "G2", "C hour",
                                 "2027-1", world.instructor)
    c = world.cadets["charlie"][1].edipi_hash
    with pytest.raises(PermissionDenied, match="not enrolled"):
        attendance.mark(world.conn, world.instructor, sec, c, "2026-10-08")
    other = identity.add_user(world.conn, world.admin, "Other Instr", "instructor")
    attendance.enroll(world.conn, world.admin, sec, world.cadets["charlie"][0])
    with pytest.raises(PermissionDenied, match="this section's instructor"):
        attendance.mark(world.conn, other, sec, c, "2026-10-08")


# ---- Arms room -------------------------------------------------------------

def test_weapon_out_and_back_in(world):
    arms.add_weapon(world.conn, world.nco, "M4-000123", "M4")
    cid, cac = world.cadets["alpha"]
    ch, payload = arms.request_challenge(world.conn, world.nco, "M4-000123",
                                         cac.edipi_hash, "OUT")
    arms.complete(world.conn, world.nco, ch, "M4-000123", cac.edipi_hash, "OUT",
                  cac.sign(payload))
    assert arms.status_report(world.conn)[0]["state"] == "OUT"

    _, other = world.cadets["bravo"]
    with pytest.raises(InvalidTransition, match="different cadet"):
        arms.request_challenge(world.conn, world.nco, "M4-000123", other.edipi_hash, "IN")

    ch, payload = arms.request_challenge(world.conn, world.nco, "M4-000123",
                                         cac.edipi_hash, "IN")
    arms.complete(world.conn, world.nco, ch, "M4-000123", cac.edipi_hash, "IN",
                  cac.sign(payload))
    assert arms.status_report(world.conn)[0]["state"] == "IN"
    assert audit.verify(world.conn).ok


def test_fitness_signature_cannot_sign_out_a_weapon(world):
    """Purpose is inside the signed bytes, so signatures don't cross functions."""
    arms.add_weapon(world.conn, world.nco, "M17-0042", "M17")
    _, cac = world.cadets["alpha"]
    ch, _payload = arms.request_challenge(world.conn, world.nco, "M17-0042",
                                          cac.edipi_hash, "OUT")
    with pytest.raises(SignatureInvalid):
        arms.complete(world.conn, world.nco, ch, "M17-0042", cac.edipi_hash, "OUT",
                      cac.sign(b'{"purpose":"FITNESS_ACCEPT"}'))


# ---- Receipts and export ---------------------------------------------------

def test_receipt_anchor_matches_log(world):
    ev = signed_aft(world)
    r = receipts.issue(world.conn, world.station, ev)
    assert audit.check_anchor(world.conn, r["audit_anchor"]["seq"],
                              r["audit_anchor"]["entry_hash"])
    text = receipts.render_text(r)
    assert "Alpha, Ann" in text and "Total:     382" in text


def test_export_marks_exported_and_carries_signature(world, tmp_path):
    ev = signed_aft(world)
    result = export.export_signed(world.conn, world.oic, export.JsonFileExporter(str(tmp_path)))
    assert result["exported"] == 1
    assert fitness.get_event(world.conn, ev)["status"] == "EXPORTED"
    batch = json.loads(open(result["reference"]).read())
    item = batch["items"][0]
    assert item["cadet_signature"]["algorithm"] == "RSA-PKCS1v15-SHA256"
    # Receiver can verify independently with only what's in the file:
    import base64
    from muster_vault import signatures
    signatures.verify_signature(item["cadet_signature"]["public_key_pem"],
                                base64.b64decode(item["cadet_signature"]["payload_b64"]),
                                base64.b64decode(item["cadet_signature"]["signature_b64"]),
                                item["cadet_signature"]["algorithm"])
    assert export.export_signed(world.conn, world.oic,
                                export.JsonFileExporter(str(tmp_path)))["exported"] == 0


def test_export_refuses_tampered_scores(world, tmp_path):
    ev = signed_aft(world)
    world.conn.execute("UPDATE fitness_component SET raw_value = '300' WHERE event_id = ? "
                       "AND component = 'MDL'", (ev,))
    with pytest.raises(ValueError, match="integrity check failed"):
        export.export_signed(world.conn, world.oic, export.JsonFileExporter(str(tmp_path)))
    assert fitness.get_event(world.conn, ev)["status"] == "SIGNED"


def test_failed_send_marks_nothing(world):
    ev = signed_aft(world)

    class Down:
        name = "down"

        def send(self, batch):
            raise ConnectionError("AMS unreachable")

    with pytest.raises(ConnectionError):
        export.export_signed(world.conn, world.oic, Down())
    assert fitness.get_event(world.conn, ev)["status"] == "SIGNED"


# ---- Identity / SR-4 -------------------------------------------------------

def test_raw_edipi_never_accepted(world):
    with pytest.raises(ValueError, match="not a raw ID"):
        identity.add_cadet(world.conn, world.admin, "1234567890", "Echo", "Eve")


def test_no_raw_edipi_anywhere_in_database(world):
    signed_aft(world)
    dump = dump_all_rows(world.conn)
    for _, cac in world.cadets.values():
        assert cac.edipi not in dump


def test_hmac_depends_on_secret_key():
    h1 = identity.hash_edipi("1000000001", TEST_HMAC_KEY)
    h2 = identity.hash_edipi("1000000001", b"a-different-secret-key-0123456789abcd")
    assert h1 != h2 and len(h1) == 64


def test_edipi_key_must_be_set(monkeypatch):
    monkeypatch.delenv("MUSTER_EDIPI_KEY", raising=False)
    with pytest.raises(VaultError):
        identity.load_edipi_key()


def test_only_one_bootstrap_admin(world):
    with pytest.raises(PermissionDenied):
        identity.bootstrap_admin(world.conn, "Second admin")


def test_revoked_key_cannot_sign(world):
    from .conftest import submitted_aft
    ev = submitted_aft(world)
    _, cac = world.cadets["alpha"]
    key = identity.active_key(world.conn, cac.edipi_hash)
    identity.revoke_key(world.conn, world.admin, key["id"], "CAC reported lost")
    ch, payload = fitness.request_cadet_challenge(world.conn, world.station, ev)
    with pytest.raises(SignatureInvalid, match="no enrolled key"):
        fitness.accept(world.conn, world.station, ev, ch, cac.sign(payload))
