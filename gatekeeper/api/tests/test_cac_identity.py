"""CAC sign-in, card lookup and registration (Sprint 2, advisor meeting 17-Nov).

* the scanner sends the parsed DoD ID (edipi); the Gatekeeper hashes it; nothing raw is stored
* a person picks a position, scans their own card, and the DATABASE decides if that card is
  registered for that position (grader or OIC, or cadet); a mismatch is 403 and logged
* a grader or OIC session opens scorecards; a cadet session reads only its own scores, never writes
* the admin registers people, and the people list holds names and roles but no ID or hash
"""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "synthetic-test-key-not-a-secret-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")  # synthetic

import auth  # noqa: E402
import vault_bridge as bridge  # noqa: E402
from live_app import DEMO_AFT_SESSION, DEMO_IOCT_SESSION, app_tokens, create_live_app, seed_app  # noqa: E402
from muster_vault import audit  # noqa: E402

H = {"X-Contract-Version": "v1"}
GRADER_CARD, OIC_CARD, CADET_A, CADET_B, STRANGER = (
    "9000000011", "9000000012", "9000000001", "9000000002", "9000000013")
CTX = {"age_group": "17-21", "scoring_column": "M|C"}
FRONT = Path(__file__).resolve().parents[3] / "edge" / "scanner" / "tests" / "synthetic_front.json"


