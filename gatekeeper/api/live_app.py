"""MUSTER Gatekeeper, live mode (PBI-04 L24 writes, PBI-05 L25 role check).

Same API as the stub (contract v0 rev 2), but three endpoints are real and write to a
Vault database. Everything else still returns the canned example, so Edge keeps working
while the rest is wired up one card at a time.

Live endpoints (they write to the Vault):
  POST /v0/sessions/{id}/events        open an AFT scorecard   (record + audit entry)
  POST /v0/events/{id}/components      enter one AFT component (record + audit entry)
  POST /v0/events/{id}/submit          send the scorecard on   (status change + audit entry)

Who may call them (auth.py): `Authorization: Bearer <token>`. The role is read from the
Vault's own user record, never from the request. The X-Role header is ignored here.
  no or bad token        -> 401 unauthenticated, nothing reaches the Vault
  cadet token            -> 403 forbidden_role,  nothing reaches the Vault
  inactive user or a role that is not allowed -> 403 forbidden_role, nothing reaches the Vault

Run from gatekeeper/api/ (PowerShell):
  $env:MUSTER_EDIPI_KEY = "a-demo-secret-of-at-least-32-characters"
  $env:MUSTER_DB_KEY = (python -c "import secrets; print(secrets.token_hex(32))")  # keep it; never commit it
  python live_app.py --reset       # builds a fresh demo database and prints demo tokens
  python live_app.py               # reuses them
"""

import json
import os
import sys
from pathlib import Path

from flask import jsonify, request

import app as stub
import auth
import scoring
import vault_bridge as bridge

HERE = Path(__file__).parent
DEFAULT_DB = HERE / "muster_demo.db"
DEFAULT_CREDS = HERE / "muster_demo_credentials.json"

# endpoint -> (name, HTTP status on success). The three AFT writes were made live in L24,
# the three IOCT (timing) writes in L25.
LIVE = {
    "POST /v0/sessions/<int:session_id>/events": ("open_event", 201),
    "POST /v0/events/<int:event_id>/components": ("add_component", 200),
    "POST /v0/events/<int:event_id>/submit": ("submit", 200),
    "GET /v0/scoring/standards": ("scoring_standards", 200),
    "POST /v0/sessions/<int:session_id>/ioct/pair": ("ioct_pair", 201),
    "POST /v0/sessions/<int:session_id>/gate-reads": ("gate_read", 201),
    "POST /v0/events/<int:event_id>/ioct/compute": ("compute_run", 200),
    "POST /v0/events/<int:event_id>/reopen": ("reopen", 200),
    "POST /v0/events/<int:event_id>/supersede": ("supersede", 201),
    "POST /v0/attendance/marks": ("attendance_mark", 201),
    "GET /v0/attendance/sections/<int:section_id>/roster": ("attendance_roster", 200),
}

DEMO_AFT_SESSION = 1
DEMO_IOCT_SESSION = 2


def _dispatch(name, conn, actor, kw, body):
    """One Vault write per request. Returns the JSON body."""
    if name == "open_event":
        return bridge.open_event(conn, actor, kw["session_id"], body["barcode"],
                                 body.get("client_ref"))
    if name == "add_component":
        return bridge.add_component(conn, actor, kw["event_id"], body["component"],
                                    body["raw_value"], body["age_group"], body["scoring_column"])
    if name == "submit":
        return bridge.submit(conn, actor, kw["event_id"])
    if name == "scoring_standards":
        return scoring.standards_summary()
    if name == "ioct_pair":
        return bridge.ioct_pair(conn, actor, kw["session_id"], body["barcode"],
                                body["tag_uid"], body.get("client_ref"))
    if name == "gate_read":
        return bridge.gate_read(conn, actor, kw["session_id"], body["tag_uid"], body["gate"],
                                body["read_at_ms"], body["gate_device"], body.get("client_ref"))
    if name == "reopen":
        return bridge.reopen(conn, actor, kw["event_id"], body["note"])
    if name == "supersede":
        return bridge.supersede(conn, actor, kw["event_id"], body["reason"])
    if name == "attendance_mark":
        return bridge.attendance_mark(conn, actor, body["section_id"], body["class_date"],
                                      body.get("status", "PRESENT"), body["barcode"],
                                      body.get("client_ref"))
    if name == "attendance_roster":
        day = request.args.get("class_date", "")
        if not stub.DATE_RE.match(day):
            raise ValueError("Field 'class_date': must be YYYY-MM-DD (query parameter)")
        return bridge.attendance_roster(conn, actor, kw["section_id"], day)
    return bridge.compute_run(conn, actor, kw["event_id"])


