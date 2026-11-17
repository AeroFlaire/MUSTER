"""PBI-09 L32: attendance API. Instructor-only writes, limited to their own section (SR-2).

Acceptance (Trello PBI-09):
  * an instructor marks 10 synthetic cadets in their own section: 1 record + 1 audit entry each
  * a write for another instructor's section: 403, 1 rejected-attempt audit entry, 0 records
    (3 of 3 attempts blocked)
  * a cadet who is not on the roster is rejected (3 of 3 attempts)
  * the database holds 0 raw barcodes or raw DoD IDs
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")  # synthetic

import vault_bridge as bridge  # noqa: E402
from live_app import create_live_app, issue_demo_credentials, seed_demo  # noqa: E402
from muster_vault import audit  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v1"}
DAY = "2026-11-17"


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class LiveAttendanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        _, self.users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(self.users)
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()
        self.cadets = json.loads(PACK.read_text(encoding="utf-8"))["cadets"]

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def count(self, table, where="1=1"):
        return self.q(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")[0]["n"]

    def mark(self, i, section=1, role="instructor", status=None, day=DAY, **extra):
        body = {"section_id": section, "class_date": day,
                "barcode": self.cadets[i]["barcode_string"], **extra}
        if status:
            body["status"] = status
        return self.client.post("/v0/attendance/marks", json=body,
                                headers={**H, "Authorization": f"Bearer {self.tokens[role]}"})

    def roster(self, section, role="instructor", day=DAY):
        url = f"/v0/attendance/sections/{section}/roster" + (f"?class_date={day}" if day else "")
        return self.client.get(url, headers={**H, "Authorization": f"Bearer {self.tokens[role]}"})

    # ---- happy path ---------------------------------------------------------------

    def test_instructor_marks_ten_cadets_one_record_and_one_audit_entry_each(self):
        for i in range(10):
            rows, logs = self.count("attendance"), self.count("audit_log")
            r = self.mark(i)
            self.assertEqual(r.status_code, 201, r.get_json())
            self.assertEqual(r.get_json()["status"], "PRESENT")
            self.assertEqual(self.count("attendance"), rows + 1)
            self.assertEqual(self.count("audit_log"), logs + 1)
        self.assertEqual(self.count("audit_log", "action = 'ATTENDANCE_MARKED'"), 10)
        conn = bridge.connect(self.db)
        self.assertTrue(audit.verify(conn).ok)
        conn.close()

    def test_roster_shows_marks_and_only_hashed_identity(self):
        self.mark(0, status="LATE")
        r = self.roster(1)
        self.assertEqual(r.status_code, 200, r.get_json())
        rows = r.get_json()["roster"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["status"], "LATE")
        self.assertEqual(sum(1 for x in rows if x["status"] is None), 9)
        self.assertNotIn("edipi", json.dumps(r.get_json()).lower())

    def test_a_retry_with_the_same_client_ref_does_not_double_write(self):
        a = self.mark(0, client_ref="tap-1").get_json()
        logs = self.count("audit_log")
        b = self.mark(0, client_ref="tap-1").get_json()
        self.assertEqual(a, b)
        self.assertEqual(self.count("audit_log"), logs)
        self.assertEqual(self.count("attendance"), 1)

    def test_a_changed_status_is_a_correction_with_its_own_audit_entry(self):
        self.mark(0, status="PRESENT")
        r = self.mark(0, status="ABSENT")
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(self.count("attendance"), 1)
        self.assertEqual(self.count("audit_log", "action = 'ATTENDANCE_CORRECTED'"), 1)

    # ---- SR-2: another instructor's section ---------------------------------------------

    def test_another_instructors_section_is_blocked_and_logged_3_of_3(self):
        for i in range(3):
            rows, logs = self.count("attendance"), self.count("audit_log")
            r = self.mark(i, section=1, role="instructor_b")  # section 1 belongs to A
            self.assertEqual(r.status_code, 403, r.get_json())
            self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")
            self.assertEqual(self.count("attendance"), rows)       # 0 records
            self.assertEqual(self.count("audit_log"), logs + 1)    # exactly 1 audit entry
        self.assertEqual(self.count("audit_log", "action = 'ATTENDANCE_REJECTED'"), 3)
        conn = bridge.connect(self.db)
        self.assertTrue(audit.verify(conn).ok)  # the chain is intact after the rejections
        conn.close()

    def test_rejected_entry_holds_no_barcode_id_or_cadet_hash(self):
        self.mark(4, section=1, role="instructor_b")
        row = self.q("SELECT data FROM audit_log WHERE action = 'ATTENDANCE_REJECTED'")[0]
        data = json.loads(row["data"])
        self.assertEqual(set(data), {"class_date", "reason"})
        blob = row["data"] + str(self.cadets[4]["edipi"]) + ""
        self.assertNotIn(self.cadets[4]["edipi"], row["data"])
        self.assertNotIn("PDF417", blob)

    def test_unknown_section_is_404(self):
        r = self.mark(0, section=99)
        self.assertEqual(r.status_code, 404, r.get_json())
        self.assertEqual(self.count("attendance"), 0)

    # ---- SR-2: a cadet who is not on the roster -----------------------------------------

    def test_cadet_not_on_the_roster_is_rejected_and_logged_3_of_3(self):
        for i in (3, 5, 7):  # section 2 enrolls only cadet 0
            rows, logs = self.count("attendance"), self.count("audit_log")
            r = self.mark(i, section=2, role="instructor_b")
            self.assertEqual(r.status_code, 403, r.get_json())
            self.assertEqual(self.count("attendance"), rows)
            self.assertEqual(self.count("audit_log"), logs + 1)
        self.assertEqual(self.count("audit_log", "action = 'ATTENDANCE_REJECTED'"), 3)

    def test_a_barcode_for_nobody_is_rejected_and_logged(self):
        logs = self.count("audit_log")
        r = self.client.post("/v0/attendance/marks", headers={
            **H, "Authorization": f"Bearer {self.tokens['instructor']}"},
            json={"section_id": 1, "class_date": DAY,
                  "barcode": "PDF417_TEST_DATA_1234567890_END"})
        self.assertEqual(r.status_code, 403, r.get_json())
        self.assertEqual(self.count("audit_log"), logs + 1)

    def test_roster_of_another_instructors_section_is_blocked_and_logged(self):
        logs = self.count("audit_log")
        r = self.roster(1, role="instructor_b")
        self.assertEqual(r.status_code, 403, r.get_json())
        self.assertEqual(self.count("audit_log"), logs + 1)

    # ---- everyone else ------------------------------------------------------------------

    def test_only_instructors_may_write_and_nothing_reaches_the_vault(self):
        logs = self.count("audit_log")
        for role in ("cadet", "grader", "oic", "admin", "compiler_station", "gate_station"):
            with mock.patch.object(bridge.attendance, "mark") as m:
                r = self.mark(0, role=role)
            self.assertEqual(r.status_code, 403, (role, r.get_json()))
            m.assert_not_called()
        self.assertEqual(self.count("audit_log"), logs)
        self.assertEqual(self.count("attendance"), 0)

    def test_no_token_is_401(self):
        r = self.client.post("/v0/attendance/marks", headers=H, json={})
        self.assertEqual(r.status_code, 401)

    def test_bad_bodies_are_400_and_name_the_field(self):
        auth = {**H, "Authorization": f"Bearer {self.tokens['instructor']}"}
        good = {"section_id": 1, "class_date": DAY,
                "barcode": self.cadets[0]["barcode_string"]}
        cases = [({k: v for k, v in good.items() if k != "section_id"}, "section_id"),
                 ({**good, "class_date": "17-11-2026"}, "class_date"),
                 ({**good, "status": "HERE"}, "status"),
                 ({**good, "barcode": "not a barcode"}, "barcode")]
        for body, field in cases:
            r = self.client.post("/v0/attendance/marks", headers=auth, json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.get_json()["error"]["message"])
        self.assertEqual(self.count("attendance"), 0)

    def test_roster_needs_a_date(self):
        self.assertEqual(self.roster(1, day=None).status_code, 400)

    def test_vault_holds_no_raw_barcode_or_id(self):
        for i in range(10):
            self.mark(i)
        self.mark(0, section=1, role="instructor_b")
        text = "\n".join(str(tuple(r)) for t in ("attendance", "audit_log", "cadet", "enrollment")
                         for r in self.q(f'SELECT * FROM "{t}"'))
        for c in self.cadets:
            self.assertNotIn(c["edipi"], text)
            self.assertNotIn(c["barcode_string"], text)

    # ---- scan marks, roster edit corrects (instructor edits only if needed) ----------------

    def post(self, body, role="instructor"):
        return self.client.post("/v0/attendance/marks", json=body,
                                headers={**H, "Authorization": f"Bearer {self.tokens[role]}"})

    def test_a_scan_marks_present_by_default_and_names_the_cadet(self):
        r = self.post({"section_id": 1, "class_date": DAY, "barcode": self.cadets[0]["barcode_string"]})
        self.assertEqual((r.status_code, r.get_json()["status"]), (201, "PRESENT"))
        self.assertEqual(r.get_json()["cadet_name"], "Alpha, Cadet")
        self.assertEqual(self.roster(1).get_json()["roster"][0]["status"], "PRESENT")

    def test_rescanning_the_same_cadet_writes_nothing_new(self):
        self.mark(0)
        rows, logs = self.count("attendance"), self.count("audit_log")
        self.assertEqual(self.mark(0).status_code, 201)
        self.assertEqual((self.count("attendance"), self.count("audit_log")), (rows, logs))

    def test_the_instructor_can_correct_a_row_from_the_roster_and_the_old_status_is_kept(self):
        self.mark(0)
        cid = self.roster(1).get_json()["roster"][0]["cadet_id"]
        r = self.post({"section_id": 1, "class_date": DAY, "cadet_id": cid, "status": "EXCUSED"})
        self.assertEqual((r.status_code, r.get_json()["status"]), (201, "EXCUSED"))
        self.assertEqual(self.roster(1).get_json()["roster"][0]["status"], "EXCUSED")
        self.assertEqual(self.count("attendance"), 1)  # same row, not a second one
        entry = self.q("SELECT data FROM audit_log WHERE action = 'ATTENDANCE_CORRECTED'")
        self.assertEqual(len(entry), 1)
        self.assertEqual(json.loads(entry[0]["data"])["old_status"], "PRESENT")
        conn = bridge.connect(self.db)
        self.assertTrue(audit.verify(conn).ok)
        conn.close()

    def test_a_correction_for_another_instructors_section_or_an_unknown_cadet_is_refused_and_logged(self):
        self.mark(0)
        rejected = self.count("audit_log", "action = 'ATTENDANCE_REJECTED'")
        for who, body in (("instructor_b", {"section_id": 1, "cadet_id": 1}),
                          ("instructor_b", {"section_id": 2, "cadet_id": 5}),
                          ("instructor", {"section_id": 1, "cadet_id": 9999})):
            r = self.post({"class_date": DAY, "status": "ABSENT", **body}, who)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (403, "forbidden_role"), body)
        self.assertEqual(self.count("audit_log", "action = 'ATTENDANCE_REJECTED'"), rejected + 3)
        self.assertEqual(self.q("SELECT status FROM attendance")[0]["status"], "PRESENT")

    def test_send_the_barcode_or_the_cadet_id_but_not_both_and_not_neither(self):
        base = {"section_id": 1, "class_date": DAY}
        for extra in ({}, {"barcode": self.cadets[0]["barcode_string"], "cadet_id": 1}):
            r = self.post({**base, **extra})
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "validation_failed"))
        self.assertEqual(self.count("attendance"), 0)


if __name__ == "__main__":
    unittest.main()
