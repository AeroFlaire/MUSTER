import base64
import copy
import hashlib
import json
import re
import unittest
from pathlib import Path

from app import create_app, load_example

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT.parents[1] / "docs" / "interfaces" / "api-contract-v0.md"
H = {"X-Contract-Version": "v0"}
ALL_ROLES = ["admin", "oic", "grader", "compiler_station", "gate_station", "instructor", "arms_nco"]

# (method, path, request example or None, response example, status, allowed roles)
# Written out by hand from contract section 2, not read from app.ROUTES, so a
# mistake in the app cannot hide a mistake in the table.
CASES = [
    ("POST", "/v0/sessions", "session_create_request", "session", 201, {"oic", "admin"}),
    ("POST", "/v0/sessions/7/close", None, "session_closed", 200, {"oic", "admin"}),
    ("POST", "/v0/sessions/7/events", "event_open_request", "aft_event_entered", 201, {"grader", "oic"}),
    ("POST", "/v0/events/11/components", "component_request", "component", 200, {"grader", "oic"}),
    ("POST", "/v0/events/11/submit", None, "aft_event_submitted", 200, {"grader", "oic"}),
    ("POST", "/v0/events/11/cadet-challenge", None, "cadet_challenge", 200, {"compiler_station", "oic"}),
    ("POST", "/v0/events/11/accept", "accept_request", "accept", 200, {"compiler_station", "oic"}),
    ("POST", "/v0/events/11/dispute", {"reason": "Cadet disagrees with plank time"}, "dispute", 200,
     {"compiler_station", "oic"}),
    ("POST", "/v0/events/11/reopen", {"note": "Recheck plank"}, "reopen", 200, {"oic", "admin"}),
    ("POST", "/v0/events/11/supersede", {"reason": "Wrong deadlift weight"}, "supersede", 201,
     {"oic", "admin"}),
    ("GET", "/v0/events/11", None, "aft_event_submitted", 200,
     {"grader", "compiler_station", "oic", "admin"}),
    ("GET", "/v0/events/12", None, "ioct_event_computed", 200,
     {"grader", "compiler_station", "oic", "admin"}),
    ("GET", "/v0/events/11/verify", None, "event_verify", 200, {"oic", "admin"}),
    ("POST", "/v0/sessions/8/ioct/pair", "ioct_pair_request", "ioct_event_paired", 201, {"grader", "oic"}),
    ("POST", "/v0/sessions/8/gate-reads", "gate_read_request", "gate_read", 201, {"gate_station", "oic"}),
    ("POST", "/v0/events/12/ioct/compute", None, "ioct_run", 200, {"grader", "compiler_station", "oic"}),
    ("GET", "/v0/sessions/8/ioct/tags/TAG-017/event", None, "tag_event", 200, {"compiler_station", "oic"}),
    ("GET", "/v0/audit/verify", None, "audit_verify", 200, {"oic", "admin"}),
    ("GET", "/v0/audit/head", None, "audit_head", 200, {"oic", "admin"}),
    ("GET", "/v0/audit/history?entity_type=fitness_event&entity_id=11", None, "audit_history", 200,
     {"oic", "admin"}),
]


def hdr(role, **extra):
    return {**H, "X-Role": role, **extra}


def body_of(req):
    if req is None:
        return None
    return load_example(req) if isinstance(req, str) else req