@unittest.skipUnless(FRONT.exists(), "Arjo's synthetic cards not found")
class CacIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "app.db"
        seed_app(self.db, synthetic=True)  # fake cards: 01-10 cadets, 11 grader, 12 OIC, 13 unknown
        self.store, self.tokens = app_tokens(self.db)
        self.app = create_live_app(self.db, self.store)
        self.app.config["DEMO_TOKENS"] = self.tokens
        self.app.logger.disabled = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # ---- helpers --------------------------------------------------------------------
    def call(self, method, path, body=None, token=None):
        h = dict(H)
        if token:
            h["Authorization"] = f"Bearer {token}"
        return self.client.open(path, method=method, json=body, headers=h)

    def signin(self, edipi, position):
        return self.call("POST", "/v0/auth/cac-signin", {"edipi": edipi, "position": position})

    def session(self, edipi, position):
        r = self.signin(edipi, position)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["token"]

    def q(self, sql, *params):
        conn = bridge.connect(self.db)
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def count(self, table, where="1=1"):
        return self.q(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")[0]["n"]

    # ---- sign-in: the database decides who is what ----------------------------------------
    def test_a_registered_grader_and_oic_sign_in_as_grader(self):
        for card, role, name in ((GRADER_CARD, "grader", "Kilo, Kai"),
                                 (OIC_CARD, "oic", "De La Lima, Lane")):
            r = self.signin(card, "grader")
            self.assertEqual(r.status_code, 200, r.get_json())
            body = r.get_json()
            self.assertEqual((body["role"], body["name"], body["position"]), (role, name, "grader"))
            self.assertTrue(body["token"] and body["expires_in_s"] > 0)

    def test_a_registered_cadet_signs_in_as_cadet(self):
        r = self.signin(CADET_A, "cadet")
        self.assertEqual((r.status_code, r.get_json()["role"], r.get_json()["name"]),
                         (200, "cadet", "Alpha, Avery"))

    def test_the_wrong_position_or_an_unknown_card_is_refused_and_logged_without_an_id(self):
        before = self.count("audit_log", "action = 'SIGN_IN_REFUSED'")
        attempts = [(CADET_A, "grader"),       # a cadet claims to be a grader
                    (GRADER_CARD, "cadet"),    # a grader claims to be a cadet
                    (STRANGER, "grader"), (STRANGER, "cadet")]  # nobody registered this card
        for card, position in attempts:
            r = self.signin(card, position)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (403, "forbidden_role"),
                             (card, position))
        self.assertEqual(self.count("audit_log", "action = 'SIGN_IN_REFUSED'"), before + 4)
        log = "\n".join(r["data"] for r in self.q("SELECT data FROM audit_log"))
        for card in (CADET_A, GRADER_CARD, STRANGER):
            self.assertNotIn(card, log)
        conn = bridge.connect(self.db)
        try:
            self.assertTrue(audit.verify(conn).ok)
        finally:
            conn.close()

    def test_sign_in_needs_a_ten_digit_id_and_a_known_position(self):
        for body in ({"edipi": "123", "position": "grader"}, {"edipi": GRADER_CARD, "position": "oic"},
                     {"position": "grader"}, {"edipi": GRADER_CARD}):
            r = self.call("POST", "/v0/auth/cac-signin", body)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "validation_failed"))

    def test_a_deactivated_person_cannot_sign_in(self):
        conn = bridge.connect(self.db)
        conn.execute("UPDATE app_user SET active = 0 WHERE role = 'grader'")
        conn.close()
        self.assertEqual(self.signin(GRADER_CARD, "grader").status_code, 403)

    def test_a_session_expires(self):
        token = self.store.issue("short", None, "00" * 32, ttl=0.05)
        time.sleep(0.1)
        r = self.call("GET", "/v0/results", token=token)
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (401, "unauthenticated"))

    def test_sessions_are_never_saved_to_disk(self):
        self.session(GRADER_CARD, "grader")
        path = Path(self.tmp.name) / "creds.json"
        self.store.save(path)
        saved = json.loads(path.read_text(encoding="utf-8"))["credentials"]
        self.assertEqual(sorted(r["label"] for r in saved), ["app admin", "app gate_station"])

    # ---- a signed-in grader works with scanned cadets -----------------------------------------
    def test_a_grader_session_scans_a_cadet_and_records_a_score_from_the_scan(self):
        tok = self.session(GRADER_CARD, "grader")
        look = self.call("POST", "/v0/identity/lookup", {"edipi": CADET_A}, tok).get_json()
        self.assertEqual((look["found"], look["kind"], look["name"]), (True, "cadet", "Alpha, Avery"))
        r = self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": CADET_A}, tok)
        self.assertEqual(r.status_code, 201, r.get_json())
        eid = r.get_json()["event_id"]
        self.assertEqual(r.get_json()["scorecard"]["cadet_name"], "Alpha, Avery")
        for code, raw in (("MDL", "340"), ("HRP", "60"), ("SDC", "1:30"), ("PLK", "3:40"), ("2MR", "13:15")):
            self.assertEqual(self.call("POST", f"/v0/events/{eid}/components",
                                       {"component": code, "raw_value": raw, **CTX}, tok).status_code, 200)
        sub = self.call("POST", f"/v0/events/{eid}/submit", None, tok)
        self.assertEqual((sub.status_code, sub.get_json()["grading"]["result"]), (200, "PASS"))

    def test_lookup_of_an_unregistered_card_says_unknown_and_a_cadet_cannot_look_up(self):
        tok = self.session(OIC_CARD, "grader")
        look = self.call("POST", "/v0/identity/lookup", {"edipi": STRANGER}, tok).get_json()
        self.assertEqual((look["found"], look["kind"], look["name"]), (False, "unknown", None))
        cadet = self.session(CADET_A, "cadet")
        self.assertEqual(self.call("POST", "/v0/identity/lookup", {"edipi": CADET_B}, cadet).status_code, 403)

    def test_edipi_works_for_ioct_pair_and_attendance_too_and_exactly_one_way_is_allowed(self):
        tok = self.session(GRADER_CARD, "grader")
        r = self.call("POST", f"/v0/sessions/{DEMO_IOCT_SESSION}/ioct/pair",
                      {"edipi": CADET_A, "tag_uid": "TAG-017"}, tok)
        self.assertEqual(r.status_code, 201, r.get_json())
        for body in ({}, {"edipi": CADET_A, "barcode": f"PDF417_TEST_DATA_{CADET_A}_END"}):
            r = self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", body, tok)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "validation_failed"))
        r = self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": "12ab"}, tok)
        self.assertEqual(r.status_code, 400)

    # ---- cadet session: view only -----------------------------------------------------------------
    def test_a_cadet_session_reads_only_its_own_scores_and_cannot_write(self):
        grader = self.session(GRADER_CARD, "grader")
        for card in (CADET_A, CADET_B):
            self.assertEqual(self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events",
                                       {"edipi": card}, grader).status_code, 201)
        a = self.call("GET", "/v0/results", token=self.session(CADET_A, "cadet")).get_json()
        b = self.call("GET", "/v0/results", token=self.session(CADET_B, "cadet")).get_json()
        self.assertEqual((a["scope"], a["count"], a["results"][0]["scorecard"]["cadet_name"]),
                         ("own", 1, "Alpha, Avery"))
        self.assertEqual((b["count"], b["results"][0]["scorecard"]["cadet_name"]), (1, "Bravo, Blake"))
        full = self.call("GET", "/v0/results", token=self.session(OIC_CARD, "grader")).get_json()
        self.assertEqual((full["scope"], full["count"]), ("all", 2))
        cadet = self.session(CADET_A, "cadet")
        for path, body in ((f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": CADET_A}),
                           ("/v0/events/1/submit", None), ("/v0/admin/register", {})):
            self.assertEqual(self.call("POST", path, body, cadet).status_code, 403, path)

    # ---- registration (admin) ---------------------------------------------------------------------
    def register(self, edipi, first, last, role, token=None):
        return self.call("POST", "/v0/admin/register",
                         {"edipi": edipi, "first_name": first, "last_name": last, "role": role},
                         token or self.tokens["admin"])

    def test_the_admin_registers_people_and_they_can_then_sign_in_in_their_position(self):
        self.assertEqual(self.register("9000000099", "Pat", "Lee", "grader").status_code, 201)
        self.assertEqual(self.register("9000000098", "Sam", "Roe", "cadet").status_code, 201)
        self.assertEqual(self.signin("9000000099", "grader").get_json()["name"], "Lee, Pat")
        self.assertEqual(self.signin("9000000098", "cadet").get_json()["name"], "Roe, Sam")
        self.assertEqual(self.signin("9000000098", "grader").status_code, 403)

    def test_registration_is_admin_only_refuses_repeats_and_bad_roles(self):
        grader = self.session(GRADER_CARD, "grader")
        self.assertEqual(self.register("9000000099", "Pat", "Lee", "grader", grader).status_code, 403)
        self.assertEqual(self.register(CADET_A, "Avery", "Alpha", "cadet").status_code, 409)
        self.assertEqual(self.register("9000000099", "Pat", "Lee", "admin").status_code, 400)
        self.assertEqual(self.register("9000000099", "", "Lee", "grader").status_code, 400)
        self.assertEqual(self.call("POST", "/v0/admin/register", {}, None).status_code, 401)

    def test_the_people_list_has_names_and_roles_but_no_id_or_hash(self):
        r = self.call("GET", "/v0/admin/people", token=self.tokens["admin"])
        self.assertEqual(r.status_code, 200)
        names = {p["name"]: p["role"] for p in r.get_json()["people"]}
        self.assertEqual((names["Kilo, Kai"], names["De La Lima, Lane"], names["Alpha, Avery"]),
                         ("grader", "oic", "cadet"))
        self.assertNotIn("Mike-Smith, Morgan", names)  # card 13 was left unregistered
        text = r.get_data(as_text=True)
        self.assertIsNone(re.search(r"[0-9a-f]{64}|\d{10}", text))
        self.assertEqual(self.call("GET", "/v0/admin/people", token=self.session(OIC_CARD, "grader")).status_code, 403)

    def test_the_database_holds_no_raw_dod_id(self):
        grader = self.session(GRADER_CARD, "grader")
        self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": CADET_A}, grader)
        self.register("9000000097", "Sky", "Park", "cadet")
        text = "\n".join(str(tuple(r.values())) for t in
                         ("app_user", "cadet", "audit_log", "fitness_event") for r in self.q(f'SELECT * FROM "{t}"'))
        for card in (GRADER_CARD, OIC_CARD, CADET_A, CADET_B, "9000000097"):
            self.assertNotIn(card, text)

    # ---- the pages ---------------------------------------------------------------------------------
    def test_scanner_files_and_demo_tokens_are_served_and_tokens_only_to_the_laptop(self):
        r = self.client.get("/scanner/cac_parse.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("parseCac", r.get_data(as_text=True))
        wasm = self.client.get("/scanner/vendor/zxing-wasm-3.1.5/zxing_reader.wasm")
        self.assertEqual((wasm.status_code, wasm.mimetype), (200, "application/wasm"))
        local = self.client.get("/app/demo_tokens.js").get_data(as_text=True)
        self.assertIn(self.tokens["admin"], local)
        remote = self.client.get("/app/demo_tokens.js", environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
        self.assertNotIn(self.tokens["admin"], remote.get_data(as_text=True))
        self.assertEqual(self.client.get("/scanner/.cert/key.pem").status_code, 404)


if __name__ == "__main__":
    unittest.main()
