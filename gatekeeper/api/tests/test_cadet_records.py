"""Cadet records: the OIC looks up any cadet, a cadet sees only their own (Sprint 2 add-on).

* the directory and a cadet's full record are OIC (or admin) only; a grader or a cadet gets 403
* a cadet reads their own record through /v0/me and cannot name another cadet
* the ID hash never appears in any response
* an OIC opening a record leaves one audit entry saying who looked; a cadet's own view does not
* the admin can register a cadet with a company and a class year
"""
import json
import unittest

import test_cac_identity as _t
from test_cac_identity import CADET_A, CADET_B, CTX, DEMO_AFT_SESSION, GRADER_CARD, OIC_CARD


class CadetRecordsTest(_t.CacIdentityTest):
    def _score(self, edipi):
        g = self.session(GRADER_CARD, "grader")
        r = self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": edipi}, g)
        self.assertEqual(r.status_code, 201, r.get_json())
        eid = r.get_json()["event_id"]
        for comp, val in (("MDL", "340"), ("HRP", "60"), ("SDC", "1:30"), ("PLK", "3:40"), ("2MR", "13:15")):
            self.assertEqual(self.call("POST", f"/v0/events/{eid}/components",
                                       {"component": comp, "raw_value": val, **CTX}, g).status_code, 200)
        self.assertEqual(self.call("POST", f"/v0/events/{eid}/submit", None, g).status_code, 200)

    def test_oic_sees_the_directory_with_summaries(self):
        self._score(CADET_A)
        t = self.session(OIC_CARD, "grader")
        r = self.call("GET", "/v0/cadets", token=t)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["count"], 10)
        alpha = next(c for c in body["cadets"] if c["name"] == "Alpha, Avery")
        self.assertEqual(alpha["aft"]["total_points"], 499)
        self.assertEqual(alpha["aft"]["result"], "PASS")
        self.assertIsNone(next(c for c in body["cadets"] if c["name"] == "Bravo, Blake")["aft"])
        self.assertIn(alpha["company"], ("E-2", "F-1"))
        self.assertNotIn("edipi", json.dumps(body).lower())

    def test_grader_and_cadet_cannot_use_the_directory(self):
        for card, pos in ((GRADER_CARD, "grader"), (CADET_A, "cadet")):
            t = self.session(card, pos)
            for path in ("/v0/cadets", "/v0/cadets/1"):
                r = self.call("GET", path, token=t)
                self.assertEqual(r.status_code, 403, (card, path))
        self.assertEqual(self.call("GET", "/v0/cadets").status_code, 401)

    def test_oic_opens_one_record_and_it_is_audited(self):
        self._score(CADET_A)
        t = self.session(OIC_CARD, "grader")
        cid = next(c["cadet_id"] for c in self.call("GET", "/v0/cadets", token=t).get_json()["cadets"]
                   if c["name"] == "Alpha, Avery")
        before = self.count("audit_log", "action = 'CADET_RECORD_VIEWED'")
        r = self.call("GET", f"/v0/cadets/{cid}", token=t)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["cadet"]["name"], "Alpha, Avery")
        self.assertEqual(len(body["results"]), 1)
        self.assertEqual(body["results"][0]["grading"]["total_points"], 499)
        self.assertNotIn("edipi_hash", json.dumps(body))
        self.assertEqual(self.count("audit_log", "action = 'CADET_RECORD_VIEWED'"), before + 1)
        self.assertEqual(self.call("GET", "/v0/cadets/9999", token=t).status_code, 404)

    def test_cadet_sees_only_their_own_record(self):
        self._score(CADET_A)
        a = self.session(CADET_A, "cadet")
        b = self.session(CADET_B, "cadet")
        ra = self.call("GET", "/v0/me", token=a).get_json()
        rb = self.call("GET", "/v0/me", token=b).get_json()
        self.assertEqual(ra["kind"], "cadet")
        self.assertEqual(ra["cadet"]["name"], "Alpha, Avery")
        self.assertEqual(len(ra["results"]), 1)
        self.assertEqual(rb["cadet"]["name"], "Bravo, Blake")
        self.assertEqual(rb["results"], [])
        self.assertNotIn("edipi_hash", json.dumps(ra))
        # their own look-up is not written to the audit log
        self.assertEqual(self.count("audit_log", "action = 'CADET_RECORD_VIEWED'"), 0)

    def test_staff_me_returns_name_and_role_only(self):
        r = self.call("GET", "/v0/me", token=self.session(OIC_CARD, "grader")).get_json()
        self.assertEqual(r["kind"], "staff")
        self.assertEqual(set(r), {"kind", "name", "role"})
        self.assertEqual(r["role"], "oic")

    def test_register_a_cadet_with_company_and_class_year(self):
        body = {"edipi": "9000000099", "first_name": "Zed", "last_name": "Zulu", "role": "cadet",
                "company": "G-3", "class_year": 2027}
        r = self.call("POST", "/v0/admin/register", body, self.tokens["admin"])
        self.assertEqual(r.status_code, 201, r.get_json())
        t = self.session(OIC_CARD, "grader")
        zed = next(c for c in self.call("GET", "/v0/cadets", token=t).get_json()["cadets"]
                   if c["name"] == "Zulu, Zed")
        self.assertEqual((zed["company"], zed["class_year"]), ("G-3", 2027))
        for bad in ({"company": "bad;name!"}, {"class_year": "2027"}, {"class_year": 1999}):
            b2 = {**body, "edipi": "9000000098", **bad}
            self.assertEqual(self.call("POST", "/v0/admin/register", b2, self.tokens["admin"]).status_code,
                             400, bad)