class StubApiTest(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()

    def call(self, method, path, role, req=None, **extra):
        kwargs = {"headers": hdr(role, **extra)}
        body = body_of(req)
        if body is not None:
            kwargs["json"] = body
        return self.client.open(path, method=method, **kwargs)

    # ---- every endpoint returns its contract example --------------------

    def test_every_endpoint_returns_its_example(self):
        for method, path, req, resp, status, allowed in CASES:
            role = sorted(allowed)[0]
            r = self.call(method, path, role, req)
            self.assertEqual(r.status_code, status, path)
            self.assertEqual(r.get_json(), load_example(resp), path)

    def test_health_open_to_every_role(self):
        for role in ALL_ROLES:
            self.assertEqual(self.call("GET", "/v0/health", role).status_code, 200, role)

    # ---- role matrix -----------------------------------------------------

    def test_role_matrix(self):
        for method, path, req, resp, status, allowed in CASES:
            for role in ALL_ROLES:
                r = self.call(method, path, role, req)
                if role in allowed:
                    self.assertEqual(r.status_code, status, f"{role} {method} {path}")
                else:
                    self.assertEqual(r.status_code, 403, f"{role} {method} {path}")
                    self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")

    def test_missing_unknown_and_old_roles_are_403(self):
        req = load_example("component_request")
        for headers in (H, {**H, "X-Role": ""}, {**H, "X-Role": "cadet"},
                        {**H, "X-Role": "cadre"}, {**H, "X-Role": "timing_gate"}):
            r = self.client.post("/v0/events/11/components", json=req, headers=headers)
            self.assertEqual(r.status_code, 403, headers)
            self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")

    # ---- version header, error shape, append only ------------------------

    def test_missing_or_wrong_contract_version_is_400(self):
        for headers in ({"X-Role": "oic"}, {"X-Role": "oic", "X-Contract-Version": "v1"}):
            r = self.client.get("/v0/audit/verify", headers=headers)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json()["error"]["code"], "unsupported_contract_version")

    def test_error_shape(self):
        err = self.call("GET", "/v0/audit/verify", "grader").get_json()["error"]
        self.assertEqual(set(err), {"code", "message", "request_id"})
        self.assertTrue(err["request_id"].startswith("req_"))
        self.assertEqual(set(load_example("error_example")["error"]), set(err))

    def test_no_update_or_delete_anywhere(self):
        for method in ("PUT", "PATCH", "DELETE"):
            for path in ("/v0/events/11", "/v0/audit/head", "/v0/audit/history", "/v0/sessions"):
                r = self.call(method, path, "oic")
                self.assertEqual(r.status_code, 405, f"{method} {path}")
                self.assertEqual(r.get_json()["error"]["code"], "method_not_allowed")

    def test_unknown_endpoint_is_json(self):
        r = self.call("GET", "/v0/nope", "oic")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    # ---- browser access (CORS) -------------------------------------------

    def test_browser_preflight_and_cors_headers(self):
        # A page opened from disk (Origin: null) sends this before any real call.
        r = self.client.options(
            "/v0/sessions/7/events",
            headers={"Origin": "null", "Access-Control-Request-Method": "POST",
                     "Access-Control-Request-Headers": "content-type,x-contract-version,x-role"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        allowed = r.headers["Access-Control-Allow-Headers"].lower()
        for h in ("content-type", "x-contract-version", "x-role", "x-stub-scenario"):
            self.assertIn(h, allowed)
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])
        # Success and error responses both carry the header, or the page cannot read them.
        ok = self.call("GET", "/v0/health", "oic")
        bad = self.call("GET", "/v0/audit/verify", "grader")
        self.assertEqual((ok.status_code, bad.status_code), (200, 403))
        for resp in (ok, bad):
            self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")

    # ---- ids and state ---------------------------------------------------

    def test_unknown_ids_are_404(self):
        for method, path, req in [
            ("GET", "/v0/events/99", None),
            ("POST", "/v0/events/99/submit", None),
            ("POST", "/v0/sessions/99/close", None),
            ("POST", "/v0/sessions/99/events", "event_open_request"),
            ("GET", "/v0/sessions/8/ioct/tags/TAG-999/event", None),
        ]:
            r = self.call(method, path, "oic", req)
            self.assertEqual(r.status_code, 404, path)
            self.assertEqual(r.get_json()["error"]["code"], "not_found")

    def test_wrong_session_or_event_type_is_409(self):
        for method, path, req in [
            ("POST", "/v0/sessions/8/events", "event_open_request"),      # IOCT session
            ("POST", "/v0/sessions/7/ioct/pair", "ioct_pair_request"),    # AFT session
            ("POST", "/v0/events/12/components", "component_request"),    # IOCT event
            ("POST", "/v0/events/11/ioct/compute", None),                 # AFT event
        ]:
            r = self.call(method, path, "oic", req)
            self.assertEqual(r.status_code, 409, path)
            self.assertEqual(r.get_json()["error"]["code"], "invalid_transition")

    def test_stub_scenarios(self):
        expect = {"not_found": 404, "invalid_transition": 409, "challenge_invalid": 400,
                  "signature_invalid": 400, "forbidden_role": 403, "timing_error": 422, "internal_error": 500}
        for name, status in expect.items():
            r = self.call("POST", "/v0/events/11/accept", "compiler_station", "accept_request",
                          **{"X-Stub-Scenario": name})
            self.assertEqual(r.status_code, status, name)
            self.assertEqual(r.get_json()["error"]["code"], name)
        r = self.call("POST", "/v0/events/11/accept", "compiler_station", "accept_request",
                      **{"X-Stub-Scenario": "bogus"})
        self.assertEqual(r.status_code, 400)
        # A scenario never skips the role check.
        r = self.call("POST", "/v0/events/11/accept", "grader", "accept_request",
                      **{"X-Stub-Scenario": "not_found"})
        self.assertEqual(r.status_code, 403)

    def test_audit_history_for_other_records_is_empty(self):
        r = self.call("GET", "/v0/audit/history?entity_type=fitness_event&entity_id=999", "oic")
        self.assertEqual(r.get_json()["entries"], [])
        r = self.call("GET", "/v0/audit/history", "oic")
        self.assertEqual(r.status_code, 400)

    # ---- validation ------------------------------------------------------

    def test_validation_rejects_bad_requests(self):
        def mutate(example, **changes):
            body = copy.deepcopy(load_example(example))
            for k, v in changes.items():
                if v is KeyError:
                    del body[k]
                else:
                    body[k] = v
            return body

        bad = [
            ("/v0/sessions", "test_type", mutate("session_create_request", test_type="SWIM")),
            ("/v0/sessions", "session_date", mutate("session_create_request", session_date="22-Oct-2026")),
            ("/v0/sessions", "location", mutate("session_create_request", location=KeyError)),
            ("/v0/sessions/7/events", "barcode", mutate("event_open_request", barcode="")),
            ("/v0/sessions/7/events", "barcode", mutate("event_open_request", barcode=KeyError)),
            ("/v0/events/11/components", "component", mutate("component_request", component="PUSHUPS")),
            ("/v0/events/11/components", "raw_value", mutate("component_request", raw_value=340)),
            ("/v0/events/11/components", "points", mutate("component_request", points=101)),
            ("/v0/events/11/accept", "challenge_id", mutate("accept_request", challenge_id="5")),
            ("/v0/events/11/accept", "signature_b64", mutate("accept_request", signature_b64=KeyError)),
            ("/v0/events/11/dispute", "reason", {"reason": "  "}),
            ("/v0/events/11/supersede", "reason", {}),
            ("/v0/sessions/8/ioct/pair", "tag_uid", mutate("ioct_pair_request", tag_uid=KeyError)),
            ("/v0/sessions/8/gate-reads", "gate", mutate("gate_read_request", gate="MIDDLE")),
            ("/v0/sessions/8/gate-reads", "read_at_ms", mutate("gate_read_request", read_at_ms="now")),
        ]
        for path, field, body in bad:
            r = self.client.post(path, json=body, headers=hdr("oic"))
            self.assertEqual(r.status_code, 400, f"{path} {field}")
            err = r.get_json()["error"]
            self.assertEqual(err["code"], "validation_failed")
            self.assertIn(field, err["message"])
        r = self.client.post("/v0/sessions", data="not json", headers=hdr("oic"))
        self.assertEqual(r.status_code, 400)

    # ---- examples are consistent with the Vault design -------------------

    def test_responses_never_contain_a_raw_id_or_barcode(self):
        for path in (ROOT / "examples").glob("*.json"):
            if path.stem.endswith("_request"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("PDF417", text, path.name)
            # Hashes are long hex strings that can hold 10 digits in a row by chance,
            # so take them out before looking for something shaped like a raw EDIPI.
            without_hashes = re.sub(r"[0-9a-f]{64}", "", text)
            self.assertIsNone(re.search(r"(?<!\d)\d{10}(?!\d)", without_hashes), path.name)
        card = load_example("aft_event_submitted")["scorecard"]
        self.assertRegex(card["cadet_edipi_hash"], r"^[0-9a-f]{64}$")

    def test_scorecard_totals_and_order(self):
        card = load_example("aft_event_submitted")["scorecard"]
        names = [c["component"] for c in card["components"]]
        self.assertEqual(names, sorted(names))                       # Vault: ORDER BY component
        self.assertEqual(set(names), {"MDL", "HRP", "SDC", "PLK", "2MR"})
        self.assertEqual(card["total_points"], sum(c["points"] for c in card["components"]))
        entered = load_example("aft_event_entered")["scorecard"]
        self.assertEqual((entered["components"], entered["total_points"]), ([], None))

    def test_ioct_example_matches_test_pack_time(self):
        ioct = load_example("ioct_event_computed")["scorecard"]["ioct"]
        self.assertEqual(ioct["end_read_at_ms"] - ioct["start_read_at_ms"], ioct["elapsed_ms"])
        self.assertEqual(ioct["elapsed_ms"], 155000)                  # test pack "2:35"
        self.assertEqual(ioct["elapsed"], "2:35.00")
        self.assertEqual(load_example("ioct_run")["elapsed_ms"], 155000)

    def test_challenge_payload_signs_the_scorecard(self):
        ch = load_example("cadet_challenge")
        payload = json.loads(base64.b64decode(ch["payload_b64"]))
        self.assertEqual(payload["muster"], "muster-sig-v1")
        self.assertEqual(payload["purpose"], "FITNESS_ACCEPT")
        self.assertEqual(payload["record"], load_example("aft_event_submitted")["scorecard"])

    def test_audit_chain_uses_the_vault_hash_formula(self):
        # Same recipe as vault/muster_vault/canonical.py and audit.compute_entry_hash.
        def canonical(obj):
            return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode("utf-8")

        def entry_hash(e):
            keys = ("seq", "ts", "function", "action", "actor_id", "entity_type",
                    "entity_id", "data", "prev_hash")
            return hashlib.sha256(canonical({k: e[k] for k in keys})).hexdigest()

        entries = load_example("audit_history")["entries"]
        for e in entries:
            self.assertEqual(entry_hash(e), e["entry_hash"], f"seq {e['seq']}")
            self.assertEqual(canonical(json.loads(e["data"])).decode("utf-8"), e["data"])
        self.assertEqual(entries[1]["prev_hash"], entries[0]["entry_hash"])
        verify, head = load_example("audit_verify"), load_example("audit_head")
        self.assertEqual(verify["head_hash"], entries[-1]["entry_hash"])
        self.assertEqual((head["seq"], head["entry_hash"]), (entries[-1]["seq"], verify["head_hash"]))


@unittest.skipUnless(CONTRACT.exists(), "contract file not found at docs/interfaces/")
class ContractMatchesExamplesTest(unittest.TestCase):
    """Every example file must appear in the contract with 0 differences."""

    def test_every_example_is_in_the_contract(self):
        text = CONTRACT.read_text(encoding="utf-8")
        blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S)]
        for path in sorted((ROOT / "examples").glob("*.json")):
            self.assertIn(json.loads(path.read_text(encoding="utf-8")), blocks, path.name)

    def test_every_route_is_in_the_contract_table(self):
        from app import ROUTES

        text = CONTRACT.read_text(encoding="utf-8")
        for method, rule, *_ in ROUTES:
            pretty = re.sub(r"<int:(\w+)>", r"{\1}", rule)
            pretty = re.sub(r"<(\w+)>", r"{\1}", pretty)
            self.assertIn(f"{method} {pretty}", text, rule)


if __name__ == "__main__":
    unittest.main()
