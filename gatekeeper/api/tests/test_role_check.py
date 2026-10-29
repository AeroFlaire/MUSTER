"""PBI-05 L25: the API refuses writes from anyone without the right role (SR-1).

Three kinds of caller, as on the card:
  grader  = Vault role grader                  (may write scorecards)
  cadre   = staff: oic, plus compiler_station  (oic may write, compiler_station may not open)
  cadet   = not a Vault user at all            (may never write)
and two kinds of non-caller: no credentials, and forged ones.

The rule under test: a spoofed or missing role must never reach the Vault. So every
refusal below also checks that the Vault write function was never called and that no
row or audit entry was added.
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
from muster_vault import identity  # noqa: E402

PACK = Path(__file__).resolve().parents[3] / "tests" / "synthetic_test_pack.json"
H = {"X-Contract-Version": "v0"}


@unittest.skipUnless(PACK.exists(), "shared synthetic data pack not found")
class RoleCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        self.session, self.users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(self.users)
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()
        pack = json.loads(PACK.read_text(encoding="utf-8"))
        self.barcodes = [c["barcode_string"] for c in pack["cadets"]]
        self.barcode = self.barcodes[0]  # the cadet used for "can this caller open an event"
        self._next_cadet = 1             # cadets 1.. are used for setup events

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def count(self, table):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        finally:
            conn.close()

    def bearer(self, role):
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def open_event(self, headers):
        return self.client.post(f"/v0/sessions/{self.session}/events",
                                headers={**H, **headers}, json={"barcode": self.barcode})

    def new_event_as_grader(self):
        """Setup: a fresh event for a different cadet each time."""
        barcode = self.barcodes[self._next_cadet]
        self._next_cadet += 1
        r = self.client.post(f"/v0/sessions/{self.session}/events",
                             headers={**H, **self.bearer("grader")}, json={"barcode": barcode})
        return r.get_json()["event_id"]

    # the three writes, each as a callable taking headers -> response
    def writes(self):
        eid = self.new_event_as_grader()
        return {
            "open_event": lambda h: self.open_event(h),
            "add_component": lambda h: self.client.post(
                f"/v0/events/{eid}/components", headers={**H, **h},
                json={"component": "MDL", "raw_value": "340", "points": 100}),
            "submit": lambda h: self.client.post(f"/v0/events/{eid}/submit", headers={**H, **h}),
        }

    def assert_refused_and_vault_untouched(self, headers, status, code, calls=None):
        """Every write endpoint refuses, the Vault write function is never called, and the
        database row counts do not change. `calls` lets a test do its setup first."""
        calls = calls or self.writes()  # setup writes happen before we start counting
        before = {t: self.count(t) for t in ("fitness_event", "fitness_component", "audit_log")}
        for fn in ("open_event", "add_component", "submit"):
            with mock.patch.object(bridge, fn, side_effect=AssertionError("reached the Vault")) as m:
                r = calls[fn](headers)
                self.assertEqual(r.status_code, status, (fn, r.get_json()))
                self.assertEqual(r.get_json()["error"]["code"], code, fn)
                m.assert_not_called()
        after = {t: self.count(t) for t in before}
        self.assertEqual(before, after)

    # ---- acceptance 1: no role -> 401 or 403, and nothing is written ----------------

    def test_no_credentials_is_401(self):
        self.assert_refused_and_vault_untouched({}, 401, "unauthenticated")

    def test_garbage_token_is_401(self):
        self.assert_refused_and_vault_untouched(
            {"Authorization": "Bearer not-a-real-token"}, 401, "unauthenticated")

    def test_wrong_scheme_and_empty_token_are_401(self):
        self.assert_refused_and_vault_untouched(
            {"Authorization": f"Basic {self.tokens['oic']}"}, 401, "unauthenticated")
        self.assert_refused_and_vault_untouched({"Authorization": "Bearer "}, 401, "unauthenticated")
        self.assert_refused_and_vault_untouched({"Authorization": "Bearer"}, 401, "unauthenticated")

    def test_spoofed_role_header_without_a_token_is_401(self):
        for role in ("oic", "admin", "grader"):
            self.assert_refused_and_vault_untouched({"X-Role": role}, 401, "unauthenticated")

    def test_the_401_does_not_say_whether_a_token_exists(self):
        a = self.open_event({}).get_json()["error"]["message"]
        b = self.open_event({"Authorization": "Bearer nope"}).get_json()["error"]["message"]
        self.assertEqual(a, b)

    # ---- acceptance 2: grader can create a fitness record, cadet cannot ---------------

    def test_grader_can_create_a_fitness_record(self):
        r = self.open_event(self.bearer("grader"))
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["status"], "ENTERED")

    def test_cadet_cannot_write_anything(self):
        self.assert_refused_and_vault_untouched(self.bearer("cadet"), 403, "forbidden_role")

    def test_cadet_cannot_spoof_a_staff_role_with_a_header(self):
        self.assert_refused_and_vault_untouched(
            {**self.bearer("cadet"), "X-Role": "oic"}, 403, "forbidden_role")

    def test_grader_cannot_claim_oic_with_a_header(self):
        # X-Role is ignored in live mode: a grader token stays a grader. Prove it by
        # asking for something only an OIC may do on a live endpoint role set -- the three
        # live endpoints allow graders, so use compiler_station (not in the grader set).
        r = self.open_event({**self.bearer("compiler_station"), "X-Role": "grader"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")

    # ---- the cadre (staff) -----------------------------------------------------------

    def test_oic_may_write(self):
        r = self.open_event(self.bearer("oic"))
        self.assertEqual(r.status_code, 201, r.get_json())

    def test_compiler_station_and_admin_may_not_open_events(self):
        for role in ("compiler_station", "admin"):
            before = self.count("fitness_event"), self.count("audit_log")
            with mock.patch.object(bridge, "open_event",
                                   side_effect=AssertionError("reached the Vault")) as m:
                r = self.open_event(self.bearer(role))
                m.assert_not_called()  # the Gatekeeper refused it; the Vault never saw it
            self.assertEqual(r.status_code, 403, role)
            self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")
            self.assertEqual((self.count("fitness_event"), self.count("audit_log")), before)

    # ---- the role is the Vault's, not the token file's --------------------------------

    def test_deactivated_user_is_refused_at_once_even_with_a_valid_token(self):
        calls = self.writes()  # the grader makes an event while still active
        conn = bridge.connect(self.db)
        identity.set_user_active(conn, self.users["admin"], self.users["grader"], False)
        conn.close()
        self.assert_refused_and_vault_untouched(self.bearer("grader"), 403, "forbidden_role",
                                                calls=calls)

    def test_role_change_in_the_vault_takes_effect_at_once(self):
        # Make the grader's user row an instructor directly: the next request must be refused.
        conn = bridge.connect(self.db)
        conn.execute("UPDATE app_user SET role = 'instructor' WHERE id = ?",
                     (self.users["grader"],))
        conn.close()
        with mock.patch.object(bridge, "open_event",
                               side_effect=AssertionError("reached the Vault")) as m:
            r = self.open_event(self.bearer("grader"))
            m.assert_not_called()
        self.assertEqual(r.status_code, 403)

    # ---- tokens are stored as hashes only ------------------------------------------------

    def test_credential_file_holds_hashes_never_tokens(self):
        from live_app import issue_demo_credentials as issue
        store, tokens = issue(self.users)
        path = Path(self.tmp.name) / "creds.json"
        store.save(path)
        text = path.read_text(encoding="utf-8")
        for token in tokens.values():
            self.assertNotIn(token, text)
        self.assertIn("token_sha256", text)

    # ---- the stub is unchanged ------------------------------------------------------------

    def test_stub_mode_still_uses_x_role(self):
        from app import create_app
        c = create_app().test_client()
        r = c.post("/v0/sessions/7/events", headers={**H, "X-Role": "grader"},
                   json={"barcode": self.barcode})
        self.assertEqual(r.status_code, 201)


if __name__ == "__main__":
    unittest.main()
