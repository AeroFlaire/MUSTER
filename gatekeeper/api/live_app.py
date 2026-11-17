"""MUSTER Gatekeeper, live mode (PBI-04 L24 writes, PBI-05 L25 role check).

Same API as the stub (contract v0 rev 2), but three endpoints are real and write to a
Vault database. Everything else still returns the canned example, so Edge keeps working
while the rest is wired up one card at a time.

Live endpoints (they write to the Vault):
  POST /v0/sessions/{id}/events        open an AFT scorecard   (record + audit entry)
  POST /v0/events/{id}/components      enter one AFT component (record + audit entry)
  POST /v0/events/{id}/submit          send the scorecard on   (status change + audit entry)
  GET  /v0/results                     read scores: grader and OIC see all, a cadet sees only theirs

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
    "POST /v0/identity/lookup": ("identity_lookup", 200),
    "POST /v0/admin/register": ("register_person", 201),
    "GET /v0/admin/people": ("people", 200),
}
SESSION_SECONDS = 4 * 60 * 60  # a CAC sign-in lasts one class period, then the card is scanned again

DEMO_AFT_SESSION = 1
DEMO_IOCT_SESSION = 2


def _dispatch(name, conn, actor, kw, body):
    """One Vault write per request. Returns the JSON body."""
    if name == "open_event":
        return bridge.open_event(conn, actor, kw["session_id"], body.get("barcode"),
                                 body.get("client_ref"), body.get("edipi"))
    if name == "add_component":
        return bridge.add_component(conn, actor, kw["event_id"], body["component"],
                                    body["raw_value"], body["age_group"], body["scoring_column"])
    if name == "submit":
        return bridge.submit(conn, actor, kw["event_id"])
    if name == "scoring_standards":
        return scoring.standards_summary()
    if name == "ioct_pair":
        return bridge.ioct_pair(conn, actor, kw["session_id"], body.get("barcode"),
                                body["tag_uid"], body.get("client_ref"), body.get("edipi"))
    if name == "gate_read":
        return bridge.gate_read(conn, actor, kw["session_id"], body["tag_uid"], body["gate"],
                                body["read_at_ms"], body["gate_device"], body.get("client_ref"))
    if name == "reopen":
        return bridge.reopen(conn, actor, kw["event_id"], body["note"])
    if name == "supersede":
        return bridge.supersede(conn, actor, kw["event_id"], body["reason"])
    if name == "attendance_mark":
        return bridge.attendance_mark(conn, actor, body["section_id"], body["class_date"],
                                      body.get("status", "PRESENT"), body.get("barcode"),
                                      body.get("client_ref"), body.get("cadet_id"),
                                      body.get("edipi"))
    if name == "identity_lookup":
        return bridge.identity_lookup(conn, body["edipi"])
    if name == "register_person":
        return bridge.register_person(conn, actor, body["edipi"], body["first_name"],
                                      body["last_name"], body["role"])
    if name == "people":
        return bridge.people(conn)
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


def results_view(app):
    """GET /v0/results. Grader and OIC: every score. A cadet token tied to a cadet's hashed ID:
    only that cadet's own scores. Read only. Anyone else: 403."""
    def view():
        principal = auth.authenticate(request.headers, app.config["CREDENTIALS"])
        if principal is None:
            return stub.error(401, "unauthenticated", "Missing or invalid credentials.")
        conn = None
        try:
            conn = bridge.connect(app.config["MUSTER_DB"])
            if principal.actor_id is None:
                if not principal.cadet_hash:
                    return stub.error(403, "forbidden_role",
                                      "This credential is not linked to a cadet record.")
                only_hash = principal.cadet_hash
            else:
                role = bridge.role_of(conn, principal.actor_id)
                if role is None:
                    return stub.error(403, "forbidden_role",
                                      "This account is inactive or unknown.")
                if role not in stub.GRADER:
                    return stub.error(403, "forbidden_role",
                                      f"Role '{role}' may not call this endpoint.")
                only_hash = None
            test_type = request.args.get("test_type")
            if test_type is not None and test_type not in ("AFT", "IOCT"):
                return stub.validation_error("test_type", "must be 'AFT' or 'IOCT' (query parameter)")
            sid = request.args.get("session_id")
            if sid is not None and not sid.isdigit():
                return stub.validation_error("session_id", "must be an integer (query parameter)")
            return jsonify(bridge.results(conn, only_hash, test_type,
                                          int(sid) if sid is not None else None)), 200
        except Exception as exc:  # noqa: BLE001
            status, code, message = bridge.map_error(exc)
            if status == 500:
                app.logger.exception("Vault read failed (details above; no request data logged)")
            return stub.error(status, code, message)
        finally:
            if conn is not None:
                conn.close()

    return view


