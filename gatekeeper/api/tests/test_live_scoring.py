"""PBI-07 L29: the Gatekeeper grades each AFT event from the scoring tables.

The grader sends the raw result plus the cadet's age group and the table column. The Gatekeeper
looks up the points, the Vault saves them with an audit entry in one transaction, and submit
reports the total and pass or fail. Nobody types a score.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")  # synthetic

import scoring  # noqa: E402
import vault_bridge as bridge  # noqa: E402
from live_app import create_live_app, issue_demo_credentials, seed_demo  # noqa: E402
from muster_vault import audit  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v1"}
KEYS = [("mdl_lbs", "MDL"), ("hrp_reps", "HRP"), ("sdc_time", "SDC"),
        ("plank_time", "PLK"), ("twomr_time", "2MR")]
CTX = {"age_group": "17-21", "scoring_column": "M|C"}


def hdr(token):
    return {**H, "Authorization": f"Bearer {token}"}


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class LiveScoringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        self.session, users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(users)
        self.pack = json.loads(PACK.read_text(encoding="utf-8"))
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def count(self, table):
        return self.q(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]

    def post(self, path, role="grader", body=None):
        return self.client.post(path, headers=hdr(self.tokens[role]), json=body)

    def open_event(self, cadet=0):
        r = self.post(f"/v0/sessions/{self.session}/events", body={
            "barcode": self.pack["cadets"][cadet]["barcode_string"]})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["event_id"]

    def comp(self, eid, code, raw, **ctx):
        return self.post(f"/v0/events/{eid}/components",
                         body={"component": code, "raw_value": raw, **(ctx or CTX)})

    def enter_pack_results(self, eid, cadet=0, **override):
        res = dict(self.pack["cadets"][cadet]["aft_results"])
        for key, code in KEYS:
            r = self.comp(eid, code, str(override.get(code, res[key])))
            self.assertEqual(r.status_code, 200, r.get_json())

    # ---- the points come from the tables, not the client -------------------

    def test_points_come_from_the_tables_and_are_saved_with_one_audit_entry(self):
        eid = self.open_event()
        comps, logs = self.count("fitness_component"), self.count("audit_log")
        r = self.comp(eid, "SDC", "1:30")
        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        self.assertEqual((body["points"], body["raw_value"], body["age_group"],
                          body["scoring_column"], body["standards_status"]),
                         (99, "1:30", "17-21", "M|C", "confirmed"))
        row = self.q("SELECT raw_value, points FROM fitness_component WHERE event_id = ?", eid)[0]
        self.assertEqual((row["raw_value"], row["points"]), ("1:30", 99))
        self.assertEqual(self.count("fitness_component"), comps + 1)
        self.assertEqual(self.count("audit_log"), logs + 1)

    def test_a_typed_score_is_refused_and_nothing_is_written(self):
        eid = self.open_event()
        comps, logs = self.count("fitness_component"), self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/components", body={
            "component": "MDL", "raw_value": "340", "points": 100, **CTX})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "validation_failed")
        self.assertIn("'points'", r.get_json()["error"]["message"])
        self.assertEqual((self.count("fitness_component"), self.count("audit_log")), (comps, logs))

    def test_missing_or_bad_scoring_fields_are_400_naming_the_field(self):
        eid = self.open_event()
        comps, logs = self.count("fitness_component"), self.count("audit_log")
        cases = [
            ({"component": "MDL", "raw_value": "340", "scoring_column": "M|C"}, "age_group"),
            ({"component": "MDL", "raw_value": "340", "age_group": "17-21"}, "scoring_column"),
            ({"component": "MDL", "raw_value": "340", "age_group": "18-22", "scoring_column": "M|C"}, "age_group"),
            ({"component": "MDL", "raw_value": "340", "age_group": "17-21", "scoring_column": "M"}, "scoring_column"),
            ({"component": "MDL", "raw_value": "9999", **CTX}, "raw_value"),
            ({"component": "MDL", "raw_value": "abc", **CTX}, "raw_value"),
            ({"component": "SDC", "raw_value": "90", **CTX}, "raw_value"),
            ({"component": "2MR", "raw_value": "", **CTX}, "raw_value"),
        ]
        for body, field in cases:
            r = self.post(f"/v0/events/{eid}/components", body=body)
            self.assertEqual(r.status_code, 400, body)
            err = r.get_json()["error"]
            self.assertEqual(err["code"], "validation_failed")
            self.assertIn(f"'{field}'", err["message"], body)
        self.assertEqual((self.count("fitness_component"), self.count("audit_log")), (comps, logs))

    def test_correcting_a_result_regrades_it_and_logs_the_change(self):
        eid = self.open_event()
        self.assertEqual(self.comp(eid, "MDL", "300").get_json()["points"], 92)
        r = self.comp(eid, "MDL", "340")
        self.assertEqual(r.get_json()["points"], 100)
        actions = [x["action"] for x in self.q(
            "SELECT action FROM audit_log WHERE entity_type = 'fitness_component' "
            "OR entity_id = ? ORDER BY seq", eid)]
        self.assertIn("COMPONENT_CHANGED", actions)

    # ---- submit grades the record -------------------------------------------

    def test_submit_reports_total_and_pass(self):
        eid = self.open_event()
        self.enter_pack_results(eid)
        r = self.post(f"/v0/events/{eid}/submit")
        self.assertEqual(r.status_code, 200, r.get_json())
        g = r.get_json()["grading"]
        # Alpha at 17-21 M|C: MDL 100, HRP 100, SDC 99, PLK 100, 2MR 100
        self.assertEqual((g["total_points"], g["result"], g["failed_events"]), (499, "PASS", []))
        self.assertEqual(g["standards_status"], "confirmed")
        self.assertEqual(r.get_json()["scorecard"]["total_points"], 499)

    def test_an_event_under_the_minimum_fails(self):
        eid = self.open_event()
        self.enter_pack_results(eid, **{"2MR": "22:00"})
        g = self.post(f"/v0/events/{eid}/submit").get_json()["grading"]
        self.assertEqual((g["result"], g["failed_events"]), ("FAIL", ["2MR"]))
        self.assertLess(g["total_points"], 500)

    def test_incomplete_scorecard_cannot_be_submitted(self):
        eid = self.open_event()
        for key, code in KEYS[:4]:
            self.comp(eid, code, str(self.pack["cadets"][0]["aft_results"][key]))
        r = self.post(f"/v0/events/{eid}/submit")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "invalid_transition")

    def test_entered_event_shows_incomplete_grading(self):
        eid = self.open_event()
        self.comp(eid, "MDL", "340")
        conn = bridge.connect(self.db)
        try:
            view = bridge._view(conn, eid)
        finally:
            conn.close()
        self.assertEqual(view["grading"]["result"], "INCOMPLETE")
        self.assertIsNone(view["grading"]["total_points"])

    # ---- PBI-07: 10 cadets, audit PASS, no raw ID anywhere ------------------

    def test_ten_graded_records_then_audit_passes_and_no_raw_id_is_stored(self):
        for i in range(10):
            eid = self.open_event(i)
            self.enter_pack_results(eid, i)
            self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)
        self.assertEqual(self.count("fitness_event"), 10)
        conn = bridge.connect(self.db)
        try:
            self.assertTrue(audit.verify(conn).ok)
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")]
            dump = "\n".join(str(v) for t in tables
                             for row in conn.execute(f'SELECT * FROM "{t}"') for v in tuple(row))
        finally:
            conn.close()
        self.assertNotIn("PDF417_TEST_DATA", dump)
        for c in self.pack["cadets"]:
            self.assertNotIn(c["edipi"], dump)

    def test_if_the_audit_write_fails_the_graded_result_is_not_saved(self):
        eid = self.open_event()
        conn = bridge.connect(self.db)
        conn.execute("CREATE TRIGGER t_break BEFORE INSERT ON audit_log "
                     "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
        conn.close()
        comps = self.count("fitness_component")
        r = self.comp(eid, "MDL", "340")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.count("fitness_component"), comps)

    # ---- who may call -------------------------------------------------------

    def test_standards_endpoint_roles_and_content(self):
        for role in ("grader", "oic", "admin"):
            r = self.client.get("/v0/scoring/standards", headers=hdr(self.tokens[role]))
            self.assertEqual(r.status_code, 200, role)
        self.assertEqual(r.get_json(), scoring.standards_summary())
        for role in ("cadet", "gate_station", "compiler_station"):
            r = self.client.get("/v0/scoring/standards", headers=hdr(self.tokens[role]))
            self.assertEqual(r.status_code, 403, role)
        r = self.client.get("/v0/scoring/standards", headers=H)
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/v0/scoring/standards", headers={**H, "X-Role": "grader"})
        self.assertEqual(r.status_code, 401)  # a role sent by the client is never trusted

    def test_component_write_still_refuses_cadets_and_no_login(self):
        eid = self.open_event()
        logs = self.count("audit_log")
        body = {"component": "MDL", "raw_value": "340", **CTX}
        r = self.client.post(f"/v0/events/{eid}/components", headers=hdr(self.tokens["cadet"]), json=body)
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/v0/events/{eid}/components", headers=H, json=body)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.count("audit_log"), logs)

    def test_v0_and_v1_headers_are_both_accepted_and_others_are_not(self):
        for version, code in (("v0", 200), ("v1", 200), ("v2", 400)):
            r = self.client.get("/v0/scoring/standards", headers={
                "X-Contract-Version": version, "Authorization": f"Bearer {self.tokens['grader']}"})
            self.assertEqual(r.status_code, code, version)


if __name__ == "__main__":
    unittest.main()
