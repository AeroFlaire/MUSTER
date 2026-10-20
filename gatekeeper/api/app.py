"""MUSTER Gatekeeper stub API (contract v0, revision 2).

Every v0 endpoint returns the canned example from the contract so the other
zones can build against it right away. This is a STUB:

- Nothing is stored. Responses do not change with the request body.
- The caller's role is read from the X-Role header, using the Vault's role names
  (admin, oic, grader, compiler_station, gate_station, instructor, arms_nco).
  The real mechanism (a session mapped to a Vault app_user id) is an open item.
- Requests are validated and rejected the way the real API will reject them, so
  Edge can build its error screens now.
- CORS is wide open (any origin) so a browser page can call it. Stub only. Send the header X-Stub-Scenario to force
  a Vault style error (see SCENARIOS) after the request passes role and validation.

Ids in the examples: AFT session 7, IOCT session 8, AFT event 11, IOCT event 12.
Any other id returns 404 not_found.

Run from gatekeeper/api/:
  python app.py        (listens on 127.0.0.1:5000)
Test (also works with pytest):
  python -m unittest discover -s tests -v
"""

import json
import re
from pathlib import Path

from flask import Flask, jsonify, request

CONTRACT_VERSION = "v0"
EXAMPLES_DIR = Path(__file__).parent / "examples"

# Same names and groupings as vault/muster_vault (identity.ROLES, fitness.*_ROLES).
ROLES = {"admin", "oic", "grader", "compiler_station", "gate_station", "instructor", "arms_nco"}
GRADER = {"grader", "oic"}
STATION = {"compiler_station", "oic"}
OIC = {"oic", "admin"}

