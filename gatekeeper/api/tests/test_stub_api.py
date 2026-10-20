import copy
import json
import re
import unittest
from pathlib import Path

from app import create_app, load_example

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT.parents[1] / "docs" / "interfaces" / "api-contract-v0.md"

H = {"X-Contract-Version": "v0"}


def hdr(role, **extra):
    return {**H, "X-Role": role, **extra}


class StubApiTest(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()
        self.record = load_example("fitness_record_response")
        self.cadet = self.record["cadet_id_hash"]

    # ---- every endpoint returns its contract example -------------------

    def test_post_fitness_record_returns_example(self):
        r = self.client.post(
            "/v0/fitness-records",
            json=load_example("fitness_record_request"),
            headers=hdr("grader"),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json(), self.record)

    def test_get_fitness_record_returns_example(self):
        r = self.client.get(
            f"/v0/fitness-records/{self.record['record_id']}", headers=hdr("cadre")
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), self.record)

    def test_list_cadet_records_returns_example(self):
        r = self.client.get(
            f"/v0/cadets/{self.cadet}/fitness-records", headers=hdr("grader")
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.get_json(), {"cadet_id_hash": self.cadet, "records": [self.record]}
        )

    def test_post_ioct_timing_returns_example(self):
        r = self.client.post(
            "/v0/ioct-timings",
            json=load_example("ioct_timing_request"),
            headers=hdr("timing_gate"),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json(), load_example("ioct_timing_response"))

    def test_audit_events_returns_example(self):
        r = self.client.get("/v0/audit/events", headers=hdr("cadre"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.get_json(), {"events": [load_example("audit_event")], "next_seq": None}
        )

    def test_audit_verify_returns_pass(self):
        r = self.client.get("/v0/audit/verify", headers=hdr("cadre"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"result": "PASS", "entries_checked": 42})

    def test_health_open_to_all_roles(self):
        for role in ("grader", "cadre", "cadet", "timing_gate"):
            r = self.client.get("/v0/health", headers=hdr(role))
            self.assertEqual(r.status_code, 200, role)

    # ---- role matrix (contract section 2) -------------------------------

    def test_role_matrix(self):
        rid = self.record["record_id"]
        cases = [
            ("POST", "/v0/fitness-records", {"grader"}),
            ("GET", f"/v0/fitness-records/{rid}", {"grader", "cadre", "cadet"}),
            ("GET", f"/v0/cadets/{self.cadet}/fitness-records", {"grader", "cadre", "cadet"}),
            ("POST", "/v0/ioct-timings", {"timing_gate"}),
            ("GET", "/v0/audit/events", {"cadre"}),
            ("GET", "/v0/audit/verify", {"cadre"}),
        ]
        bodies = {
            "/v0/fitness-records": load_example("fitness_record_request"),
            "/v0/ioct-timings": load_example("ioct_timing_request"),
        }
        for method, path, allowed in cases:
            for role in ("grader", "cadre", "cadet", "timing_gate"):
                extra = {"X-Cadet-Id-Hash": self.cadet} if role == "cadet" else {}
                kwargs = {"headers": hdr(role, **extra)}
                if method == "POST":
                    kwargs["json"] = bodies[path]
                r = self.client.open(path, method=method, **kwargs)
                if role in allowed:
                    self.assertIn(r.status_code, (200, 201), f"{role} {method} {path}")
                else:
                    self.assertEqual(r.status_code, 403, f"{role} {method} {path}")
                    self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")

    def test_missing_and_unknown_role_are_403(self):
        for headers in (H, {**H, "X-Role": "admin"}, {**H, "X-Role": ""}):
            r = self.client.post(
                "/v0/fitness-records",
                json=load_example("fitness_record_request"),
                headers=headers,
            )
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.get_json()["error"]["code"], "forbidden_role")

    def test_cadet_sees_only_own_record(self):
        rid = self.record["record_id"]
        mine = self.client.get(
            f"/v0/fitness-records/{rid}",
            headers=hdr("cadet", **{"X-Cadet-Id-Hash": self.cadet}),
        )
        self.assertEqual(mine.status_code, 200)
        other = self.client.get(
            f"/v0/fitness-records/{rid}",
            headers=hdr("cadet", **{"X-Cadet-Id-Hash": "0" * 64}),
        )
        self.assertEqual(other.status_code, 404)  # 404, not 403: no existence leak
        self.assertEqual(other.get_json()["error"]["code"], "record_not_found")

    # ---- version header, errors, append-only ----------------------------

    def test_missing_or_wrong_contract_version_is_400(self):
        for headers in ({"X-Role": "cadre"}, {"X-Role": "cadre", "X-Contract-Version": "v1"}):
            r = self.client.get("/v0/audit/verify", headers=headers)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json()["error"]["code"], "unsupported_contract_version")

    def test_error_shape(self):
        r = self.client.get("/v0/audit/verify", headers=hdr("cadet"))
        err = r.get_json()["error"]
        self.assertEqual(set(err), {"code", "message", "request_id"})
        self.assertTrue(err["request_id"].startswith("req_"))

    def test_no_update_or_delete_anywhere(self):
        rid = self.record["record_id"]
        for method in ("PUT", "PATCH", "DELETE"):
            for path in (f"/v0/fitness-records/{rid}", "/v0/fitness-records", "/v0/audit/events"):
                r = self.client.open(path, method=method, headers=hdr("grader"))
                self.assertEqual(r.status_code, 405, f"{method} {path}")
                self.assertEqual(r.get_json()["error"]["code"], "method_not_allowed")

    def test_unknown_endpoint_is_json(self):
        r = self.client.get("/v0/nope", headers=hdr("cadre"))
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    # ---- validation -----------------------------------------------------

    def post_record(self, body):
        return self.client.post("/v0/fitness-records", json=body, headers=hdr("grader"))

    def test_validation_rejects_bad_requests(self):
        good = load_example("fitness_record_request")

        def without(*path):
            body = copy.deepcopy(good)
            node = body
            for key in path[:-1]:
                node = node[key]
            del node[path[-1]]
            return body

        def with_value(path, value):
            body = copy.deepcopy(good)
            node = body
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            return body

        bad_bodies = [
            ("idempotency_key", without("idempotency_key")),
            ("cadet_id_hash", without("cadet_id_hash")),
            ("cadet_id_hash", with_value(("cadet_id_hash",), "123456789")),  # raw-ID shaped
            ("test_type", with_value(("test_type",), "SWIM")),
            ("test_date", with_value(("test_date",), "16-Oct-2026")),
            ("measurements.plank_seconds", without("measurements", "plank_seconds")),
            ("measurements.deadlift_lbs", with_value(("measurements", "deadlift_lbs"), "heavy")),
        ]
        for field, body in bad_bodies:
            r = self.post_record(body)
            self.assertEqual(r.status_code, 400, field)
            err = r.get_json()["error"]
            self.assertEqual(err["code"], "validation_failed")
            self.assertIn(field, err["message"])

        r = self.client.post(
            "/v0/fitness-records", data="not json", headers=hdr("grader")
        )
        self.assertEqual(r.status_code, 400)

    def test_timing_rejects_finish_before_start(self):
        body = load_example("ioct_timing_request")
        body["finish_timestamp"] = body["start_timestamp"]
        r = self.client.post("/v0/ioct-timings", json=body, headers=hdr("timing_gate"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("finish_timestamp", r.get_json()["error"]["message"])

    # ---- privacy --------------------------------------------------------

    def test_no_raw_ids_in_examples(self):
        for path in (ROOT / "examples").glob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\b\d{9,10}\b", text), path.name)


@unittest.skipUnless(CONTRACT.exists(), "contract file not found next to gatekeeper/")
class ExamplesMatchContractTest(unittest.TestCase):
    """The canned examples must be the contract's examples, with 0 differences."""

    def test_json_blocks_in_contract_match_example_files(self):
        text = CONTRACT.read_text(encoding="utf-8")
        blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S)]
        by_name = {
            "fitness_record_request": 0,
            "fitness_record_response": 1,
            "ioct_timing_request": 2,
            "audit_event": 4,
        }
        for name, index in by_name.items():
            self.assertEqual(blocks[index], load_example(name), name)


if __name__ == "__main__":
    unittest.main()
