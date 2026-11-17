"""Security Tests 1 and 2 (Trello backlog), run once against the live Gatekeeper and Vault.

  Test 1  Impersonation and fake record attempts (SR-1, SR-3)
  Test 2  Attendance impersonation and out-of-section writes (SR-2)

Synthetic data only. Builds a throwaway encrypted Vault database, tries to cheat, and writes the
result of every attempt to docs/evidence/gatekeeper-tests-1-2-results.md (pass or fail, with the
counts). Run from gatekeeper/api/ (PowerShell):

    python run_security_tests.py
"""
import json
import os
import secrets
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("MUSTER_EDIPI_KEY", "security-test-key-synthetic-only-0123456789")
os.environ.setdefault("MUSTER_DB_KEY", secrets.token_hex(32))  # throwaway, this run only

import vault_bridge as bridge  # noqa: E402
from live_app import DEMO_AFT_SESSION, create_live_app, issue_demo_credentials, seed_demo  # noqa: E402
from muster_vault import audit  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "tests" / "synthetic_test_pack.json"
OUT = ROOT / "docs" / "evidence" / "gatekeeper-tests-1-2-results.md"
H = {"X-Contract-Version": "v1"}
DAY = "2026-11-17"


class Rig:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "vault.db"
        _, users = seed_demo(self.db, PACK)
        store, self.tokens = issue_demo_credentials(users)
        app = create_live_app(self.db, store)
        app.logger.disabled = True
        self.client = app.test_client()
        self.cadets = json.loads(PACK.read_text(encoding="utf-8"))["cadets"]

    def counts(self):
        conn = bridge.connect(self.db)
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("fitness_event", "fitness_component", "attendance", "audit_log")}
        finally:
            conn.close()

    def scalar(self, sql):
        conn = bridge.connect(self.db)
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()

    def call(self, method, path, who=None, body=None, extra=None):
        headers = dict(H)
        if who:
            headers["Authorization"] = (f"Bearer {self.tokens[who]}" if who in self.tokens
                                        else f"Bearer {who}")
        headers.update(extra or {})
        r = self.client.open(path, method=method, headers=headers, json=body)
        return r.status_code, r.get_json()

    def bc(self, i):
        return self.cadets[i]["barcode_string"]


