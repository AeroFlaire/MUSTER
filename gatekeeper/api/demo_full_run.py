"""Sprint 1 review: one person, one laptop, the whole system, every access level.

Synthetic data only. Builds its own throwaway Vault database, starts the real Gatekeeper on
it, and walks through the full path over real HTTP:

  1. Grader enters a full AFT scorecard (scan a fake barcode, five events, submit)
  2. Every access level tries every write endpoint: who is allowed, who is refused
  3. An IOCT run: wristband paired, simulated gate reads, run time computed by the Vault
  4. Forced failure: the audit write breaks, and 0 records are saved
  5. Attendance: an instructor marks their own section; other sections and strangers are refused
  6. Integrity: the audit chain verifies, then one entry is edited and the verifier catches it

Run from gatekeeper/api/ (PowerShell):

  python demo_full_run.py --pause     # terminal run, waits for Enter between scenes
  python demo_full_run.py             # runs straight through
  python demo_full_run.py --screen    # browser part: starts the API and the demo page tokens

For --screen, open docs/interfaces/live_demo.html in a browser, pick an access level, and run it.
"""

import argparse
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "demo-key-synthetic-data-only-0123456789")
# Throwaway database key for this run only (the demo database is deleted afterwards).
os.environ.setdefault("MUSTER_DB_KEY", secrets.token_hex(32))
os.system("")  # lets Windows terminals show colors

import vault_bridge as bridge  # noqa: E402  (also puts the Vault on sys.path)
from live_app import (DEMO_AFT_SESSION, DEMO_IOCT_SESSION, create_live_app,  # noqa: E402
                      issue_demo_credentials, seed_demo)
from muster_vault import audit, clock, fitness  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "tests" / "synthetic_test_pack.json"
TOKENS_JS = ROOT / "docs" / "interfaces" / "demo_tokens.js"
PORT = 5057

G, R, Y, C, B, X = "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[1m", "\033[0m"

COMPONENTS = [("mdl_lbs", "MDL"), ("hrp_reps", "HRP"), ("sdc_time", "SDC"),
              ("plank_time", "PLK"), ("twomr_time", "2MR")]

# Contract section 2, written out by hand: which role may call which live write.
ACTIONS = ["open AFT", "enter result", "submit", "gate read", "pair band", "compute time"]
ALLOWED = {
    "open AFT": {"grader", "oic"},
    "enter result": {"grader", "oic"},
    "submit": {"grader", "oic"},
    "gate read": {"gate_station", "oic"},
    "pair band": {"grader", "oic"},
    "compute time": {"grader", "compiler_station", "oic"},
}
ACCESS_LEVELS = ["no login", "cadet", "grader", "gate_station", "compiler_station", "oic", "admin"]


def say(text="", color=""):
    print(f"{color}{text}{X}" if color else text, flush=True)


def scene(n, title):
    say()
    say("=" * 76, C)
    say(f" SCENE {n}: {title}", B + C)
    say("=" * 76, C)


class Demo:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "demo_vault.db"
        self.session, users = seed_demo(self.db, PACK)
        self.store, self.tokens = issue_demo_credentials(users)
        self.app = create_live_app(self.db, self.store)
        self.app.logger.disabled = True  # a forced failure would print a traceback on camera
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self.server = make_server("127.0.0.1", port, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.pack = json.loads(PACK.read_text(encoding="utf-8"))["cadets"]

    def stop(self):
        self.server.shutdown()
        self.tmp.cleanup()

    # -- helpers --
    def call(self, method, path, who=None, body=None, extra=None):
        headers = {"X-Contract-Version": "v1", "Content-Type": "application/json"}
        if who in self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens[who]}"
        headers.update(extra or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def counts(self):
        conn = bridge.connect(self.db)
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("fitness_event", "fitness_component", "gate_read", "ioct_run",
                              "audit_log")}
        finally:
            conn.close()

    def verify(self):
        conn = bridge.connect(self.db)
        try:
            return audit.verify(conn)
        finally:
            conn.close()

    def barcode(self, i):
        return self.pack[i]["barcode_string"]


def line(c):
    return (f"{c['fitness_event']} events, {c['fitness_component']} results, "
            f"{c['gate_read']} gate reads, {c['ioct_run']} timed runs | "
            f"audit entries: {c['audit_log']}")