def live_view(app, name, ok_status, roles, body_spec):
    def view(**kw):
        # 1. Who is calling? No Vault, no body parsing, no detail leaked before this passes.
        principal = auth.authenticate(request.headers, app.config["CREDENTIALS"])
        if principal is None:
            return stub.error(401, "unauthenticated", "Missing or invalid credentials.")
        if principal.actor_id is None:
            return stub.error(403, "forbidden_role", "This account may not write records.")
        conn = None
        try:
            conn = bridge.connect(app.config["MUSTER_DB"])
            # 2. What may they do? The role comes from the Vault's record of that user.
            role = bridge.role_of(conn, principal.actor_id)
            if role is None:
                return stub.error(403, "forbidden_role", "This account is inactive or unknown.")
            if role not in roles:
                return stub.error(403, "forbidden_role",
                                  f"Role '{role}' may not call this endpoint.")
            # 3. Is the request well formed?
            body = {}
            if body_spec is not None:
                bad = stub.validate_body(body_spec)
                if bad:
                    return bad
                body = request.get_json()
            # 4. Only now does anything reach the Vault, which checks the role again.
            return jsonify(_dispatch(name, conn, principal.actor_id, kw, body)), ok_status
        except Exception as exc:  # noqa: BLE001 - every Vault error maps to a contract error
            status, code, message = bridge.map_error(exc)
            if status == 500:
                app.logger.exception("Vault call failed (details above; no request data logged)")
            return stub.error(status, code, message)
        finally:
            if conn is not None:
                conn.close()

    return view


def create_live_app(db_path, credentials):
    """`credentials` is an auth.CredentialStore (or a path to a saved one)."""
    app = stub.create_app()
    app.config["MUSTER_DB"] = str(db_path)
    if not isinstance(credentials, auth.CredentialStore):
        credentials = auth.CredentialStore.load(credentials)
    app.config["CREDENTIALS"] = credentials

    for method, rule, roles, _status, _example, body_spec, _s, _e in stub.ROUTES:
        key = f"{method} {rule}"
        if key in LIVE:
            name, ok_status = LIVE[key]
            app.view_functions[key] = live_view(app, name, ok_status, roles, body_spec)

    return app


# ---- demo database (synthetic data only) ------------------------------------

def seed_demo(db_path, pack_path):
    """Fresh Vault database: an admin, an OIC, a grader, a compiler station, the ten
    synthetic cadets from the shared data pack, a gate station, an open AFT session (id 1),
    an open IOCT session (id 2), and wristbands TAG-017 to TAG-019.
    Returns (aft_session_id, {role: vault user id})."""
    from muster_vault import attendance, fitness, identity, ioct

    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    conn = bridge.connect(db_path)
    bridge.init_schema(conn)
    key = identity.load_edipi_key()
    admin = identity.bootstrap_admin(conn, "Demo Admin")
    users = {
        "admin": admin,
        "oic": identity.add_user(conn, admin, "Demo OIC", "oic"),
        "grader": identity.add_user(conn, admin, "Demo Grader", "grader"),
        "compiler_station": identity.add_user(conn, admin, "Demo Compiler Station",
                                              "compiler_station"),
        "gate_station": identity.add_user(conn, admin, "Demo Gate", "gate_station"),
        "instructor": identity.add_user(conn, admin, "Demo Instructor A", "instructor"),
        "instructor_b": identity.add_user(conn, admin, "Demo Instructor B", "instructor"),
    }
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    cadet_ids = []
    for c in pack["cadets"]:
        first, last = c["fake_name"].split(" ", 1)
        cadet_ids.append(identity.add_cadet(conn, admin, identity.hash_edipi(c["edipi"], key),
                                            last, first))
    # Attendance: section 1 (Instructor A) has all ten cadets, section 2 (Instructor B) has only
    # the first, so the other nine are "not on the roster" for B.
    for sec, instr, who in ((1, "instructor", cadet_ids), (2, "instructor_b", cadet_ids[:1])):
        sid = attendance.add_section(conn, admin, "PE101", f"G{sec}", "L1", "AY27-1", users[instr])
        assert sid == sec
        for cid in who:
            attendance.enroll(conn, admin, sid, cid)
    session = fitness.open_session(conn, users["oic"], "AFT", "2026-10-22", "Test Gym")
    ioct_session = fitness.open_session(conn, users["oic"], "IOCT", "2026-10-22", "Test Track")
    assert (session, ioct_session) == (DEMO_AFT_SESSION, DEMO_IOCT_SESSION)
    for tag in ("TAG-017", "TAG-018", "TAG-019"):
        ioct.register_tag(conn, users["admin"], tag, tag[-2:])
    conn.close()
    return session, users


def issue_demo_credentials(users):
    """One token per demo user, plus a cadet token (no Vault user). Returns (store, tokens)."""
    store = auth.CredentialStore()
    tokens = {role: store.issue(f"demo {role}", uid) for role, uid in users.items()}
    tokens["cadet"] = store.issue("demo cadet", None)
    return store, tokens


if __name__ == "__main__":
    db = Path(os.environ.get("MUSTER_DB", DEFAULT_DB))
    creds_path = Path(os.environ.get("MUSTER_CREDENTIALS", DEFAULT_CREDS))
    pack = Path(__file__).resolve().parents[2] / "tests" / "synthetic_test_pack.json"
    if "--reset" in sys.argv or not db.exists() or not creds_path.exists():
        sid, users = seed_demo(db, pack)
        store, tokens = issue_demo_credentials(users)
        store.save(creds_path)  # hashes only
        print(f"Built demo database {db} (AFT session {sid}, 10 synthetic cadets).")
        print("Demo tokens (shown once, not saved anywhere; paste one into Edge):")
        for role, token in tokens.items():
            print(f"  {role:17s} {token}")
    create_live_app(db, creds_path).run(host="127.0.0.1", port=5000)