def test1(rig):
    rows = []
    s = f"/v0/sessions/{DEMO_AFT_SESSION}/events"
    base = rig.counts()

    def attempt(name, expect, call):
        before = rig.counts()
        status, _ = call()
        after = rig.counts()
        wrote = {k: after[k] - before[k] for k in ("fitness_event", "fitness_component")}
        ok = status in expect and not any(wrote.values())
        rows.append((name, "/".join(map(str, expect)), status, "0 records" if ok else f"WROTE {wrote}",
                     ok))

    attempt("No role (no token) opens an AFT record", (401,), lambda: rig.call("POST", s, None, {"barcode": rig.bc(0)}))
    attempt("A made-up token", (401,), lambda: rig.call("POST", s, "not-a-real-token", {"barcode": rig.bc(0)}))
    attempt("Role claimed in a header, no token (X-Role: oic)", (401,),
            lambda: rig.call("POST", s, None, {"barcode": rig.bc(0)}, {"X-Role": "oic"}))
    attempt("Cadet token", (403,), lambda: rig.call("POST", s, "cadet", {"barcode": rig.bc(0)}))
    attempt("Cadet token claiming X-Role: oic", (403,),
            lambda: rig.call("POST", s, "cadet", {"barcode": rig.bc(0)}, {"X-Role": "oic"}))
    for role in ("gate_station", "compiler_station", "instructor"):
        attempt(f"Wrong role ({role}) opens an AFT record", (403,),
                lambda role=role: rig.call("POST", s, role, {"barcode": rig.bc(0)}))
    attempt("Cadet who should not be gradeable: barcode of nobody", (404,),
            lambda: rig.call("POST", s, "grader", {"barcode": "PDF417_TEST_DATA_1234567890_END"}))
    attempt("Cadet who should not be gradeable: not a barcode", (400,),
            lambda: rig.call("POST", s, "grader", {"barcode": "Alpha, Cadet"}))

    # one real record, then try to alter it
    st, out = rig.call("POST", s, "grader", {"barcode": rig.bc(0)})
    assert st == 201, out
    eid = out["event_id"]
    attempt("Second live score for the same cadet and session", (409,),
            lambda: rig.call("POST", s, "grader", {"barcode": rig.bc(0)}))
    attempt("Grader sends the points themselves (cheating the scoring)", (400,),
            lambda: rig.call("POST", f"/v0/events/{eid}/components", "grader",
                             {"component": "MDL", "raw_value": "340", "points": 100,
                              "age_group": "17-21", "scoring_column": "M|C"}))
    for comp, raw in (("MDL", "340"), ("HRP", "60"), ("SDC", "1:30"), ("PLK", "3:40"), ("2MR", "13:15")):
        st, _ = rig.call("POST", f"/v0/events/{eid}/components", "grader",
                         {"component": comp, "raw_value": raw, "age_group": "17-21",
                          "scoring_column": "M|C"})
        assert st == 200
    st, _ = rig.call("POST", f"/v0/events/{eid}/submit", "grader")
    assert st == 200
    attempt("Edit a saved record: change a result after submit", (409,),
            lambda: rig.call("POST", f"/v0/events/{eid}/components", "grader",
                             {"component": "MDL", "raw_value": "100", "age_group": "17-21",
                              "scoring_column": "M|C"}))
    for method in ("PUT", "PATCH", "DELETE"):
        attempt(f"{method} on a saved record", (405,),
                lambda m=method: rig.call(m, f"/v0/events/{eid}", "oic", {}))
    attempt("Grader reopens their own submitted record", (403,),
            lambda: rig.call("POST", f"/v0/events/{eid}/reopen", "grader", {"note": "change it"}))

    # every real record is in the audit log
    events = rig.scalar("SELECT COUNT(*) FROM fitness_event")
    comps = rig.scalar("SELECT COUNT(*) FROM fitness_component")
    opened = rig.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'EVENT_OPENED'")
    entered = rig.scalar("SELECT COUNT(*) FROM audit_log WHERE action IN ('COMPONENT_ENTERED','COMPONENT_CORRECTED')")
    submitted = rig.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'EVENT_SUBMITTED'")
    conn = bridge.connect(rig.db)
    chain = audit.verify(conn)
    conn.close()
    ok = (events == opened == 1 and comps == 5 and entered >= 5 and submitted == 1 and chain.ok)
    rows.append((f"Every real record is in the audit log ({events} event, {comps} results, "
                 f"{opened} opened, {entered} entered, {submitted} submitted entries)",
                 "all present, chain PASS", "chain PASS" if chain.ok else "chain FAIL",
                 "matches" if ok else "MISMATCH", ok))
    fakes = rig.counts()["fitness_event"] - 1 + rig.counts()["fitness_component"] - 5
    return rows, fakes