def signin_view(app):
    """POST /v0/auth/cac-signin. The page scanned the caller's own CAC and sends the parsed DoD ID
    and the position they chose ("grader" or "cadet"). The database decides whether that card is
    registered for that position. A mismatch is refused (403) and logged. On success the caller gets
    a session token that expires, tied to the person (never to an ID)."""
    def view():
        body = request.get_json(silent=True)
        bad = stub.validate_body({"edipi": ("edipi", True), "position": ("position", True)})
        if bad:
            return bad
        body = request.get_json()
        conn = None
        try:
            conn = bridge.connect(app.config["MUSTER_DB"])
            who = bridge.cac_signin(conn, body["edipi"].strip(), body["position"])
            token = app.config["CREDENTIALS"].issue(
                "cac " + who["role"], who["actor_id"], who["cadet_hash"], ttl=SESSION_SECONDS)
            return jsonify({"token": token, "role": who["role"], "position": body["position"],
                            "name": who["name"], "expires_in_s": SESSION_SECONDS}), 200
        except Exception as exc:  # noqa: BLE001
            status, code, message = bridge.map_error(exc)
            if status == 500:
                app.logger.exception("Sign-in failed (details above; no request data logged)")
            return stub.error(status, code, message)
        finally:
            if conn is not None:
                conn.close()

    return view


def serve_pages(app):
    """The demo web app and Arjo's scanner files, served from the same address as the API. That
    keeps the camera working (localhost or HTTPS is a secure context) and needs no CORS."""
    from flask import abort, redirect, send_from_directory
    root = HERE.parents[1] / "edge"

    @app.route("/app/demo_tokens.js")
    def demo_tokens():
        tokens = app.config.get("DEMO_TOKENS") or {}
        if request.remote_addr not in ("127.0.0.1", "::1"):
            tokens = {}  # only the laptop itself gets the admin and gate tokens, never the network
        resp = app.response_class("window.DEMO_TOKENS = " + json.dumps(tokens) + ";\n",
                                  mimetype="text/javascript")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/")
    def home():
        return redirect("/app/")

    @app.route("/app/", defaults={"name": "index.html"})
    @app.route("/app/<path:name>")
    def app_files(name):
        return send_from_directory(root / "app", name)

    @app.route("/scanner/<path:name>")
    def scanner_files(name):
        if name.startswith(".cert"):
            abort(404)
        return send_from_directory(root / "scanner", name)


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
        elif key == "GET /v0/results":
            app.view_functions[key] = results_view(app)
        elif key == "POST /v0/auth/cac-signin":
            app.view_functions[key] = signin_view(app)

    serve_pages(app)
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


def demo_cadet_hashes(pack_path, n=2):
    """Keyed ID hashes of the first n synthetic cadets (needs MUSTER_EDIPI_KEY)."""
    from muster_vault import identity

    key = identity.load_edipi_key()
    pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
    return [identity.hash_edipi(c["edipi"], key) for c in pack["cadets"][:n]]


def issue_demo_credentials(users, cadet_hashes=()):
    """One token per demo user, plus cadet tokens (no Vault user). `cadet` is tied to the first
    hash and `cadet_2` to the second, so each can read only their own results. With no hashes,
    `cadet` is a cadet credential linked to nobody. Returns (store, tokens)."""
    store = auth.CredentialStore()
    tokens = {role: store.issue(f"demo {role}", uid) for role, uid in users.items()}
    hashes = list(cadet_hashes)
    tokens["cadet"] = store.issue("demo cadet", None, hashes[0] if hashes else None)
    if len(hashes) > 1:
        tokens["cadet_2"] = store.issue("demo cadet 2", None, hashes[1])
    return store, tokens


# ---- app mode: a database that keeps real people between runs --------------------------------
# `python live_app.py --app` is the CAC version of the demo. The database persists (same
# MUSTER_DB_KEY and MUSTER_EDIPI_KEY every run, see start_app.ps1), people are added by scanning
# their own card on the Register screen, and sign-in is a scan. Nothing here is synthetic unless
# --synthetic is given, which adds Arjo's fake test cards so the flow can be tried without anyone's
# real card.

DEFAULT_APP_DB = HERE / "muster_app.db"
SYNTHETIC_CARDS = HERE.parents[1] / "edge" / "scanner" / "tests" / "synthetic_front.json"


