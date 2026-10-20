"""MUSTER Gatekeeper stub API (contract v0).

Every v0 endpoint returns the canned example from the contract so the other
zones can build against it right away. This is a STUB:

- Nothing is stored. Responses do not change with the request body.
- The caller's role is read from the X-Role header. That is a stand-in only.
  The real mechanism is open item 3 in docs/interfaces/api-contract-v0.md.
- Requests are validated and rejected the way the real API will reject them,
  so Edge can build its error screens now.

Run from gatekeeper/api/:
  python app.py        (listens on 127.0.0.1:5000)
Test (also works with pytest):
  python -m unittest discover -s tests -v
"""

import json
import re
import secrets
from pathlib import Path

from flask import Flask, jsonify, request

CONTRACT_VERSION = "v0"
EXAMPLES_DIR = Path(__file__).parent / "examples"

ROLES = {"grader", "cadre", "cadet", "timing_gate"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

AFT_FIELDS = {
    "deadlift_lbs": int,
    "hand_release_pushups_reps": int,
    "sprint_drag_carry_seconds": (int, float),
    "plank_seconds": int,
    "two_mile_run_seconds": int,
}


def load_example(name):
    with open(EXAMPLES_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def error(status, code, message):
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": "req_" + secrets.token_hex(5).upper(),
        }
    }
    return jsonify(body), status


def create_app():
    app = Flask(__name__)
    app.json.sort_keys = False

    # ---- guards -------------------------------------------------------

    @app.before_request
    def check_contract_version():
        if request.path.startswith("/v0/"):
            if request.headers.get("X-Contract-Version") != CONTRACT_VERSION:
                return error(
                    400,
                    "unsupported_contract_version",
                    "Send the header 'X-Contract-Version: v0'.",
                )

    def role_gate(allowed):
        """Return an error response if the caller's role may not call this
        endpoint, else None. Missing, unknown, and wrong roles are all 403
        (contract section 2)."""
        role = request.headers.get("X-Role", "")
        if role not in ROLES:
            return error(403, "forbidden_role", "Role missing or unknown.")
        if role not in allowed:
            return error(
                403, "forbidden_role", f"Role '{role}' may not call this endpoint."
            )
        return None

    # ---- validation ---------------------------------------------------

    def validation_error(field, why):
        return error(400, "validation_failed", f"Field '{field}': {why}.")

    def validate_common(body):
        for field in ("idempotency_key", "cadet_id_hash"):
            if field not in body:
                return validation_error(field, "required")
        if not isinstance(body["cadet_id_hash"], str) or not HASH_RE.match(
            body["cadet_id_hash"]
        ):
            return validation_error("cadet_id_hash", "must be 64 lowercase hex characters")
        return None

    def validate_fitness_record(body):
        bad = validate_common(body)
        if bad:
            return bad
        for field in ("test_type", "test_date", "measurements"):
            if field not in body:
                return validation_error(field, "required")
        if body["test_type"] not in ("AFT", "IOCT_MANUAL"):
            return validation_error("test_type", "must be 'AFT' or 'IOCT_MANUAL'")
        if not isinstance(body["test_date"], str) or not DATE_RE.match(body["test_date"]):
            return validation_error("test_date", "must be YYYY-MM-DD")
        m = body["measurements"]
        if not isinstance(m, dict):
            return validation_error("measurements", "must be an object")
        if body["test_type"] == "AFT":
            for field, kind in AFT_FIELDS.items():
                if field not in m:
                    return validation_error(f"measurements.{field}", "required")
                if isinstance(m[field], bool) or not isinstance(m[field], kind):
                    return validation_error(f"measurements.{field}", "wrong type")
        else:
            if "elapsed_seconds" not in m:
                return validation_error("measurements.elapsed_seconds", "required")
        return None

    def validate_timing(body):
        bad = validate_common(body)
        if bad:
            return bad
        for field in ("tag_id", "start_timestamp", "finish_timestamp", "gate_id"):
            if field not in body:
                return validation_error(field, "required")
        if body["finish_timestamp"] <= body["start_timestamp"]:
            return validation_error("finish_timestamp", "must be after start_timestamp")
        return None

    def json_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, validation_error("body", "must be a JSON object")
        return body, None

    # ---- endpoints ----------------------------------------------------

    @app.get("/v0/health")
    def health():
        denied = role_gate(ROLES)
        if denied:
            return denied
        return jsonify({"status": "ok", "contract_version": CONTRACT_VERSION, "stub": True})

    @app.post("/v0/fitness-records")
    def create_fitness_record():
        denied = role_gate({"grader"})
        if denied:
            return denied
        body, bad = json_body()
        if bad:
            return bad
        bad = validate_fitness_record(body)
        if bad:
            return bad
        return jsonify(load_example("fitness_record_response")), 201

    def cadet_may_see(cadet_id_hash):
        """Stub for 'cadet may see own record only': a cadet sends
        X-Cadet-Id-Hash and it must match the record's cadet."""
        if request.headers.get("X-Role") != "cadet":
            return True
        return request.headers.get("X-Cadet-Id-Hash") == cadet_id_hash

    @app.get("/v0/fitness-records/<record_id>")
    def get_fitness_record(record_id):
        denied = role_gate({"grader", "cadre", "cadet"})
        if denied:
            return denied
        record = load_example("fitness_record_response")
        if record_id != record["record_id"] or not cadet_may_see(record["cadet_id_hash"]):
            return error(404, "record_not_found", "No such record.")
        return jsonify(record)

    @app.get("/v0/cadets/<cadet_id_hash>/fitness-records")
    def list_cadet_records(cadet_id_hash):
        denied = role_gate({"grader", "cadre", "cadet"})
        if denied:
            return denied
        if not HASH_RE.match(cadet_id_hash):
            return validation_error("cadet_id_hash", "must be 64 lowercase hex characters")
        if not cadet_may_see(cadet_id_hash):
            return error(404, "record_not_found", "No such record.")
        record = load_example("fitness_record_response")
        records = [record] if cadet_id_hash == record["cadet_id_hash"] else []
        return jsonify({"cadet_id_hash": cadet_id_hash, "records": records})

    @app.post("/v0/ioct-timings")
    def create_ioct_timing():
        denied = role_gate({"timing_gate"})
        if denied:
            return denied
        body, bad = json_body()
        if bad:
            return bad
        bad = validate_timing(body)
        if bad:
            return bad
        return jsonify(load_example("ioct_timing_response")), 201

    @app.get("/v0/audit/events")
    def list_audit_events():
        denied = role_gate({"cadre"})
        if denied:
            return denied
        return jsonify({"events": [load_example("audit_event")], "next_seq": None})

    @app.get("/v0/audit/verify")
    def verify_audit():
        denied = role_gate({"cadre"})
        if denied:
            return denied
        return jsonify(load_example("audit_verify_pass"))

    # ---- JSON errors for everything else ------------------------------

    @app.errorhandler(404)
    def not_found(_):
        return error(404, "record_not_found", "No such endpoint.")

    @app.errorhandler(405)
    def not_allowed(_):
        return error(
            405,
            "method_not_allowed",
            "Records and audit entries are append only. There is no update or delete.",
        )

    @app.errorhandler(500)
    def internal(_):
        return error(500, "internal_error", "Internal error. Nothing was written.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
