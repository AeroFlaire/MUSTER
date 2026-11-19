"""Graders start new AFT and IOCT sessions; only the OIC can change a saved score.

* a grader opens a session (201); a cadet cannot (403)
* the session list shows it, newest first
* a cadet who already has a score gets a fresh score in the new session
* a grader still cannot reopen or supersede a saved score (403); the OIC can
"""
import test_cac_identity as _t
from test_cac_identity import CADET_A, CTX, DEMO_AFT_SESSION, GRADER_CARD, OIC_CARD

NEW = {"test_type": "AFT", "session_date": "2026-11-19", "location": "Retake Gym"}


class SessionsTest(_t.CacIdentityTest):
    def _score(self, token, session, edipi=CADET_A):
        r = self.call("POST", f"/v0/sessions/{session}/events", {"edipi": edipi}, token)
        self.assertEqual(r.status_code, 201, r.get_json())
        eid = r.get_json()["event_id"]
        for comp, val in (("MDL", "340"), ("HRP", "60"), ("SDC", "1:30"), ("PLK", "3:40"), ("2MR", "13:15")):
            self.assertEqual(self.call("POST", f"/v0/events/{eid}/components",
                                       {"component": comp, "raw_value": val, **CTX}, token).status_code, 200)
        self.assertEqual(self.call("POST", f"/v0/events/{eid}/submit", None, token).status_code, 200)
        return eid

    def test_grader_starts_a_session_and_retakes_in_it(self):
        g = self.session(GRADER_CARD, "grader")
        first = self._score(g, DEMO_AFT_SESSION)
        again = self.call("POST", f"/v0/sessions/{DEMO_AFT_SESSION}/events", {"edipi": CADET_A}, g)
        self.assertEqual(again.status_code, 409)
        r = self.call("POST", "/v0/sessions", NEW, g)
        self.assertEqual(r.status_code, 201, r.get_json())
        sid = r.get_json()["session_id"]
        self.assertEqual(r.get_json()["location"], "Retake Gym")
        second = self._score(g, sid)
        self.assertNotEqual(first, second)
        listing = self.call("GET", "/v0/sessions", token=g).get_json()
        self.assertEqual(listing["sessions"][0]["session_id"], sid)
        self.assertIsNone(listing["sessions"][0]["closed_at"])

    def test_cadet_cannot_start_or_list_sessions(self):
        c = self.session(CADET_A, "cadet")
        self.assertEqual(self.call("POST", "/v0/sessions", NEW, c).status_code, 403)
        self.assertEqual(self.call("GET", "/v0/sessions", token=c).status_code, 403)
        self.assertEqual(self.call("GET", "/v0/sessions").status_code, 401)

    def test_only_the_oic_changes_a_saved_score(self):
        g = self.session(GRADER_CARD, "grader")
        eid = self._score(g, DEMO_AFT_SESSION)
        self.assertEqual(self.call("POST", f"/v0/events/{eid}/reopen", {"note": "x"}, g).status_code, 403)
        self.assertEqual(self.call("POST", f"/v0/events/{eid}/supersede", {"reason": "x"}, g).status_code, 403)
        o = self.session(OIC_CARD, "grader")
        self.assertEqual(self.call("POST", f"/v0/events/{eid}/reopen", {"note": "fix"}, o).status_code, 200)


if __name__ == "__main__":
    import unittest
    unittest.main()
