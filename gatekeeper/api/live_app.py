"""MUSTER Gatekeeper, live mode (PBI-04 L24).

Same API as the stub (contract v0 rev 2), but three endpoints are real and write to a
Vault database. Everything else still returns the canned example, so Edge keeps working
while the rest is wired up one card at a time.

Live endpoints (they write to the Vault):
  POST /v0/sessions/{id}/events        open an AFT scorecard   (record + audit entry)
  POST /v0/events/{id}/components      enter one AFT component (record + audit entry)
  POST /v0/events/{id}/submit          send the scorecard on   (status change + audit entry)

Run from gatekeeper/api/ (PowerShell):
  $env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
  python live_app.py --reset       # builds a fresh demo database from the test pack
  python live_app.py               # reuses it

The caller's role still comes from the X-Role header. DEV ONLY: Gatekeeper maps it to the
first active Vault user with that role (how a real login maps to a Vault app_user is an
open item). The Vault re-checks the role itself.
"""

import json
import os
import sys
from pathlib import Path

from flask import jsonify, request

import app as stub
import vault_bridge as bridge

DEFAULT_DB = Path(__file__).parent / "muster_demo.db"

LIVE = {
    "POST /v0/sessions/<int:session_id>/events": "open_event",
    "POST /v0/events/<int:event_id>/components": "add_component",
    "POST /v0/events/<int:event_id>/submit": "submit",
}


def live_view(app, name, roles, body_spec):
    def view(**kw):
        role = request.headers.get("X-Role", "")
        if role not in stub.ROLES:
            return stub.error(403, "forbidden_role", "Role missing or unknown.")
        if role not in roles:
            return stub.error(403, "forbidden_role", f"Role '{role}' may not call this endpoint.")
        body = {}
        if body_spec is not None:
            bad = stub.validate_body(body_spec)
            if bad:
                return bad
            body = request.get_json()
        conn = bridge.connect(app.config["MUSTER_DB"])
        try:
            actor = bridge.actor_for_role(conn, role)
            if actor is None:
                return stub.error(403, "forbidden_role", f"No active Vault user has role '{role}'.")
            if name == "open_event":
                out = bridge.open_event(conn, actor, kw["session_id"], body["barcode"],
                                        body.get("client_ref"))
                return jsonify(out), 201
            if name == "add_component":
                out = bridge.add_component(conn, actor, kw["event_id"], body["component"],
                                           body["raw_value"], body.get("points"))
                return jsonify(out), 200
            out = bridge.submit(conn, actor, kw["event_id"])
            return jsonify(out), 200
        except Exception as exc:  # noqa: BLE001 - every Vault error maps to a contract error
            status, code, message = bridge.map_error(exc)
            if status == 500:
                app.logger.exception("Vault call failed (details above; no request data logged)")
            return stub.error(status, code, message)
        finally:
            conn.close()

    return view


def create_live_app(db_path):
    app = stub.create_app()
    app.config["MUSTER_DB"] = str(db_path)
    for method, rule, roles, _status, _example, body_spec, _s, _e in stub.ROUTES:
        key = f"{method} {rule}"
        if key in LIVE:
            app.view_functions[key] = live_view(app, LIVE[key], roles, body_spec)
    return app


# ---- demo database (synthetic data only) ------------------------------------

def seed_demo(db_path, pack_path):
    """Fresh Vault database: an admin, an OIC, a grader, a compiler station, the ten
    synthetic cadets from the shared data pack, and one open AFT session (id 1)."""
    from muster_vault import fitness, identity

    Path(db_path).unlink(missing_ok=True)
    conn = bridge.connect(db_path)
    bridge.init_schema(conn)
    key = identity.load_edipi_key()
    admin = identity.bootstrap_admin(conn, "Demo Admin")
    oic = identity.add_user(conn, admin, "Demo OIC", "oic")
    identity.add_user(conn, admin, "Demo Grader", "grader")
    identity.add_user(conn, admin, "Demo Compiler Station", "compiler_station")
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    for c in pack["cadets"]:
        first, last = c["fake_name"].split(" ", 1)
        identity.add_cadet(conn, admin, identity.hash_edipi(c["edipi"], key), last, first)
    session = fitness.open_session(conn, oic, "AFT", "2026-10-22", "Test Gym")
    conn.close()
    return session


if __name__ == "__main__":
    db = Path(os.environ.get("MUSTER_DB", DEFAULT_DB))
    pack = Path(__file__).resolve().parents[2] / "tests" / "synthetic_test_pack.json"
    if "--reset" in sys.argv or not db.exists():
        sid = seed_demo(db, pack)
        print(f"Built demo database {db} (AFT session {sid}, 10 synthetic cadets).")
    create_live_app(db).run(host="127.0.0.1", port=5000)