AFT_COMPONENTS = {"MDL", "HRP", "SDC", "PLK", "2MR"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SESSIONS = {7: "AFT", 8: "IOCT"}
EVENTS = {11: "AFT", 12: "IOCT"}
TAGS = {(8, "TAG-017")}

# status, code, message (code and status follow contract section 8)
SCENARIOS = {
    "not_found": (404, "not_found", "Stub scenario: no such record."),
    "invalid_transition": (409, "invalid_transition", "Stub scenario: scorecard is SIGNED, not SUBMITTED."),
    "challenge_invalid": (400, "challenge_invalid", "Stub scenario: the challenge is expired or already used."),
    "signature_invalid": (400, "signature_invalid", "Stub scenario: the signature does not verify against the enrolled key."),
    "timing_error": (422, "timing_error", "Stub scenario: no END gate read for this wristband since pairing."),
    "internal_error": (500, "internal_error", "Stub scenario: internal error. Nothing was written."),
}


def load_example(name):
    with open(EXAMPLES_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def error(status, code, message):
    import secrets

    body = {"error": {"code": code, "message": message,
                      "request_id": "req_" + secrets.token_hex(5).upper()}}
    return jsonify(body), status


def validation_error(field, why):
    return error(400, "validation_failed", f"Field '{field}': {why}.")


# ---- body validation --------------------------------------------------------
# Each spec maps field -> (kind, required). Kinds: str, int, date, test_type,
# component, gate, points.

def check_field(field, kind, value):
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            return validation_error(field, "must be a non-empty string")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return validation_error(field, "must be an integer")
    elif kind == "date":
        if not isinstance(value, str) or not DATE_RE.match(value):
            return validation_error(field, "must be YYYY-MM-DD")
    elif kind == "test_type":
        if value not in ("AFT", "IOCT"):
            return validation_error(field, "must be 'AFT' or 'IOCT'")
    elif kind == "component":
        if value not in AFT_COMPONENTS:
            return validation_error(field, f"must be one of {sorted(AFT_COMPONENTS)}")
    elif kind == "gate":
        if value not in ("START", "END"):
            return validation_error(field, "must be 'START' or 'END'")
    elif kind == "points":
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or not 0 <= value <= 100):
            return validation_error(field, "must be null or an integer from 0 to 100")
    return None


def validate_body(spec):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return validation_error("body", "must be a JSON object")
    for field, (kind, required) in spec.items():
        if field not in body:
            if required:
                return validation_error(field, "required")
            continue
        bad = check_field(field, kind, body[field])
        if bad:
            return bad
    return None


# ---- endpoint table ---------------------------------------------------------

def event_card(kw):
    return "aft_event_submitted" if kw["event_id"] == 11 else "ioct_event_computed"


ROUTES = [
    # method, rule, roles, status, example (name or function of path args), body spec,
    # session type required, event type required
    ("GET", "/v0/health", None, 200, None, None, None, None),
    ("POST", "/v0/sessions", OIC, 201, "session",
     {"test_type": ("test_type", True), "session_date": ("date", True), "location": ("str", True)},
     None, None),
    ("POST", "/v0/sessions/<int:session_id>/close", OIC, 200, "session_closed", None, "any", None),
    ("POST", "/v0/sessions/<int:session_id>/events", GRADER, 201, "aft_event_entered",
     {"barcode": ("str", True), "client_ref": ("str", False)}, "AFT", None),
    ("POST", "/v0/events/<int:event_id>/components", GRADER, 200, "component",
     {"component": ("component", True), "raw_value": ("str", True), "points": ("points", False)},
     None, "AFT"),
    ("POST", "/v0/events/<int:event_id>/submit", GRADER, 200, event_card, None, None, "any"),
    ("POST", "/v0/events/<int:event_id>/cadet-challenge", STATION, 200, "cadet_challenge",
     None, None, "any"),
    ("POST", "/v0/events/<int:event_id>/accept", STATION, 200, "accept",
     {"challenge_id": ("int", True), "signature_b64": ("str", True)}, None, "any"),
    ("POST", "/v0/events/<int:event_id>/dispute", STATION, 200, "dispute",
     {"reason": ("str", True)}, None, "any"),
    ("POST", "/v0/events/<int:event_id>/reopen", OIC, 200, "reopen",
     {"note": ("str", True)}, None, "any"),
    ("POST", "/v0/events/<int:event_id>/supersede", OIC, 201, "supersede",
     {"reason": ("str", True)}, None, "any"),
    ("GET", "/v0/events/<int:event_id>", GRADER | STATION | OIC, 200, event_card, None, None, "any"),
    ("GET", "/v0/events/<int:event_id>/verify", OIC, 200, "event_verify", None, None, "any"),
    ("POST", "/v0/sessions/<int:session_id>/ioct/pair", GRADER, 201, "ioct_event_paired",
     {"barcode": ("str", True), "tag_uid": ("str", True), "client_ref": ("str", False)},
     "IOCT", None),
    ("POST", "/v0/sessions/<int:session_id>/gate-reads", {"gate_station", "oic"}, 201, "gate_read",
     {"tag_uid": ("str", True), "gate": ("gate", True), "read_at_ms": ("int", True),
      "gate_device": ("str", True), "client_ref": ("str", False)}, "any", None),
    ("POST", "/v0/events/<int:event_id>/ioct/compute", GRADER | {"compiler_station"}, 200,
     "ioct_run", None, None, "IOCT"),
    ("GET", "/v0/sessions/<int:session_id>/ioct/tags/<tag_uid>/event", STATION, 200, "tag_event",
     None, "IOCT", None),
    ("GET", "/v0/audit/verify", OIC, 200, "audit_verify", None, None, None),
    ("GET", "/v0/audit/head", OIC, 200, "audit_head", None, None, None),
    ("GET", "/v0/audit/history", OIC, 200, "audit_history", None, None, None),
]

WRONG_TYPE = {
    ("session", "AFT"): "IOCT events are opened by pairing a wristband (ioct pair).",
    ("session", "IOCT"): "This is not an IOCT session.",
    ("event", "AFT"): "Components are for AFT events only.",
    ("event", "IOCT"): "Not an IOCT event.",
}


def make_view(roles, status, example, body_spec, session_type, event_type):
    def view(**kw):
        # 1. role
        if roles is not None:
            role = request.headers.get("X-Role", "")
            if role not in ROLES:
                return error(403, "forbidden_role", "Role missing or unknown.")
            if role not in roles:
                return error(403, "forbidden_role", f"Role '{role}' may not call this endpoint.")
        # 2. body
        if body_spec is not None:
            bad = validate_body(body_spec)
            if bad:
                return bad
        # 3. ids and state
        if session_type is not None:
            sid = kw["session_id"]
            if sid not in SESSIONS:
                return error(404, "not_found", f"Test session {sid} not found.")
            if session_type != "any" and SESSIONS[sid] != session_type:
                return error(409, "invalid_transition", WRONG_TYPE[("session", session_type)])
        if event_type is not None:
            eid = kw["event_id"]
            if eid not in EVENTS:
                return error(404, "not_found", f"Fitness event {eid} not found.")
            if event_type != "any" and EVENTS[eid] != event_type:
                return error(409, "invalid_transition", WRONG_TYPE[("event", event_type)])
        if "tag_uid" in kw and not body_spec and (kw["session_id"], kw["tag_uid"]) not in TAGS:
            return error(404, "not_found", f"Wristband '{kw['tag_uid']}' is not assigned in this session.")
        # 4. forced scenario
        scenario = request.headers.get("X-Stub-Scenario")
        if scenario:
            if scenario not in SCENARIOS:
                return validation_error("X-Stub-Scenario", f"must be one of {sorted(SCENARIOS)}")
            return error(*SCENARIOS[scenario])
        # 5. canned answer
        if request.path == "/v0/health":
            return jsonify({"status": "ok", "contract_version": CONTRACT_VERSION, "stub": True})
        if request.path == "/v0/audit/history":
            et, eid = request.args.get("entity_type"), request.args.get("entity_id")
            if not et or not (eid or "").isdigit():
                return validation_error("entity_type, entity_id", "both are required (entity_id is an integer)")
            body = load_example("audit_history")
            if et != body["entity_type"] or int(eid) != body["entity_id"]:
                body = {"entity_type": et, "entity_id": int(eid), "entries": []}
            return jsonify(body)
        name = example(kw) if callable(example) else example
        return jsonify(load_example(name)), status

    return view


def create_app():
    app = Flask(__name__)
    app.json.sort_keys = False

    @app.before_request
    def check_contract_version():
        if request.method == "OPTIONS":
            return None  # a browser's CORS preflight carries none of our custom headers
        if request.path.startswith("/v0/"):
            if request.headers.get("X-Contract-Version") != CONTRACT_VERSION:
                return error(400, "unsupported_contract_version",
                             "Send the header 'X-Contract-Version: v0'.")

    @app.after_request
    def allow_browser_calls(resp):
        # STUB ONLY. Lets the Edge page, opened from disk or from another port, call this
        # API from a browser. The real Gatekeeper must allow only the Edge origin.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Contract-Version, X-Role, X-Stub-Scenario")
        resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    for method, rule, roles, status, example, body_spec, s_type, e_type in ROUTES:
        app.add_url_rule(rule, endpoint=f"{method} {rule}",
                         view_func=make_view(roles, status, example, body_spec, s_type, e_type),
                         methods=[method])

    @app.errorhandler(404)
    def not_found(_):
        return error(404, "not_found", "No such endpoint.")

    @app.errorhandler(405)
    def not_allowed(_):
        return error(405, "method_not_allowed",
                     "Not allowed here. Audit entries are append only: there is no update or delete.")

    @app.errorhandler(500)
    def internal(_):
        return error(500, "internal_error", "Internal error. Nothing was written.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