def seed_app(db_path, synthetic=False):
    """Fresh app database: an admin, a gate station, a session host (OIC with no card), an open AFT
    session (id 1), an open IOCT session (id 2) and wristbands TAG-017 to TAG-019. With `synthetic`,
    also Arjo's fake cards: 9000000001 to 10 as cadets, 11 as a grader, 12 as the OIC, and 13 is left
    unregistered so a refusal can be shown."""
    from muster_vault import fitness, identity, ioct

    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    conn = bridge.connect(db_path)
    bridge.init_schema(conn)
    admin = identity.bootstrap_admin(conn, "Demo Admin")
    identity.add_user(conn, admin, "Demo Gate", "gate_station")
    host = identity.add_user(conn, admin, "Session Host", "oic")
    assert fitness.open_session(conn, host, "AFT", "2026-11-19", "Demo Floor") == DEMO_AFT_SESSION
    assert fitness.open_session(conn, host, "IOCT", "2026-11-19", "Demo Track") == DEMO_IOCT_SESSION
    for tag in ("TAG-017", "TAG-018", "TAG-019"):
        ioct.register_tag(conn, admin, tag, tag[-2:])
    if synthetic:
        key = identity.load_edipi_key()
        cards = json.loads(SYNTHETIC_CARDS.read_text(encoding="utf-8"))
        for c in cards:
            if c["note"] and c["edipi"] != "9000000011" and c["edipi"] != "9000000012":
                continue
            n = int(c["edipi"][-2:])
            h = identity.hash_edipi(c["edipi"], key)
            e = c["expect"]
            if n <= 10:
                identity.add_cadet(conn, admin, h, e["lastName"], e["firstName"])
            elif n == 11:
                identity.add_user(conn, admin, f"{e['lastName']}, {e['firstName']}", "grader", h)
            elif n == 12:
                identity.add_user(conn, admin, f"{e['lastName']}, {e['firstName']}", "oic", h)
    conn.close()


def app_tokens(db_path):
    """Fresh in-memory tokens for this run: the admin (Register screen) and the demo gate (the
    simulated RFID gate). People sign in by scanning, so they get no token here."""
    conn = bridge.connect(db_path)
    ids = {r["role"]: r["id"] for r in conn.execute(
        "SELECT id, role FROM app_user WHERE role IN ('admin', 'gate_station') ORDER BY id")}
    conn.close()
    store = auth.CredentialStore()
    return store, {role: store.issue(f"app {role}", uid) for role, uid in ids.items()}


def _lan_address():
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    if "--app" in sys.argv:
        db = Path(os.environ.get("MUSTER_DB", DEFAULT_APP_DB))
        if "--reset" in sys.argv or not db.exists():
            seed_app(db, synthetic="--synthetic" in sys.argv)
            print(f"Built a new app database {db}" +
                  (" with Arjo's fake test cards." if "--synthetic" in sys.argv else "."))
        store, tokens = app_tokens(db)
        flask_app = create_live_app(db, store)
        flask_app.config["DEMO_TOKENS"] = tokens
        https = "--https" in sys.argv
        host = "0.0.0.0" if https else "127.0.0.1"
        scheme = "https" if https else "http"
        print()
        print(f"  MUSTER app:  {scheme}://127.0.0.1:5000/app/   (open this on the laptop)")
        if https:
            print(f"  From a phone on the same network:  https://{_lan_address()}:5000/app/")
            print("  The phone will warn about the certificate (self-signed). Accept it once.")
            print("  Only the laptop itself can register people or use the demo gate.")
        if https:
            print()
            print("  Admin token (type it into the Register page on your phone; it changes every start):")
            print("  " + tokens.get("admin", ""))
        print("  Press Ctrl+C to stop.")
        flask_app.run(host=host, port=5000, ssl_context="adhoc" if https else None)
        sys.exit(0)
    db = Path(os.environ.get("MUSTER_DB", DEFAULT_DB))
    creds_path = Path(os.environ.get("MUSTER_CREDENTIALS", DEFAULT_CREDS))
    pack = Path(__file__).resolve().parents[2] / "tests" / "synthetic_test_pack.json"
    if "--reset" in sys.argv or not db.exists() or not creds_path.exists():
        sid, users = seed_demo(db, pack)
        store, tokens = issue_demo_credentials(users, demo_cadet_hashes(pack))
        store.save(creds_path)  # hashes only
        print(f"Built demo database {db} (AFT session {sid}, 10 synthetic cadets).")
        print("Demo tokens (shown once, not saved anywhere; paste one into Edge):")
        for role, token in tokens.items():
            print(f"  {role:17s} {token}")
    create_live_app(db, creds_path).run(host="127.0.0.1", port=5000)
