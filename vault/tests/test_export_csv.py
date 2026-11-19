"""PBI-10 L32: AMS export v0 (CSV of AFT, IOCT and attendance records) and
PBI-09 L31: attendance follows the same audit rules as fitness (tamper is caught)."""
import csv
import json
import os
import re

import pytest

from muster_vault import attendance, audit, cli, db, export, fitness, ioct  # noqa: F401
from muster_vault.errors import PermissionDenied

from .conftest import TEST_HMAC_KEY, entered_aft, submitted_aft


def _read(folder, name):
    with open(os.path.join(folder, name), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _ioct_run(w, who="charlie", tag="TAG-001"):
    _, cac = w.cadets[who]
    ev = ioct.pair(w.conn, w.grader, w.ioct_session, cac.edipi_hash, tag)
    t = 1_900_000_000_000
    ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "START", t, "START")
    ioct.record_gate_read(w.conn, w.gate, w.ioct_session, tag, "END", t + 155_000, "END")
    ioct.compute_run(w.conn, w.grader, ev)
    fitness.submit(w.conn, w.grader, ev)
    return ev


@pytest.fixture
def records(world):
    """2 counted AFT scores (one is a correction), 1 draft, 1 replaced score, 1 IOCT run,
    3 attendance marks."""
    w = world
    a = submitted_aft(w, "alpha")
    fixed = fitness.supersede(w.conn, w.oic, a, "Wrong deadlift")
    fitness.record_component(w.conn, w.oic, fixed, "MDL", "340", 100)
    fitness.submit(w.conn, w.oic, fixed)
    submitted_aft(w, "bravo")
    entered_aft(w, "charlie")                         # a draft: not exported
    _ioct_run(w)
    sec = attendance.add_section(w.conn, w.admin, "XE401", "G1", "B hour", "2027-1",
                                 w.instructor)
    for name in ("alpha", "bravo", "charlie"):
        attendance.enroll(w.conn, w.admin, sec, w.cadets[name][0])
    for name, st in (("alpha", "PRESENT"), ("bravo", "LATE"), ("charlie", "ABSENT")):
        attendance.mark(w.conn, w.instructor, sec, w.cadets[name][1].edipi_hash,
                        "2026-11-17", st)
    return {"replaced": a, "fixed": fixed}


def test_export_writes_three_csvs_and_a_manifest_with_matching_row_counts(world, records,
                                                                         tmp_path):
    m = export.export_csv(world.conn, world.oic, str(tmp_path))
    assert sorted(os.listdir(m["folder"])) == ["aft.csv", "attendance.csv", "ioct.csv",
                                               "manifest.json"]
    assert {k: v["rows"] for k, v in m["files"].items()} == \
        {"aft.csv": 2, "ioct.csv": 1, "attendance.csv": 3}
    db_counts = world.conn.execute(
        "SELECT (SELECT COUNT(*) FROM fitness_event WHERE test_type='AFT' AND status IN "
        "('SUBMITTED','SIGNED','EXPORTED')), (SELECT COUNT(*) FROM attendance)").fetchone()
    assert (len(_read(m["folder"], "aft.csv")), len(_read(m["folder"], "attendance.csv"))) \
        == tuple(db_counts)
    assert m["scores_left_out"] == {"ENTERED": 1, "SUPERSEDED": 1}
    on_disk = json.loads(open(os.path.join(m["folder"], "manifest.json")).read())
    assert on_disk["files"] == m["files"] and on_disk["integrity_check"].startswith("PASS")


def test_aft_rows_carry_scores_result_and_the_correction_link(world, records, tmp_path):
    m = export.export_csv(world.conn, world.oic, str(tmp_path))
    rows = {int(r["muster_event_id"]): r for r in _read(m["folder"], "aft.csv")}
    assert records["replaced"] not in rows                    # the replaced score is out
    fixed = rows[records["fixed"]]
    assert fixed["corrects_event_id"] == str(records["replaced"])
    assert fixed["MDL_raw"] == "340" and fixed["MDL_points"] == "100"
    assert fixed["total_points"] == str(100 + 75 + 80 + 72 + 85)
    assert fixed["result"] == "PASS" and fixed["last_name"] == "Alpha"
    assert fixed["company"] == "E-2" and fixed["cadet_signed"] == "no"
    entry = audit.entries_for(world.conn, "fitness_event", records["fixed"])[-1]
    assert (fixed["audit_seq"], fixed["audit_entry_hash"]) == (str(entry["seq"]),
                                                               entry["entry_hash"])
    ioct_row = _read(m["folder"], "ioct.csv")[0]
    assert ioct_row["elapsed_ms"] == "155000" and ioct_row["last_name"] == "Charlie"
    att = {r["last_name"]: r["status"] for r in _read(m["folder"], "attendance.csv")}
    assert att == {"Alpha": "PRESENT", "Bravo": "LATE", "Charlie": "ABSENT"}


