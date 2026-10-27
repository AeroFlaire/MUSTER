"""IOCT (timing) endpoints are live: pair, gate reads, compute the run time.

Same rules as the AFT writes: one Vault call per POST, record and audit entry written
together or not at all, and the right role for each caller. Uses the shared synthetic
data pack (fake cadets and their IOCT times).
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
from live_app import DEMO_IOCT_SESSION, create_live_app, issue_demo_credentials, seed_demo  # noqa: E402
from muster_vault import audit, clock, ioct  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v0"}


def minutes_to_ms(text):
    m, s = text.split(":")
    return (int(m) * 60 + int(s)) * 1000


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class LiveIoctTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        _, self.users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(self.users)
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()
        self.cadets = json.loads(PACK.read_text(encoding="utf-8"))["cadets"]
        conn = bridge.connect(self.db)  # one wristband per synthetic cadet
        for i in range(len(self.cadets)):
            ioct.register_tag(conn, self.users["admin"], f"BAND-{i:02d}")
        conn.close()
        self.s = DEMO_IOCT_SESSION

    def tearDown(self):
        self.tmp.cleanup()

    def count(self, table):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        finally:
            conn.close()

    def post(self, path, role, body=None):
        return self.client.post(path, headers={**H, "Authorization": f"Bearer {self.tokens[role]}"},
                                json=body)

    def pair(self, i, role="grader", tag=None):
        return self.post(f"/v0/sessions/{self.s}/ioct/pair", role,
                         {"barcode": self.cadets[i]["barcode_string"], "tag_uid": tag or f"BAND-{i:02d}"})

    def gate(self, tag, gate, at_ms, role="gate_station", **extra):
        return self.post(f"/v0/sessions/{self.s}/gate-reads", role,
                         {"tag_uid": tag, "gate": gate, "read_at_ms": at_ms,
                          "gate_device": "G1" if gate == "START" else "G2", **extra})

    def run_cadet(self, i, elapsed_ms=None):
        """Pair, then START and END reads, then compute. Returns (event_id, compute response)."""
        elapsed_ms = elapsed_ms or minutes_to_ms(self.cadets[i]["ioct_time"])
        r = self.pair(i)
        self.assertEqual(r.status_code, 201, r.get_json())
        eid, tag = r.get_json()["event_id"], f"BAND-{i:02d}"
        start = clock.now_ms() + 1000  # simulated gate clock
        self.assertEqual(self.gate(tag, "START", start).status_code, 201)
        self.assertEqual(self.gate(tag, "END", start + elapsed_ms).status_code, 201)
        return eid, self.post(f"/v0/events/{eid}/ioct/compute", "grader")

    # ---- each write: one record, one audit entry --------------------------------------

    def test_gate_read_is_one_record_and_one_audit_entry(self):
        reads, logs = self.count("gate_read"), self.count("audit_log")
        r = self.gate("BAND-00", "START", clock.now_ms())
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertIn("gate_read_id", r.get_json())
        self.assertEqual(self.count("gate_read"), reads + 1)
        self.assertEqual(self.count("audit_log"), logs + 1)

    def test_compute_is_one_run_and_one_audit_entry_and_matches_the_pack(self):
        eid, _ = None, None
        r = self.pair(0)
        eid = r.get_json()["event_id"]
        start = clock.now_ms() + 1000
        self.gate("BAND-00", "START", start)
        self.gate("BAND-00", "END", start + minutes_to_ms(self.cadets[0]["ioct_time"]))
        runs, logs = self.count("ioct_run"), self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/ioct/compute", "grader")
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json(), {"event_id": eid, "elapsed_ms": 155000, "elapsed": "2:35.00"})
        self.assertEqual(self.count("ioct_run"), runs + 1)
        self.assertEqual(self.count("audit_log"), logs + 1)

    def test_whole_synthetic_pack_times(self):
        for i, cadet in enumerate(self.cadets):
            _, r = self.run_cadet(i)
            self.assertEqual(r.status_code, 200, (cadet["fake_name"], r.get_json()))
            self.assertEqual(r.get_json()["elapsed_ms"], minutes_to_ms(cadet["ioct_time"]))
        conn = bridge.connect(self.db)
        result = audit.verify(conn)
        conn.close()
        self.assertTrue(result.ok, str(result))

    def test_submit_after_compute_returns_the_ioct_scorecard(self):
        eid, _ = self.run_cadet(0)
        r = self.post(f"/v0/events/{eid}/submit", "grader")
        self.assertEqual(r.status_code, 200, r.get_json())
        card = r.get_json()["scorecard"]
        self.assertEqual(r.get_json()["status"], "SUBMITTED")
        self.assertEqual(card["ioct"]["elapsed"], "2:35.00")

    def test_retry_with_same_client_ref_does_not_double_write(self):
        t = clock.now_ms()
        a = self.gate("BAND-00", "START", t, client_ref="gate-1").get_json()
        logs = self.count("audit_log")
        b = self.gate("BAND-00", "START", t, client_ref="gate-1").get_json()
        self.assertEqual(a, b)
        self.assertEqual(self.count("audit_log"), logs)

    # ---- who may do what ---------------------------------------------------------------

    def test_roles_on_timing_endpoints(self):
        t = clock.now_ms()
        cases = [
            ("grader posts a gate read", lambda: self.gate("BAND-00", "START", t, role="grader"), 403),
            ("cadet posts a gate read", lambda: self.gate("BAND-00", "START", t, role="cadet"), 403),
            ("gate station pairs a band", lambda: self.pair(0, role="gate_station"), 403),
            ("cadet pairs a band", lambda: self.pair(0, role="cadet"), 403),
            ("compiler station pairs a band", lambda: self.pair(0, role="compiler_station"), 403),
        ]
        for label, call, want in cases:
            before = (self.count("gate_read"), self.count("fitness_event"), self.count("audit_log"))
            r = call()
            self.assertEqual(r.status_code, want, label)
            self.assertEqual(r.get_json()["error"]["code"], "forbidden_role", label)
            self.assertEqual((self.count("gate_read"), self.count("fitness_event"),
                              self.count("audit_log")), before, label)

    def test_gate_station_can_post_reads_and_oic_can_too(self):
        for role in ("gate_station", "oic"):
            self.assertEqual(self.gate("BAND-00", "START", clock.now_ms(), role=role).status_code, 201)

    def test_compiler_station_may_compute(self):
        r = self.pair(0)
        eid = r.get_json()["event_id"]
        start = clock.now_ms() + 1000
        self.gate("BAND-00", "START", start)
        self.gate("BAND-00", "END", start + 155000)
        r = self.post(f"/v0/events/{eid}/ioct/compute", "compiler_station")
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_no_login_never_reaches_the_vault(self):
        for fn in ("ioct_pair", "gate_read", "compute_run"):
            with mock.patch.object(bridge, fn, side_effect=AssertionError("reached the Vault")) as m:
                r = self.client.post(f"/v0/sessions/{self.s}/gate-reads", headers=H,
                                     json={"tag_uid": "BAND-00", "gate": "START", "read_at_ms": 1,
                                           "gate_device": "G1"})
                self.assertEqual(r.status_code, 401)
                m.assert_not_called()

    # ---- failures write nothing --------------------------------------------------------

    def test_forced_audit_failure_stores_no_gate_read(self):
        conn = bridge.connect(self.db)
        conn.execute("CREATE TRIGGER test_break_audit BEFORE INSERT ON audit_log "
                     "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
        conn.close()
        reads, logs = self.count("gate_read"), self.count("audit_log")
        r = self.gate("BAND-00", "START", clock.now_ms())
        self.assertEqual(r.status_code, 500)
        self.assertEqual((self.count("gate_read"), self.count("audit_log")), (reads, logs))

    def test_compute_without_an_end_read_is_422_and_writes_nothing(self):
        eid = self.pair(0).get_json()["event_id"]
        self.gate("BAND-00", "START", clock.now_ms() + 1000)
        runs, logs = self.count("ioct_run"), self.count("audit_log")
        r = self.post(f"/v0/events/{eid}/ioct/compute", "grader")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "timing_error")
        self.assertEqual((self.count("ioct_run"), self.count("audit_log")), (runs, logs))

    def test_a_time_outside_the_allowed_range_is_422(self):
        _, r = self.run_cadet(0, elapsed_ms=30_000)  # 30 seconds
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.get_json()["error"]["code"], "timing_error")

    def test_unknown_wristband_is_404_and_aft_session_is_409(self):
        r = self.pair(0, tag="NOPE-99")
        self.assertEqual(r.status_code, 404)
        r = self.post("/v0/sessions/1/ioct/pair", "grader",
                      {"barcode": self.cadets[0]["barcode_string"], "tag_uid": "BAND-00"})
        self.assertEqual(r.status_code, 409)

    def test_bad_gate_value_is_400(self):
        r = self.post(f"/v0/sessions/{self.s}/gate-reads", "gate_station",
                      {"tag_uid": "BAND-00", "gate": "MIDDLE", "read_at_ms": 1, "gate_device": "G1"})
        self.assertEqual(r.status_code, 400)

    def test_raw_barcode_and_id_never_stored(self):
        self.pair(0)
        conn = bridge.connect(self.db)
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        dump = "\n".join(str(v) for t in tables
                         for row in conn.execute(f'SELECT * FROM "{t}"') for v in tuple(row))
        conn.close()
        self.assertNotIn("9000000001", dump)
        self.assertNotIn("PDF417_TEST_DATA", dump)


if __name__ == "__main__":
    unittest.main()
