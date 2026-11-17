"""PBI-08 L31: IOCT grading and corrections.

* a computed run carries a `grading` block built from the DPE grade scale (every row on the slide,
  men and women; pass = D, TAB = A- or better)
* a correction is a NEW event that points at the original, with its own audit entry, and the
  original event's data is unchanged
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")  # synthetic

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

import scoring  # noqa: E402
import vault_bridge as bridge  # noqa: E402
from live_app import DEMO_AFT_SESSION, DEMO_IOCT_SESSION, create_live_app, issue_demo_credentials, seed_demo  # noqa: E402
from muster_vault import audit, clock, fitness, identity, ioct  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v1"}


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class IoctGradingAndCorrectionsTest(unittest.TestCase):
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

    def post(self, path, role="grader", body=None):
        return self.client.post(path, headers={**H, "Authorization": f"Bearer {self.tokens[role]}"},
                                json=body)

    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def timed_run(self, i=0, elapsed_ms=155_000):
        r = self.post(f"/v0/sessions/{DEMO_IOCT_SESSION}/ioct/pair", body={
            "barcode": self.cadets[i]["barcode_string"], "tag_uid": "TAG-017"})
        self.assertEqual(r.status_code, 201, r.get_json())
        eid = r.get_json()["event_id"]
        start = clock.now_ms() + 1000
        for gate, at in (("START", start), ("END", start + elapsed_ms)):
            self.post(f"/v0/sessions/{DEMO_IOCT_SESSION}/gate-reads", "gate_station", {
                "tag_uid": "TAG-017", "gate": gate, "read_at_ms": at, "gate_device": gate})
        self.assertEqual(self.post(f"/v0/events/{eid}/ioct/compute").status_code, 200)
        return eid

    # ---- grading --------------------------------------------------------------------

    def test_submit_grades_the_run_for_both_columns(self):
        eid = self.timed_run()  # 2:35.00
        r = self.post(f"/v0/events/{eid}/submit")
        g = r.get_json()["grading"]
        self.assertEqual(g["elapsed"], "2:35.00")
        self.assertEqual(g["elapsed_ms"], 155_000)
        self.assertEqual(g["standards_status"], "dpe_grade_scale")
        self.assertEqual(g["by_column"]["M|C"],
                         {"points": 40, "grade": "A-", "result": "PASS", "tab": True})
        self.assertEqual(g["by_column"]["F"],
                         {"points": 50, "grade": "A+", "result": "PASS", "tab": True})

    def test_every_row_of_the_dpe_slide(self):
        """Every band edge on the DPE slide, men then women: (seconds, points, grade)."""
        men = [(0, 50), (146, 50), (147, 45), (153, 45), (154, 40), (158, 40), (159, 35), (162, 35),
               (163, 30), (167, 30), (168, 25), (171, 25), (172, 20), (176, 20), (177, 15), (184, 15),
               (185, 10), (195, 10), (196, 5), (210, 5), (211, 0), (900, 0)]
        women = [(194, 50), (195, 45), (204, 45), (205, 40), (215, 40), (216, 35), (224, 35),
                 (225, 30), (234, 30), (235, 25), (244, 25), (245, 20), (258, 20), (259, 15),
                 (276, 15), (277, 10), (300, 10), (301, 5), (329, 5), (330, 0), (900, 0)]
        for column, cases in (("M|C", men), ("F", women)):
            for sec, points in cases:
                g = scoring.ioct_grade(sec * 1000)["by_column"][column]
                self.assertEqual(g["points"], points, (column, sec))

    def test_pass_is_a_d_and_a_tab_is_an_a_minus(self):
        def g(sec, col):
            return scoring.ioct_grade(sec * 1000)["by_column"][col]
        self.assertEqual((g(210, "M|C")["result"], g(210, "M|C")["tab"]), ("PASS", False))   # 3:30 is a D
        self.assertEqual(g(211, "M|C")["result"], "FAIL")                                     # 3:31 is an F
        self.assertEqual((g(329, "F")["result"], g(329, "F")["tab"]), ("PASS", False))        # 5:29 is a D
        self.assertEqual(g(330, "F")["result"], "FAIL")                                       # 5:30 is an F
        self.assertTrue(g(158, "M|C")["tab"])      # 2:38 is the last A- for men
        self.assertFalse(g(159, "M|C")["tab"])     # 2:39 is a B+
        self.assertTrue(g(215, "F")["tab"])        # 3:35 is the last A- for women
        self.assertFalse(g(216, "F")["tab"])       # 3:36 is a B+

    def test_the_time_is_cut_to_the_whole_second_and_never_changed(self):
        g = scoring.ioct_grade(146_990)  # 2:26.99 is still "2:26 or less"
        self.assertEqual(g["by_column"]["M|C"]["points"], 50)
        self.assertEqual(g["elapsed_ms"], 146_990)

    # ---- corrections ----------------------------------------------------------------

    def sign(self, event_id, cadet_i=0):
        """Sign an event at the compiler station with a throwaway software key (Vault-level)."""
        conn = bridge.connect(self.db)
        try:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            h = identity.hash_edipi(self.cadets[cadet_i]["edipi"], identity.load_edipi_key())
            identity.enroll_key(conn, self.users["admin"], h, pem)
            ch, payload = fitness.request_cadet_challenge(conn, self.users["compiler_station"],
                                                          event_id)
            fitness.accept(conn, self.users["compiler_station"], event_id, ch,
                           key.sign(payload, padding.PKCS1v15(), hashes.SHA256()))
        finally:
            conn.close()

    def test_supersede_keeps_the_original_and_points_the_new_event_at_it(self):
        eid = self.timed_run()
        self.post(f"/v0/events/{eid}/submit")
        self.sign(eid)
        before = [tuple(r) for r in self.q("SELECT * FROM ioct_run WHERE event_id = ?", eid)]
        orig = dict(self.q("SELECT * FROM fitness_event WHERE id = ?", eid)[0])
        logs = self.q("SELECT COUNT(*) AS n FROM audit_log")[0]["n"]

        r = self.post(f"/v0/events/{eid}/supersede", "oic", {"reason": "Gate missed the finish"})
        self.assertEqual(r.status_code, 201, r.get_json())
        new_id = r.get_json()["new_event_id"]
        self.assertEqual(r.get_json()["superseded_event_id"], eid)
        self.assertEqual(r.get_json()["status"], "ENTERED")

        new = dict(self.q("SELECT * FROM fitness_event WHERE id = ?", new_id)[0])
        self.assertEqual(new["supersedes_event_id"], eid)  # points at the original
        after = dict(self.q("SELECT * FROM fitness_event WHERE id = ?", eid)[0])
        changed = {k for k in orig if orig[k] != after[k]}
        self.assertEqual(changed, {"status"})  # only the label moves to SUPERSEDED
        self.assertEqual(after["status"], "SUPERSEDED")
        self.assertEqual([tuple(r) for r in self.q("SELECT * FROM ioct_run WHERE event_id = ?", eid)],
                         before)  # the original time is untouched
        # its own audit entries: the supersede is written as new entries, none were edited
        self.assertGreater(self.q("SELECT COUNT(*) AS n FROM audit_log")[0]["n"], logs)
        self.assertEqual(len(self.q("SELECT 1 FROM audit_log WHERE action = 'EVENT_SUPERSEDED' "
                                    "AND entity_id = ?", eid)), 1)
        conn = bridge.connect(self.db)
        self.assertTrue(audit.verify(conn).ok)
        conn.close()

    def test_corrections_are_oic_only(self):
        eid = self.timed_run()
        self.post(f"/v0/events/{eid}/submit")
        for role in ("grader", "gate_station", "instructor", "cadet", "compiler_station"):
            self.assertEqual(self.post(f"/v0/events/{eid}/supersede", role,
                                       {"reason": "x"}).status_code, 403, role)
            self.assertEqual(self.post(f"/v0/events/{eid}/reopen", role,
                                       {"note": "x"}).status_code, 403, role)

    def test_supersede_needs_a_reason_and_a_signed_score(self):
        eid = self.timed_run()
        self.post(f"/v0/events/{eid}/submit")
        self.assertEqual(self.post(f"/v0/events/{eid}/supersede", "oic", {}).status_code, 400)
        r = self.post(f"/v0/events/{eid}/supersede", "oic", {"reason": "x"})
        self.assertEqual(r.status_code, 409, r.get_json())  # still SUBMITTED: use reopen

    def test_reopen_sends_a_submitted_run_back_with_one_audit_entry(self):
        eid = self.timed_run()
        self.post(f"/v0/events/{eid}/submit")
        logs = self.q("SELECT COUNT(*) AS n FROM audit_log")[0]["n"]
        r = self.post(f"/v0/events/{eid}/reopen", "oic", {"note": "Recheck"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["status"], "ENTERED")
        self.assertEqual(self.q("SELECT COUNT(*) AS n FROM audit_log")[0]["n"], logs + 1)


if __name__ == "__main__":
    unittest.main()