def test_no_raw_id_and_no_id_hash_in_any_file(world, records, tmp_path):
    m = export.export_csv(world.conn, world.oic, str(tmp_path))
    blob = "".join(open(os.path.join(m["folder"], f), encoding="utf-8").read()
                   for f in os.listdir(m["folder"]))
    for _, cac in world.cadets.values():
        assert cac.edipi not in blob and cac.edipi_hash not in blob
    assert world.grader_cac.edipi not in blob
    # no 10-digit number anywhere, once the batch id and the 64-hex audit hashes (which can
    # contain a run of digits by chance) are taken out
    rest = re.sub(r"[0-9a-f]{64}", "", blob.replace(m["batch_id"], ""))
    assert not re.search(r"(?<!\d)\d{10}(?!\d)", rest)


def test_the_export_is_logged_with_a_hash_of_each_file(world, records, tmp_path):
    m = export.export_csv(world.conn, world.oic, str(tmp_path))
    e = world.conn.execute("SELECT * FROM audit_log WHERE action = 'EXPORT_CSV'").fetchone()
    data = json.loads(e["data"])
    assert data["batch_id"] == m["batch_id"] and data["files"] == m["files"]
    assert audit.verify(world.conn).ok
    # nothing changed status: this is a report, not the signed push to AMS
    assert {r[0] for r in world.conn.execute("SELECT status FROM fitness_event")} == \
        {"SUBMITTED", "SUPERSEDED", "ENTERED"}


def test_a_broken_audit_chain_stops_the_export_with_a_message(world, records, tmp_path):
    world.conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    world.conn.execute("UPDATE audit_log SET data = '{}' WHERE seq = 5")
    with pytest.raises(export.ExportRefused, match="Audit chain broken at entry 5"):
        export.export_csv(world.conn, world.oic, str(tmp_path))
    assert os.listdir(tmp_path) == []
    assert world.conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = "
                              "'EXPORT_REFUSED'").fetchone()[0] == 1


def test_an_edited_score_stops_the_export(world, records, tmp_path):
    world.conn.execute("UPDATE fitness_component SET points = 100 WHERE event_id = ? "
                       "AND component = '2MR'", (records["fixed"],))
    with pytest.raises(export.ExportRefused, match=f"Score {records['fixed']}: 2MR"):
        export.export_csv(world.conn, world.oic, str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_only_the_oic_can_export(world, records, tmp_path):
    for actor in (world.grader, world.instructor, world.station):
        with pytest.raises(PermissionDenied):
            export.export_csv(world.conn, actor, str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_a_name_cannot_turn_into_a_spreadsheet_formula(world, tmp_path):
    from muster_vault import identity
    h = identity.hash_edipi("1000000099", TEST_HMAC_KEY)
    cid = identity.add_cadet(world.conn, world.admin, h, "=HYPERLINK(\"x\")", "Eve")
    sec = attendance.add_section(world.conn, world.admin, "XE401", "G9", "A", "2027-1",
                                 world.instructor)
    attendance.enroll(world.conn, world.admin, sec, cid)
    attendance.mark(world.conn, world.instructor, sec, h, "2026-11-18")
    m = export.export_csv(world.conn, world.oic, str(tmp_path))
    assert _read(m["folder"], "attendance.csv")[0]["last_name"].startswith("'=")


def test_cli_export_csv(tmp_path, monkeypatch, capsys):
    from .conftest import build_world
    path = tmp_path / "v.db"
    monkeypatch.setenv(db.DB_KEY_ENV, db.new_key())
    disk = db.connect(str(path))
    w = build_world(disk)
    submitted_aft(w, "alpha")
    disk.close()
    assert cli.main(["export-csv", "--db", str(path), "--actor", str(w.oic),
                     "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "aft.csv" in out and "1 rows" in out and "PASS" in out


# ---- PBI-09: attendance follows the same audit rules as fitness ------------------------

def test_attendance_edited_added_or_deleted_with_sql_is_caught(world, records):
    assert attendance.check_against_audit(world.conn) == []
    assert fitness.integrity_report(world.conn)["ok"]
    world.conn.execute("UPDATE attendance SET status = 'PRESENT' WHERE status = 'ABSENT'")
    rep = fitness.integrity_report(world.conn)
    assert not rep["ok"]
    assert rep["attendance"][0]["problems"] == ["status: database says PRESENT, audit log "
                                                "says ABSENT"]
    world.conn.execute("DELETE FROM attendance WHERE status = 'LATE'")
    world.conn.execute("INSERT INTO attendance(section_id, cadet_id, class_date, status, "
                       "recorded_by, recorded_at) VALUES (1, 1, '2026-11-18', 'PRESENT', ?, "
                       "'2026-11-18T00:00:00.000Z')", (world.instructor,))
    problems = {p for a in attendance.check_against_audit(world.conn) for p in a["problems"]}
    assert "in the audit log but missing from the database (deleted)" in problems
    assert "in the database but never recorded through MUSTER" in problems


def test_attendance_stores_no_id_only_the_cadet_number(world, records):
    cols = [r[1] for r in world.conn.execute("PRAGMA table_info(attendance)")]
    assert cols == ["id", "section_id", "cadet_id", "class_date", "status", "recorded_by",
                    "recorded_at", "client_ref"]
    blob = json.dumps([dict(r) for r in world.conn.execute("SELECT * FROM attendance")])
    for _, cac in world.cadets.values():
        assert cac.edipi not in blob and cac.edipi_hash not in blob
