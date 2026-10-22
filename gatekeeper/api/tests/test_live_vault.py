"""PBI-04 L24: Gatekeeper writes to the Vault. One record + one audit entry, or nothing.

Runs against a real Vault database (SQLite, built in a temp folder from the Vault's own
schema) loaded with the shared synthetic data pack. No real data anywhere.

  python -m unittest discover -s tests -v      (from gatekeeper/api/)
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")

import vault_bridge as bridge  # noqa: E402  (also puts the Vault on sys.path)
from live_app import create_live_app, seed_demo  # noqa: E402
from muster_vault import audit  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v0"}
COMPONENTS = [("mdl_lbs", "MDL"), ("hrp_reps", "HRP"), ("sdc_time", "SDC"),
              ("plank_time", "PLK"), ("twomr_time", "2MR")]


def hdr(role):
    return {**H, "X-Role": role}


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class LiveVaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        self.session = seed_demo(self.db, PACK)
        self.pack = json.loads(PACK.read_text(encoding="utf-8"))
        app = create_live_app(self.db)
        app.logger.disabled = True  # the forced failures below would print tracebacks
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def count(self, table):
        return self.q(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]

    def post(self, path, role, body=None):
        return self.client.post(path, headers=hdr(role), json=body)

    def open_event(self, cadet=0, role="grader", **extra):
        return self.post(f"/v0/sessions/{self.session}/events", role,
                         {"barcode": self.pack["cadets"][cadet]["barcode_string"], **extra})

    def add_all_components(self, event_id, cadet=0):
        res = self.pack["cadets"][cadet]["aft_results"]
        for key, code in COMPONENTS:
            r = self.post(f"/v0/events/{event_id}/components", "grader",
                          {"component": code, "raw_value": str(res[key]), "points": 100})
            self.assertEqual(r.status_code, 200, r.get_json())

    def break_audit_writes(self):
        """A real database failure: any insert into audit_log now aborts."""
        conn = bridge.connect(self.db)
        conn.execute("CREATE TRIGGER test_break_audit BEFORE INSERT ON audit_log "
                     "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
        conn.close()

    def fix_audit_writes(self):
        conn = bridge.connect(self.db)
        conn.execute("DROP TRIGGER test_break_audit")
        conn.close()

    # ---- acceptance 1: a POST creates one record and one audit entry together ----

    def test_open_event_writes_one_record_and_one_audit_entry(self):
        events, logs = self.count("fitness_event"), self.count("audit_log")
        r = self.open_event()
        self.assertEqual(r.status_code, 201, r.get_json())
        body = r.get_json()
        self.assertEqual(body["status"], "ENTERED")
        self.assertEqual(body["scorecard"]["cadet_name"], "Alpha, Cadet")
        self.assertEqual(self.count("fitness_event"), events + 1)
        self.assertEqual(self.count("audit_log"), logs + 1)
        last = self.q("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1")[0]
        self.assertEqual((last["action"], last["entity_type"], last["entity_id"]),
                         ("EVENT_OPENED", "fitness_event", body["event_id"]))

    def test_component_writes_one_record_and_one_audit_entry(self):
        eid = self.open_event().get_json()["event_id"]
        comps, logs = self.count("fitness_component"), self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/components", "grader",
                      {"component": "MDL", "raw_value": "340", "points": 100})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(self.count("fitness_component"), comps + 1)
        self.assertEqual(self.count("audit_log"), logs + 1)
        self.assertEqual(self.q("SELECT action FROM audit_log ORDER BY seq DESC LIMIT 1")[0]["action"],
                         "COMPONENT_ENTERED")

    def test_submit_changes_status_and_writes_one_audit_entry(self):
        eid = self.open_event().get_json()["event_id"]
        self.add_all_components(eid)
        logs = self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/submit", "grader")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["status"], "SUBMITTED")
        self.assertEqual(r.get_json()["scorecard"]["total_points"], 500)
        self.assertEqual(self.count("audit_log"), logs + 1)

    # ---- acceptance 2: if the audit write fails, no record is saved ---------------

    def test_rollback_open_event_when_audit_fails(self):
        events, logs = self.count("fitness_event"), self.count("audit_log")
        self.break_audit_writes()
        r = self.open_event()
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"]["code"], "internal_error")
        self.assertEqual(self.count("fitness_event"), events, "record survived a failed audit write")
        self.assertEqual(self.count("audit_log"), logs)
        self.fix_audit_writes()
        again = self.open_event()  # the failed attempt left nothing behind
        self.assertEqual(again.status_code, 201, again.get_json())

    def test_rollback_component_when_audit_fails(self):
        eid = self.open_event().get_json()["event_id"]
        comps, logs = self.count("fitness_component"), self.count("audit_log")
        self.break_audit_writes()
        r = self.post(f"/v0/events/{eid}/components", "grader",
                      {"component": "MDL", "raw_value": "340", "points": 100})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.count("fitness_component"), comps)
        self.assertEqual(self.count("audit_log"), logs)

    def test_rollback_submit_when_audit_fails(self):
        eid = self.open_event().get_json()["event_id"]
        self.add_all_components(eid)
        self.break_audit_writes()
        r = self.post(f"/v0/events/{eid}/submit", "grader")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.q("SELECT status FROM fitness_event WHERE id = ?", eid)[0]["status"],
                         "ENTERED", "status changed even though the audit write failed")

    # ---- acceptance 3: the shared synthetic data pack, end to end ------------------

    def test_whole_synthetic_pack(self):
        events0, comps0, logs0 = (self.count(t) for t in ("fitness_event", "fitness_component", "audit_log"))
        for i, cadet in enumerate(self.pack["cadets"]):
            before = self.count("audit_log")
            r = self.open_event(i)
            self.assertEqual(r.status_code, 201, (cadet["fake_name"], r.get_json()))
            self.assertEqual(self.count("audit_log"), before + 1)
            eid = r.get_json()["event_id"]
            self.add_all_components(eid, i)
            r = self.post(f"/v0/events/{eid}/submit", "grader")
            self.assertEqual(r.status_code, 200, r.get_json())
        n = len(self.pack["cadets"])
        self.assertEqual(self.count("fitness_event") - events0, n)
        self.assertEqual(self.count("fitness_component") - comps0, n * 5)
        # per cadet: 1 open + 5 components + 1 submit = 7 audit entries
        self.assertEqual(self.count("audit_log") - logs0, n * 7)
        conn = bridge.connect(self.db)
        result = audit.verify(conn)
        conn.close()
        self.assertTrue(result.ok, str(result))

    # ---- the other rules that keep a record honest ------------------------------------

    def test_same_client_ref_returns_same_event_and_one_audit_entry(self):
        a = self.open_event(client_ref="scan-0001").get_json()["event_id"]
        logs = self.count("audit_log")
        b = self.open_event(client_ref="scan-0001").get_json()["event_id"]
        self.assertEqual(a, b)
        self.assertEqual(self.count("audit_log"), logs)

    def test_second_live_score_for_same_cadet_is_409_and_writes_nothing(self):
        self.open_event()
        events, logs = self.count("fitness_event"), self.count("audit_log")
        r = self.open_event()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"]["code"], "invalid_transition")
        self.assertEqual((self.count("fitness_event"), self.count("audit_log")), (events, logs))

    def test_incomplete_scorecard_cannot_be_submitted(self):
        eid = self.open_event().get_json()["event_id"]
        logs = self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/submit", "grader")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.count("audit_log"), logs)

    def test_wrong_role_writes_nothing(self):
        events, logs = self.count("fitness_event"), self.count("audit_log")
        r = self.open_event(role="compiler_station")
        self.assertEqual(r.status_code, 403)
        self.assertEqual((self.count("fitness_event"), self.count("audit_log")), (events, logs))

    def test_unknown_session_and_unknown_cadet_are_404(self):
        r = self.post("/v0/sessions/999/events", "grader",
                      {"barcode": self.pack["cadets"][0]["barcode_string"]})
        self.assertEqual(r.status_code, 404)
        r = self.post(f"/v0/sessions/{self.session}/events", "grader",
                      {"barcode": "PDF417_TEST_DATA_9999999999_END"})
        self.assertEqual(r.status_code, 404)

    def test_unreadable_barcode_is_400_and_error_does_not_echo_it(self):
        r = self.post(f"/v0/sessions/{self.session}/events", "grader", {"barcode": "garbage-123"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"]["code"], "validation_failed")
        self.assertNotIn("garbage-123", r.get_data(as_text=True))

    def test_no_raw_id_or_barcode_is_stored_or_returned(self):
        r = self.open_event()
        self.assertNotIn("9000000001", r.get_data(as_text=True))
        conn = bridge.connect(self.db)
        dump = "\n".join(conn.iterdump())
        conn.close()
        self.assertNotIn("9000000001", dump)
        self.assertNotIn("PDF417_TEST_DATA", dump)

    def test_hash_matches_the_vaults_own_hash(self):
        from muster_vault import identity
        key = identity.load_edipi_key()
        want = identity.hash_edipi("9000000001", key)
        got = self.open_event().get_json()["scorecard"]["cadet_edipi_hash"]
        self.assertEqual(got, want)

    def test_other_endpoints_still_return_the_stub_examples(self):
        r = self.client.get("/v0/health", headers=hdr("oic"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["stub"])


if __name__ == "__main__":
    unittest.main()