def test2(rig):
    rows = []
    before_marks = rig.counts()["attendance"]
    m = "/v0/attendance/marks"

    def mark(i, section, who):
        return rig.call("POST", m, who, {"section_id": section, "class_date": DAY, "barcode": rig.bc(i)})

    def attempt(name, expect, call, logged):
        before = rig.counts()
        status, _ = call()
        after = rig.counts()
        new_marks = after["attendance"] - before["attendance"]
        new_logs = after["audit_log"] - before["audit_log"]
        ok = status in expect and new_marks == 0 and new_logs == logged
        rows.append((name, "/".join(map(str, expect)), status,
                     f"{new_marks} records, {new_logs} audit entries", ok))

    for i in range(3):
        attempt(f"Instructor B marks cadet {i} in Instructor A's section", (403,),
                lambda i=i: mark(i, 1, "instructor_b"), 1)
    for i in (3, 5, 7):
        attempt(f"Instructor B marks cadet {i}, who is not on B's roster", (403,),
                lambda i=i: mark(i, 2, "instructor_b"), 1)
    attempt("Instructor A marks a barcode for nobody", (403,),
            lambda: rig.call("POST", m, "instructor", {"section_id": 1, "class_date": DAY,
                             "barcode": "PDF417_TEST_DATA_1234567890_END"}), 1)
    attempt("Instructor B reads the roster of Instructor A's section", (403,),
            lambda: rig.call("GET", f"/v0/attendance/sections/1/roster?class_date={DAY}", "instructor_b"), 1)
    attempt("A section that does not exist", (404,), lambda: mark(0, 99, "instructor"), 1)
    for who in ("cadet", "grader", "oic", "admin", "gate_station"):
        attempt(f"Not an instructor ({who}) marks attendance", (403,),
                lambda who=who: mark(0, 1, who), 0)
    attempt("Impersonation: no token, X-Role: instructor", (401,),
            lambda: rig.call("POST", m, None, {"section_id": 1, "class_date": DAY, "barcode": rig.bc(0)},
                             {"X-Role": "instructor"}), 0)
    attempt("Made-up token", (401,),
            lambda: rig.call("POST", m, "nope", {"section_id": 1, "class_date": DAY, "barcode": rig.bc(0)}), 0)
    false_marks = rig.counts()["attendance"] - before_marks
    return rows, false_marks


def table(rows):
    lines = ["| # | Attempt | Expected | Got | Result | Pass |", "|---|---|---|---|---|---|"]
    for n, (name, exp, got, res, ok) in enumerate(rows, 1):
        lines.append(f"| {n} | {name} | {exp} | {got} | {res} | {'PASS' if ok else 'FAIL'} |")
    return "\n".join(lines)


def main():
    rig = Rig()
    r1, fakes1 = test1(rig)
    r2, fakes2 = test2(rig)
    p1, p2 = all(r[-1] for r in r1) and fakes1 == 0, all(r[-1] for r in r2) and fakes2 == 0
    text = f"""# Gatekeeper security Tests 1 and 2: results

Run once on {date.today().isoformat()} by `python run_security_tests.py` (gatekeeper/api). Synthetic data only, a throwaway encrypted Vault database, the real Gatekeeper and Vault code. Re-run any time; this file is regenerated.

## Test 1: Impersonation and fake record attempts (SR-1, SR-3)

Pass means the system blocks every attempt, no fake record gets in, and every real record shows up in the audit log.

{table(r1)}

Fake records accepted: **{fakes1}**. Test 1 result: **{'PASS' if p1 else 'FAIL'}** ({sum(r[-1] for r in r1)} of {len(r1)} checks).

Not covered here (the arms-room stretch is not built): the wrong-PIN and missing-signature cases.

## Test 2: Attendance impersonation and out-of-section writes (SR-2)

Pass means attempts to mark attendance as someone who is not the section's instructor, or for a cadet not on the roster, are rejected and logged, with zero false attendance records accepted.

{table(r2)}

False attendance records accepted: **{fakes2}**. Test 2 result: **{'PASS' if p2 else 'FAIL'}** ({sum(r[-1] for r in r2)} of {len(r2)} checks).

A rejection that is stopped by the role check at the Gatekeeper (a cadet, a grader, no token) is refused before anything reaches the Vault and writes 0 audit entries by design, so it is not logged. A rejection of a real instructor (wrong section, not on the roster) writes one rejected-attempt audit entry that holds the section, the date and the reason only.

## Honest limits

- Staff sign in with demo bearer tokens. Real CAC sign-in is not built, so "impersonation" here means no token, a made-up token, a cadet token, a wrong role and a role claimed in a header.
- Cadet barcodes use the synthetic test format. The real CAC parser belongs to Edge.
- Cadet signing at the compiler station and exports are not part of these tests.
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    rig.tmp.cleanup()
    return 0 if (p1 and p2) else 1


if __name__ == "__main__":
    sys.exit(main())
