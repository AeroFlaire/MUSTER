"""Sprint 2 role test (advisor meeting 17-Nov, Demo 3).

What each role may do to a score, checked against a real Vault database:

  grader   can enter and re-enter results while the scorecard is still ENTERED, and can submit.
           After submit the original is locked to them. They cannot reopen or supersede.
  oic      corrects a SUBMITTED or SIGNED score only through supersede: the original is kept
           (marked SUPERSEDED) and a NEW event points back at it. The OIC edits the new event,
           never the original. A SIGNED score cannot be reopened.
  cadet    can only view. Every write returns 403. They see only their own scores.
  grader and oic see every score (GET /v0/results).

Every refused attempt must leave the database exactly as it was, and the audit chain must still
verify at the end.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")  # synthetic

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

import auth  # noqa: E402
import vault_bridge as bridge  # noqa: E402
from live_app import (DEMO_AFT_SESSION, create_live_app, demo_cadet_hashes,  # noqa: E402
                      issue_demo_credentials, seed_demo)
from muster_vault import audit, fitness, identity  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v1"}
KEYS = [("mdl_lbs", "MDL"), ("hrp_reps", "HRP"), ("sdc_time", "SDC"),
        ("plank_time", "PLK"), ("twomr_time", "2MR")]
CTX = {"age_group": "17-21", "scoring_column": "M|C"}
STAFF_ROLES_WITHOUT_READ = ("admin", "instructor", "compiler_station", "gate_station")


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class RolePermissionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        _, self.users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(self.users, demo_cadet_hashes(PACK))
        self.store = store
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()
        self.pack = json.loads(PACK.read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    # ---- helpers ------------------------------------------------------------------

    def head(self, role):
        return {**H, "Authorization": f"Bearer {self.tokens[role]}"}

    def post(self, path, role="grader", body=None):
        return self.client.post(path, headers=self.head(role), json=body)

    def get(self, path, role):
        return self.client.get(path, headers=self.head(role))

    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def open_event(self, cadet=0, role="grader"):
        r = self.post(f"/v0/sessions/{DEMO_AFT_SESSION}/events", role,
                      {"barcode": self.pack["cadets"][cadet]["barcode_string"]})
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["event_id"]

    def comp(self, eid, code, raw, role="grader"):
        return self.post(f"/v0/events/{eid}/components", role,
                         {"component": code, "raw_value": raw, **CTX})

    def enter_all(self, eid, cadet=0, role="grader"):
        res = self.pack["cadets"][cadet]["aft_results"]
        for key, code in KEYS:
            self.assertEqual(self.comp(eid, code, str(res[key]), role).status_code, 200)

    def sign(self, eid, cadet=0):
        """Sign at the compiler station with a throwaway software key (Vault level)."""
        conn = bridge.connect(self.db)
        try:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            h = identity.hash_edipi(self.pack["cadets"][cadet]["edipi"], identity.load_edipi_key())
            identity.enroll_key(conn, self.users["admin"], h, pem)
            ch, payload = fitness.request_cadet_challenge(conn, self.users["compiler_station"], eid)
            fitness.accept(conn, self.users["compiler_station"], eid, ch,
                           key.sign(payload, padding.PKCS1v15(), hashes.SHA256()))
        finally:
            conn.close()

    def snapshot(self):
        """Everything a score is made of, so a refused call can be shown to change nothing."""
        return {
            "events": self.q("SELECT * FROM fitness_event ORDER BY id"),
            "components": self.q("SELECT * FROM fitness_component ORDER BY event_id, component"),
            "audit": self.q("SELECT COUNT(*) AS n FROM audit_log")[0]["n"],
        }

    def components_of(self, eid):
        return self.q("SELECT component, raw_value, points FROM fitness_component "
                      "WHERE event_id = ? ORDER BY component", eid)

    def assert_audit_chain_ok(self):
        conn = bridge.connect(self.db)
        try:
            self.assertTrue(audit.verify(conn).ok)
        finally:
            conn.close()

    def signed_event(self, cadet=0):
        eid = self.open_event(cadet)
        self.enter_all(eid, cadet)
        self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)
        self.sign(eid, cadet)
        self.assertEqual(self.q("SELECT status FROM fitness_event WHERE id = ?", eid)[0]["status"],
                         "SIGNED")
        return eid

    # ---- grader: update before submit, original locked after -------------------------

    def test_grader_can_update_before_submit_and_the_old_value_stays_in_the_audit_log(self):
        eid = self.open_event()
        self.assertEqual(self.comp(eid, "MDL", "300").status_code, 200)
        self.assertEqual(self.comp(eid, "MDL", "340").status_code, 200)  # the grader fixes a typo
        self.assertEqual(self.components_of(eid)[0]["raw_value"], "340")
        entries = self.q("SELECT action, data FROM audit_log WHERE entity_type = 'fitness_event' "
                         "AND entity_id = ? ORDER BY seq", eid)
        changed = [json.loads(e["data"]) for e in entries if e["action"] == "COMPONENT_CHANGED"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["old_raw_value"], "300")  # the first entry is never lost
        self.assert_audit_chain_ok()

    def test_after_submit_the_grader_cannot_change_the_original(self):
        eid = self.open_event()
        self.enter_all(eid)
        self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)
        before = self.snapshot()
        attempts = [
            (f"/v0/events/{eid}/components", {"component": "MDL", "raw_value": "200", **CTX}, 409),
            (f"/v0/events/{eid}/reopen", {"note": "fix it myself"}, 403),
            (f"/v0/events/{eid}/supersede", {"reason": "fix it myself"}, 403),
        ]
        for path, body, status in attempts:
            r = self.post(path, "grader", body)
            self.assertEqual(r.status_code, status, (path, r.get_json()))
        self.assertEqual(self.snapshot(), before)  # not one row or audit entry changed
        self.assert_audit_chain_ok()

    def test_a_signed_score_is_locked_to_the_grader_too(self):
        eid = self.signed_event()
        before = self.snapshot()
        self.assertEqual(self.comp(eid, "HRP", "10").status_code, 409)
        self.assertEqual(self.post(f"/v0/events/{eid}/supersede", "grader",
                                   {"reason": "x"}).status_code, 403)
        self.assertEqual(self.snapshot(), before)

    # ---- OIC: corrects only through supersede, original kept -------------------------

    def test_oic_corrects_a_signed_score_by_superseding_it_never_by_editing_it(self):
        eid = self.signed_event()
        original = self.components_of(eid)
        r = self.post(f"/v0/events/{eid}/supersede", "oic", {"reason": "Wrong deadlift weight"})
        self.assertEqual(r.status_code, 201, r.get_json())
        new_id = r.get_json()["new_event_id"]
        # the original is kept, marked SUPERSEDED, with its results exactly as signed
        self.assertEqual(self.q("SELECT status FROM fitness_event WHERE id = ?", eid)[0]["status"],
                         "SUPERSEDED")
        self.assertEqual(self.components_of(eid), original)
        # the new event points back at it, and the OIC fixes the NEW event
        self.assertEqual(self.q("SELECT supersedes_event_id FROM fitness_event WHERE id = ?",
                                new_id)[0]["supersedes_event_id"], eid)
        self.assertEqual(self.comp(new_id, "MDL", "320", "oic").status_code, 200)
        self.assertEqual([c for c in self.components_of(new_id) if c["component"] == "MDL"][0]
                         ["raw_value"], "320")
        # and still the original has not moved
        self.assertEqual(self.components_of(eid), original)
        self.assert_audit_chain_ok()

    def test_oic_corrects_a_submitted_score_by_superseding_it_and_the_original_never_moves(self):
        """What the app does (signing is not live, so scores stop at SUBMITTED)."""
        eid = self.open_event()
        self.enter_all(eid)
        self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)
        original_row = self.q("SELECT * FROM fitness_event WHERE id = ?", eid)[0]
        original = self.q("SELECT * FROM fitness_component WHERE event_id = ? ORDER BY component", eid)
        r = self.post(f"/v0/events/{eid}/supersede", "oic", {"reason": "Wrong deadlift weight"})
        self.assertEqual(r.status_code, 201, r.get_json())
        new_id = r.get_json()["new_event_id"]
        self.assertEqual(self.comp(new_id, "MDL", "320", "oic").status_code, 200)
        self.assertEqual(self.post(f"/v0/events/{new_id}/submit", "oic").status_code, 200)
        # the original row: only its status changed; its results are byte-for-byte the same
        after = self.q("SELECT * FROM fitness_event WHERE id = ?", eid)[0]
        self.assertEqual(after.pop("status"), "SUPERSEDED")
        original_row.pop("status")
        self.assertEqual(after, original_row)
        self.assertEqual(self.q("SELECT * FROM fitness_component WHERE event_id = ? "
                                "ORDER BY component", eid), original)
        self.assertEqual(self.q("SELECT supersedes_event_id FROM fitness_event WHERE id = ?",
                                new_id)[0]["supersedes_event_id"], eid)
        # a grader still cannot do this, and nobody can edit the original afterwards
        self.assertEqual(self.comp(eid, "MDL", "200", "oic").status_code, 409)
        self.assert_audit_chain_ok()

    def test_the_oic_cannot_change_the_original_after_it_is_superseded_or_signed(self):
        eid = self.signed_event()
        original = self.components_of(eid)
        # signed: no reopen, no direct edit, not even for the OIC
        before = self.snapshot()
        self.assertEqual(self.post(f"/v0/events/{eid}/reopen", "oic", {"note": "x"}).status_code,
                         409)
        self.assertEqual(self.comp(eid, "MDL", "200", "oic").status_code, 409)
        self.assertEqual(self.snapshot(), before)
        # superseded: still no edit of the original
        self.assertEqual(self.post(f"/v0/events/{eid}/supersede", "oic",
                                   {"reason": "fix"}).status_code, 201)
        before = self.snapshot()
        self.assertEqual(self.comp(eid, "MDL", "200", "oic").status_code, 409)
        self.assertEqual(self.post(f"/v0/events/{eid}/supersede", "oic",
                                   {"reason": "again"}).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.components_of(eid), original)

    def test_oic_reopen_before_signing_is_audited(self):
        """Not yet signed: the OIC may send a SUBMITTED score back. The edit is in place, but the
        audit log keeps the old value and the reopen note. Once SIGNED there is no reopen."""
        eid = self.open_event()
        self.enter_all(eid)
        self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)
        self.assertEqual(self.post(f"/v0/events/{eid}/reopen", "oic",
                                   {"note": "wrong plank"}).status_code, 200)
        self.assertEqual(self.comp(eid, "PLK", "3:00", "oic").status_code, 200)
        actions = [e["action"] for e in self.q(
            "SELECT action FROM audit_log WHERE entity_type = 'fitness_event' AND entity_id = ? "
            "ORDER BY seq", eid)]
        self.assertIn("EVENT_REOPENED", actions)
        self.assertIn("COMPONENT_CHANGED", actions)
        self.assert_audit_chain_ok()

    # ---- cadet: view only ---------------------------------------------------------------

    def test_a_cadet_can_only_view_every_write_is_403(self):
        eid = self.open_event()
        self.enter_all(eid)
        before = self.snapshot()
        writes = [
            ("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events",
             {"barcode": self.pack["cadets"][0]["barcode_string"]}),
            ("POST", f"/v0/events/{eid}/components", {"component": "MDL", "raw_value": "1", **CTX}),
            ("POST", f"/v0/events/{eid}/submit", None),
            ("POST", f"/v0/events/{eid}/reopen", {"note": "x"}),
            ("POST", f"/v0/events/{eid}/supersede", {"reason": "x"}),
            ("POST", "/v0/attendance/marks", {"section_id": 1, "class_date": "2026-11-17",
                                              "barcode": self.pack["cadets"][0]["barcode_string"]}),
        ]
        for token in ("cadet", "cadet_2"):
            for method, path, body in writes:
                r = self.client.open(path, method=method, json=body, headers=self.head(token))
                self.assertEqual(r.status_code, 403, (token, path, r.get_json()))
                self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")
        self.assertEqual(self.snapshot(), before)

    # ---- who sees which scores ------------------------------------------------------------

    def three_scores(self):
        for i in range(3):
            eid = self.open_event(i)
            self.enter_all(eid, i)
            self.assertEqual(self.post(f"/v0/events/{eid}/submit").status_code, 200)

    def names(self, r):
        return sorted(x["scorecard"]["cadet_name"] for x in r.get_json()["results"])

    def test_grader_and_oic_see_every_score(self):
        self.three_scores()
        expected = sorted(f"{c['fake_name'].split(' ', 1)[1]}, {c['fake_name'].split(' ', 1)[0]}"
                          for c in self.pack["cadets"][:3])
        for role in ("grader", "oic"):
            r = self.get("/v0/results", role)
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual((r.get_json()["scope"], r.get_json()["count"]), ("all", 3))
            self.assertEqual(self.names(r), expected)
            first = r.get_json()["results"][0]
            self.assertEqual(first["grading"]["total_points"], 499)  # Alpha at 17-21 M|C
            self.assertEqual(first["grading"]["result"], "PASS")

    def test_a_cadet_sees_only_their_own_scores(self):
        self.three_scores()
        a = self.get("/v0/results", "cadet")
        b = self.get("/v0/results", "cadet_2")
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual((a.get_json()["scope"], a.get_json()["count"]), ("own", 1))
        self.assertEqual(self.names(a), ["Alpha, Cadet"])
        self.assertEqual((b.get_json()["scope"], b.get_json()["count"]), ("own", 1))
        self.assertEqual(self.names(b), ["Bravo, Cadet"])
        # the filters only narrow inside their own scores; nothing widens them
        self.assertEqual(self.get("/v0/results?test_type=IOCT", "cadet").get_json()["count"], 0)
        self.assertEqual(self.get(f"/v0/results?session_id={DEMO_AFT_SESSION}",
                                  "cadet").get_json()["count"], 1)
        self.assertEqual(self.get("/v0/results?session_id=999", "cadet").get_json()["count"], 0)
        # a cadet who has no score yet sees an empty list, not someone else's
        store = self.store
        third = demo_cadet_hashes(PACK, 4)[3]
        token = store.issue("cadet four", None, third)
        r = self.client.get("/v0/results", headers={**H, "Authorization": f"Bearer {token}"})
        self.assertEqual((r.status_code, r.get_json()["count"]), (200, 0))

    def test_results_never_contain_an_id_hash_or_a_raw_barcode(self):
        self.three_scores()
        for role in ("grader", "oic", "cadet"):
            text = self.get("/v0/results", role).get_data(as_text=True)
            self.assertIsNone(re.search(r"[0-9a-f]{64}", text), role)
            self.assertNotIn("PDF417", text)
            self.assertNotIn("cadet_edipi_hash", text)

    def test_every_other_role_and_a_cadet_with_no_record_are_refused(self):
        self.three_scores()
        for role in STAFF_ROLES_WITHOUT_READ:
            r = self.get("/v0/results", role)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (403, "forbidden_role"),
                             role)
        orphan = self.store.issue("cadet with no record", None)  # a cadet token tied to nobody
        r = self.client.get("/v0/results", headers={**H, "Authorization": f"Bearer {orphan}"})
        self.assertEqual(r.status_code, 403)

    def test_results_needs_a_token_and_a_good_query(self):
        for headers in (H, {**H, "Authorization": "Bearer nonsense"}, {**H, "X-Role": "oic"}):
            r = self.client.get("/v0/results", headers=headers)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (401, "unauthenticated"))
        for qs in ("test_type=PFT", "session_id=abc"):
            r = self.get(f"/v0/results?{qs}", "grader")
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "validation_failed"))

    def test_results_is_read_only_and_the_credential_file_holds_hashes_only(self):
        self.three_scores()
        before = self.snapshot()
        for role in ("grader", "oic", "cadet"):
            self.get("/v0/results", role)
        self.assertEqual(self.snapshot(), before)  # a read writes nothing, not even an audit entry
        path = Path(self.tmp.name) / "creds.json"
        self.store.save(path)
        text = path.read_text(encoding="utf-8")
        for token in self.tokens.values():
            self.assertNotIn(token, text)
        for c in self.pack["cadets"]:
            self.assertNotIn(c["edipi"], text)
        reloaded = auth.CredentialStore.load(path)
        self.assertEqual(reloaded.lookup(self.tokens["cadet"]).cadet_hash,
                         demo_cadet_hashes(PACK)[0])


if __name__ == "__main__":
    unittest.main()