def show_call(method, path, who, status, payload):
    ok = status < 300
    word = "OK" if ok else ("FAILED" if status >= 500 else "REFUSED")
    label = who.upper() if who in ACCESS_LEVELS else who
    say(f"  {method} {path}   as {label}", B)
    say(f"    -> {status} {word}", G if ok else R)
    if not ok:
        err = payload["error"]
        say(f"       {err['code']}: {err['message']}", R)


# ---- scenes ---------------------------------------------------------------------------

def scene_aft(d, wait):
    scene(1, "A grader enters a full AFT scorecard")
    cadet = d.pack[0]
    say(f"  Grader scans {cadet['fake_name']}'s fake back barcode and enters the five events.")
    before = d.counts()
    ev_path = f"/v0/sessions/{d.session}/events"
    st, out = d.call("POST", ev_path, "grader", {"barcode": d.barcode(0)})
    show_call("POST", ev_path, "grader", st, out)
    eid = out["event_id"]
    say(f"       cadet shown only as a hash: {out['scorecard']['cadet_edipi_hash'][:16]}...", G)
    for key, code in COMPONENTS:
        value = str(cadet["aft_results"][key])
        st, out = d.call("POST", f"/v0/events/{eid}/components", "grader",
                         {"component": code, "raw_value": value, "age_group": "17-21", "scoring_column": "M|C"})
        say(f"  POST /v0/events/{eid}/components  {code} = {value:>5}  -> {st} OK", G)
    st, out = d.call("POST", f"/v0/events/{eid}/submit", "grader")
    show_call("POST", f"/v0/events/{eid}/submit", "grader", st, out)
    after = d.counts()
    g = out["grading"]
    say(f"  Scorecard status: {out['status']}   total points: {g['total_points']}   result: {g['result']}"
        "  (scored from the official AFT tables)")
    say(f"  Records written: {after['fitness_event'] - before['fitness_event']} event, "
        f"{after['fitness_component'] - before['fitness_component']} results.   "
        f"Audit entries added: {after['audit_log'] - before['audit_log']} "
        "(one per write).", G)
    wait()


def scene_access(d, wait):
    scene(2, "Every access level tries every write endpoint")
    say("  Each role calls each write on a record that does not exist. A role that is allowed")
    say("  gets past the check and the Vault says 'not found' (nothing is written).")
    say("  A role that is refused gets 401 or 403 and never reaches the Vault.")
    say()
    before = d.counts()
    bc = d.barcode(1)
    probes = {
        "open AFT": ("POST", "/v0/sessions/999/events", {"barcode": bc}),
        "enter result": ("POST", "/v0/events/999/components",
                         {"component": "MDL", "raw_value": "340", "age_group": "17-21", "scoring_column": "M|C"}),
        "submit": ("POST", "/v0/events/999/submit", None),
        "gate read": ("POST", "/v0/sessions/999/gate-reads",
                      {"tag_uid": "TAG-017", "gate": "START", "read_at_ms": 1, "gate_device": "G1"}),
        "pair band": ("POST", "/v0/sessions/999/ioct/pair", {"barcode": bc, "tag_uid": "TAG-017"}),
        "compute time": ("POST", "/v0/events/999/ioct/compute", None),
    }
    w = 18
    say("  " + "access level".ljust(w) + "".join(a.center(14) for a in ACTIONS), B)
    mismatches = []
    for level in ACCESS_LEVELS:
        who = None if level == "no login" else level
        row = "  " + level.ljust(w)
        for action in ACTIONS:
            method, path, body = probes[action]
            st, _ = d.call(method, path, who, body)
            refused = st in (401, 403)
            cell = f"NO ({st})" if refused else "yes"
            should = level in ALLOWED[action]
            if refused == should:
                mismatches.append((level, action, st))
            row += (R if refused else G) + cell.center(14) + X
        say(row)
    say()
    after = d.counts()
    say(f"  Records and audit entries added by all {len(ACCESS_LEVELS) * len(ACTIONS)} attempts: "
        f"{sum(after[k] - before[k] for k in after)}", G if after == before else R)
    say("  Matches the contract's role table: " + ("yes" if not mismatches else f"NO {mismatches}"),
        G if not mismatches else R)
    say()
    say("  Cadets are not Vault users, so a cadet is refused everywhere. And a role typed into")
    say("  a request header means nothing, because the role comes from the Vault, not the caller:")
    st, out = d.call("POST", f"/v0/sessions/{d.session}/events", None, {"barcode": bc},
                     {"X-Role": "oic"})
    show_call("POST", f"/v0/sessions/{d.session}/events", "no login + a fake X-Role: oic header", st, out)
    wait()


def scene_ioct(d, wait):
    scene(3, "An IOCT run: wristband, gate reads, run time computed by the Vault")
    say("  The RFID gate hardware is simulated here: the reads are posted by a gate station.", Y)
    cadet = d.pack[0]
    ses = DEMO_IOCT_SESSION
    before = d.counts()
    st, out = d.call("POST", f"/v0/sessions/{ses}/ioct/pair", "grader",
                     {"barcode": d.barcode(0), "tag_uid": "TAG-017"})
    show_call("POST", f"/v0/sessions/{ses}/ioct/pair", "grader", st, out)
    eid = out["event_id"]
    start = clock.now_ms() + 1000
    target_ms = int(cadet["ioct_time"].split(":")[0]) * 60_000 + int(cadet["ioct_time"].split(":")[1]) * 1000
    for gate, device, at in (("START", "G1", start), ("END", "G2", start + target_ms)):
        mid = d.counts()
        st, out = d.call("POST", f"/v0/sessions/{ses}/gate-reads", "gate_station",
                         {"tag_uid": "TAG-017", "gate": gate, "read_at_ms": at,
                          "gate_device": device})
        show_call("POST", f"/v0/sessions/{ses}/gate-reads  ({gate})", "gate_station", st, out)
        now = d.counts()
        say(f"       new gate reads: {now['gate_read'] - mid['gate_read']}   "
            f"new audit entries: {now['audit_log'] - mid['audit_log']}", G)
    st, out = d.call("POST", f"/v0/events/{eid}/ioct/compute", "grader")
    show_call("POST", f"/v0/events/{eid}/ioct/compute", "grader", st, out)
    say(f"  Run time computed by the Vault: {out['elapsed']}   "
        f"(the shared test pack says {cadet['ioct_time']} for {cadet['fake_name']})", G)
    st, out = d.call("POST", f"/v0/events/{eid}/submit", "grader")
    say(f"  Submitted for the cadet to sign: status {out['status']}", G)
    say("  A gate can post reads but cannot pair a band or compute a time:", Y)
    st, out = d.call("POST", f"/v0/events/{eid}/ioct/compute", "gate_station")
    show_call("POST", f"/v0/events/{eid}/ioct/compute", "gate_station", st, out)
    wait()


def scene_forced_failure(d, wait):
    scene(4, "Forced failure: if the audit write fails, no record is saved")
    before = d.counts()
    say("  Before:  " + line(before))
    say("  Breaking the audit log on purpose (every audit write will fail)...", Y)
    conn = bridge.connect(d.db)
    conn.execute("CREATE TRIGGER demo_break_audit BEFORE INSERT ON audit_log "
                 "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
    conn.close()
    path = f"/v0/sessions/{d.session}/events"
    st, out = d.call("POST", path, "grader", {"barcode": d.barcode(2)})
    show_call("POST", path, "grader", st, out)
    after = d.counts()
    say("  After:   " + line(after))
    say(f"    new records: {after['fitness_event'] - before['fitness_event']}   "
        f"new audit entries: {after['audit_log'] - before['audit_log']}",
        G if after == before else R)
    conn = bridge.connect(d.db)
    conn.execute("DROP TRIGGER demo_break_audit")
    conn.close()
    say("  Audit log repaired. The same request now works, with nothing left over:", Y)
    st, out = d.call("POST", path, "grader", {"barcode": d.barcode(2)})
    show_call("POST", path, "grader", st, out)
    wait()


def scene_attendance(d, wait):
    scene(5, "Attendance: an instructor marks their own section only (SR-2)")
    conn = bridge.connect(d.db)
    base = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    conn.close()
    say("  Instructor A marks all ten cadets in their own section (G1):")
    ok = 0
    for i in range(10):
        st, _ = d.call("POST", "/v0/attendance/marks", "instructor",
                       {"section_id": 1, "class_date": "2026-11-17", "barcode": d.barcode(i)})
        ok += st == 201
    say(f"    {ok} of 10 marked, 1 record and 1 audit entry each", G if ok == 10 else R)
    say()
    say("  Instructor B tries to mark cadets in Instructor A's section (3 attempts):", Y)
    before = d.counts()["audit_log"]
    for i in range(3):
        st, out = d.call("POST", "/v0/attendance/marks", "instructor_b",
                         {"section_id": 1, "class_date": "2026-11-17", "barcode": d.barcode(i)})
        show_call("POST", "/v0/attendance/marks", "instructor_b", st, out)
    say(f"    audit entries added: {d.counts()['audit_log'] - before} (one rejected-attempt entry each)",
        G)
    say()
    say("  Instructor B tries to mark cadets who are not on their roster (3 attempts):", Y)
    for i in (3, 5, 7):
        st, out = d.call("POST", "/v0/attendance/marks", "instructor_b",
                         {"section_id": 2, "class_date": "2026-11-17", "barcode": d.barcode(i)})
        show_call("POST", "/v0/attendance/marks", "instructor_b", st, out)
    conn = bridge.connect(d.db)
    marks = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] - base
    rej = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'ATTENDANCE_REJECTED'"
                       ).fetchone()[0]
    conn.close()
    say()
    say(f"  False attendance records accepted: {marks - 10}.  Rejected attempts logged: {rej}.",
        G if (marks - 10 == 0 and rej == 6) else R)
    wait()


def scene_integrity(d, wait):
    scene(6, "Integrity: the audit chain, then a tamper attempt")
    r = d.verify()
    say(f"  Audit chain: PASS, {r.entries_checked} entries, none changed or missing", G)
    say()
    say("  Now an attacker with direct file access edits one entry (removing the append-only", Y)
    say("  guard first, which anyone with the file can do)...", Y)
    conn = bridge.connect(d.db)
    seq = conn.execute("SELECT seq FROM audit_log WHERE action = 'COMPONENT_ENTERED' "
                       "ORDER BY seq LIMIT 1").fetchone()["seq"]
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET data = replace(data, '340', '999') WHERE seq = ?", (seq,))
    conn.close()
    r = d.verify()
    if r.ok:
        say("  Audit chain: PASS  (the tamper was NOT caught)", R)
    else:
        say(f"  Audit chain: FAIL at entry {r.first_bad_seq}: {r.reason}", G)
    say("  The verifier names the first broken entry.")


def closing():
    say()
    say("=" * 76, C)
    say(" Not in this run (Sprint 2 and carry-over)", B + C)
    say("=" * 76, C)
    for item in (
        "Cadet signing with a real CAC at the score compiler (the signing endpoints are canned)",
        "The real CAC barcode parser (only the test pack format is read)",
        "Staff sign-in with a CAC (demo tokens stand in)",
        "Real RFID gate hardware (reads are simulated)",
        "DPE confirmation of the scoring standards, the IOCT pass time, and age group source",
    ):
        say(f"  - {item}")
    say()


def run_terminal(pause):
    def wait():
        if pause:
            input(f"{Y}  [press Enter for the next scene]{X}")

    d = Demo(PORT)
    try:
        say()
        say(" MUSTER  |  full run, one laptop  |  synthetic data only", B)
        say(" Real Vault database, real Gatekeeper, real HTTP. Roles are checked on every write.")
        say(f" People in this run: {', '.join(['grader', 'gate_station', 'compiler_station', 'oic', 'admin', 'cadet'])}")
        wait()
        scene_aft(d, wait)
        scene_access(d, wait)
        scene_ioct(d, wait)
        scene_forced_failure(d, wait)
        scene_attendance(d, wait)
        scene_integrity(d, wait)
        closing()
    finally:
        d.stop()


def run_screen():
    """Browser part: keep the API up on port 5000 and give the demo page its tokens."""
    d = Demo(5000)
    TOKENS_JS.write_text("window.DEMO_TOKENS = " + json.dumps(d.tokens, indent=2) + ";\n",
                         encoding="utf-8")
    say()
    say(" MUSTER Gatekeeper is running on http://127.0.0.1:5000 (synthetic data, throwaway database)", B)
    say(f" Demo tokens written to {TOKENS_JS}")
    say(" Now open docs/interfaces/live_demo.html in your browser, pick an access level, and run it.")
    say(" Press Ctrl+C here when you are done.", Y)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        d.stop()
        TOKENS_JS.unlink(missing_ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pause", action="store_true", help="wait for Enter between scenes")
    ap.add_argument("--screen", action="store_true", help="start the API for the browser demo page")
    args = ap.parse_args()
    if not PACK.exists():
        sys.exit(f"Cannot find the shared synthetic data pack at {PACK}")
    run_screen() if args.screen else run_terminal(args.pause)
