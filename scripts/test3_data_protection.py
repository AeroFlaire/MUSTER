"""Security Test 3 (SR-4): data in transit and at rest. Synthetic data only.

Pass = encrypted on the wire and on disk, no full DoD ID stored, only needed info kept.

What it does, start to finish, with nothing to set up:
  1. Builds a fresh app database with the fake test cards (9000000001 to 13), the same way
     start_muster_demo.bat does, and adds one class section with three attendance marks.
  2. Starts the real Gatekeeper app over HTTPS on a spare port (same TLS setup as
     start_app.ps1 -Https), with a small relay in front that records every byte on the wire.
  3. Uses the app the way the phone does: grader and OIC sign in by card, three AFT scores,
     one OIC correction, one IOCT run.
  4. IN TRANSIT: checks the TLS version, that TLS 1.2 and plain HTTP are refused, and that no
     DoD ID, name or token can be read in the recorded traffic.
  5. AT REST: reads the database file as raw bytes and checks there is no readable header,
     ID, name, score or ID hash, that it can't be opened without the key, and that inside it
     only the keyed hash of an ID is kept.
  6. Runs the AMS export and checks the files hold no ID or ID hash.

Writes docs/evidence/test3-results.md and docs/evidence/sample-export/. Exit code 1 if any
check fails.

    python scripts/test3_data_protection.py          (from the repo root)
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3 as plain_sqlite   # the standard module: plays an outsider with no key
import ssl
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API = REPO / "gatekeeper" / "api"
for p in (REPO / "vault", API):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Throwaway keys for this run only (never the real ones in .app_keys).
os.environ["MUSTER_EDIPI_KEY"] = secrets.token_hex(32)
os.environ["MUSTER_DB_KEY"] = secrets.token_hex(32)

import live_app  # noqa: E402
import vault_bridge as bridge  # noqa: E402
from muster_vault import attendance, audit, export, identity  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

CARDS = json.loads((REPO / "edge" / "scanner" / "tests" / "synthetic_front.json")
                   .read_text(encoding="utf-8"))
EDIPIS = [c["edipi"] for c in CARDS]
NAMES = sorted({c["expect"]["lastName"] for c in CARDS if c["expect"].get("lastName")})
EVIDENCE = REPO / "docs" / "evidence"
H = {"X-Contract-Version": "v1", "Content-Type": "application/json"}
CTX = {"age_group": "17-21", "scoring_column": "M|C"}

results: list[tuple[str, str, bool, str]] = []   # (part, check, passed, detail)


def check(part, name, passed, detail=""):
    results.append((part, name, bool(passed), detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


# ---- a relay that records every byte between the client and the server --------------

class WireTap:
    def __init__(self, target_port):
        self.target = target_port
        self.bytes = bytearray()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _pipe(self, src, dst):
        try:
            while data := src.recv(65536):
                self.bytes += data
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _serve(self):
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            server = socket.create_connection(("127.0.0.1", self.target))
            threading.Thread(target=self._pipe, args=(client, server), daemon=True).start()
            threading.Thread(target=self._pipe, args=(server, client), daemon=True).start()


def client_ctx(max_version=None):
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE       # the app's certificate is self-signed
    if max_version:
        c.minimum_version = ssl.TLSVersion.TLSv1_2
        c.maximum_version = max_version
    return c


def call(port, method, path, body=None, token=None):
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=client_ctx(), timeout=10)
    headers = dict(H)
    if token:
        headers["Authorization"] = "Bearer " + token
    conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = conn.getresponse()
    data = json.loads(r.read() or b"{}")
    conn.close()
    return r.status, data


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="muster-test3-"))
    db_path = work / "muster_app.db"
    server_log = []

    print("Building a fresh app database with the fake test cards...")
    live_app.seed_app(db_path, synthetic=True)
    conn = bridge.connect(db_path)
    admin = conn.execute("SELECT id FROM app_user WHERE role = 'admin'").fetchone()["id"]
    instr = identity.add_user(conn, admin, "Instructor, Test", "instructor")
    sec = attendance.add_section(conn, admin, "XE401", "G1", "B hour", "2027-1", instr)
    key = identity.load_edipi_key()
    for e, st in (("9000000001", "PRESENT"), ("9000000002", "LATE"), ("9000000003", "ABSENT")):
        h = identity.hash_edipi(e, key)
        attendance.enroll(conn, admin, sec, identity.cadet_by_hash(conn, h)["id"])
        attendance.mark(conn, instr, sec, h, "2026-11-19", st)
    conn.close()

    store, tokens = live_app.app_tokens(db_path)
    app = live_app.create_live_app(db_path, store)
    handler = logging.Handler()
    handler.emit = lambda rec: server_log.append(rec.getMessage())
    logging.getLogger("werkzeug").addHandler(handler)
    app.logger.addHandler(handler)
    tls = live_app.tls_context()
    server = make_server("127.0.0.1", 0, app, threaded=True, ssl_context=tls)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tap = WireTap(port)
    time.sleep(0.3)

    print("Using the app over HTTPS like the phone does...")
    s, g = call(tap.port, "POST", "/v0/auth/cac-signin", {"edipi": "9000000011", "position": "grader"})
    grader = g.get("token")
    s2, o = call(tap.port, "POST", "/v0/auth/cac-signin", {"edipi": "9000000012", "position": "grader"})
    oic = o.get("token")
    events = []
    raw = [("MDL", "340"), ("HRP", "60"), ("SDC", "1:30"), ("PLK", "3:40"), ("2MR", "13:15")]
    for e in ("9000000001", "9000000002", "9000000003"):
        _, ev = call(tap.port, "POST", "/v0/sessions/1/events", {"edipi": e}, grader)
        for comp, v in raw:
            call(tap.port, "POST", f"/v0/events/{ev['event_id']}/components",
                 {"component": comp, "raw_value": v, **CTX}, grader)
        call(tap.port, "POST", f"/v0/events/{ev['event_id']}/submit", None, grader)
        events.append(ev["event_id"])
    _, sup = call(tap.port, "POST", f"/v0/events/{events[0]}/supersede",
                  {"reason": "Wrong deadlift weight"}, oic)
    call(tap.port, "POST", f"/v0/events/{sup['new_event_id']}/components",
         {"component": "MDL", "raw_value": "300", **CTX}, oic)
    call(tap.port, "POST", f"/v0/events/{sup['new_event_id']}/submit", None, oic)
    _, pair = call(tap.port, "POST", "/v0/sessions/2/ioct/pair",
                   {"edipi": "9000000004", "tag_uid": "TAG-017"}, grader)
    t0 = int(time.time() * 1000) + 1000
    for gate, t in (("START", t0), ("END", t0 + 155_000)):
        call(tap.port, "POST", "/v0/sessions/2/gate-reads",
             {"tag_uid": "TAG-017", "gate": gate, "read_at_ms": t, "gate_device": gate},
             tokens["gate_station"])
    call(tap.port, "POST", f"/v0/events/{pair['event_id']}/ioct/compute", None, grader)
    s3, done = call(tap.port, "POST", f"/v0/events/{pair['event_id']}/submit", None, grader)
    used_ok = s == 200 and s2 == 200 and s3 == 200 and grader and oic
    print(f"  app traffic: {'ok' if used_ok else 'FAILED'} "
          f"({len(events)} AFT scores, 1 correction, 1 IOCT run)")

    # ---------------- IN TRANSIT ----------------
    print("In transit:")
    with client_ctx().wrap_socket(socket.create_connection(("127.0.0.1", port))) as t:
        version, cipher = t.version(), t.cipher()[0]
    check("transit", "HTTPS uses TLS 1.3", version == "TLSv1.3", f"{version}, {cipher}")
    try:
        with client_ctx(ssl.TLSVersion.TLSv1_2).wrap_socket(
                socket.create_connection(("127.0.0.1", port))) as t:
            old = t.version()
        check("transit", "A client limited to TLS 1.2 is refused", False, f"accepted {old}")
    except (ssl.SSLError, OSError) as e:
        check("transit", "A client limited to TLS 1.2 is refused", True,
              type(e).__name__)
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/v0/health", headers=H)
        status = c.getresponse().status
        check("transit", "Plain HTTP (no encryption) gets no answer", False, f"HTTP {status}")
    except (http.client.HTTPException, OSError) as e:
        check("transit", "Plain HTTP (no encryption) gets no answer", True, type(e).__name__)
    wire = bytes(tap.bytes)
    leaks = [x for x in EDIPIS if x.encode() in wire]
    check("transit", f"Recorded traffic ({len(wire):,} bytes) has no readable DoD ID",
          used_ok and not leaks and len(wire) > 10_000, ", ".join(leaks) or "0 of 13 IDs found")
    name_leaks = [n for n in NAMES if n.encode() in wire]
    check("transit", "Recorded traffic has no readable cadet or staff name", not name_leaks,
          ", ".join(name_leaks) or f"0 of {len(NAMES)} names found")
    tok_leaks = [k for k in (grader, oic) if k and k.encode() in wire]
    check("transit", "Recorded traffic has no readable sign-in token", not tok_leaks,
          f"{len(tok_leaks)} of 2 found")
    check("transit", "Recorded traffic has no readable HTTP (headers or JSON)",
          b"Authorization" not in wire and b"edipi" not in wire and b"HTTP/1.1" not in wire)
    log_text = "\n".join(server_log)
    check("transit", "Server log has no DoD ID or token",
          not any(x in log_text for x in EDIPIS) and grader not in log_text,
          f"{len(server_log)} log lines checked")

    server.shutdown()

    # ---------------- AT REST ----------------
    print("At rest:")
    files = [p for p in work.iterdir() if p.name.startswith("muster_app.db")]
    raw_bytes = b"".join(p.read_bytes() for p in files)
    check("rest", "Database file has no readable 'SQLite format 3' header",
          not db_path.read_bytes().startswith(b"SQLite format 3"),
          f"first bytes {db_path.read_bytes()[:8].hex()}")
    conn = bridge.connect(db_path)
    hashes = [r[0] for r in conn.execute(
        "SELECT edipi_hash FROM cadet UNION SELECT edipi_hash FROM app_user "
        "WHERE edipi_hash IS NOT NULL")]
    leaks = [x for x in EDIPIS if x.encode() in raw_bytes]
    check("rest", f"Raw file bytes ({len(raw_bytes):,}) have no DoD ID", not leaks,
          ", ".join(leaks) or "0 of 13 IDs found")
    name_leaks = [n for n in NAMES if n.encode() in raw_bytes]
    check("rest", "Raw file bytes have no name", not name_leaks,
          ", ".join(name_leaks) or f"0 of {len(NAMES)} names found")
    check("rest", "Raw file bytes have no ID hash and no score",
          not any(h.encode() in raw_bytes for h in hashes) and b"13:15" not in raw_bytes,
          f"{len(hashes)} hashes and the 2-mile time checked")
    try:
        outsider = plain_sqlite.connect(str(db_path))
        outsider.execute("SELECT * FROM cadet").fetchall()
        check("rest", "Opening without the key fails", False, "opened")
    except plain_sqlite.DatabaseError as e:
        check("rest", "Opening without the key fails", True, str(e))
    # Inside the database (with the key): IDs only as keyed hashes, nothing extra kept.
    every_value = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        for row in conn.execute(f'SELECT * FROM "{t}"'):
            every_value += [str(v) for v in tuple(row) if v is not None]
    blob = "\n".join(every_value)
    leaks = [x for x in EDIPIS if x in blob]
    check("rest", "With the key: no DoD ID in any table, including the audit log", not leaks,
          f"{len(every_value):,} values checked")
    hash_ok = all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
    known = identity.hash_edipi("9000000001", identity.load_edipi_key())
    check("rest", "IDs are kept only as keyed hashes (HMAC-SHA256)",
          hash_ok and known in hashes, f"{len(hashes)} hashes, all 64 hex characters")
    cadet_cols = [r[1] for r in conn.execute("PRAGMA table_info(cadet)")]
    extra = {c for c in cadet_cols} - {"id", "edipi_hash", "last_name", "first_name",
                                       "company_id", "class_year", "active", "created_at"}
    check("rest", "Only needed details kept about a cadet (no date of birth or card dates)",
          not extra and not any("birth" in c or "dob" in c for c in cadet_cols),
          ", ".join(cadet_cols))
    check("rest", "Audit chain still verifies", audit.verify(conn).ok, str(audit.verify(conn)))

    # ---------------- EXPORT ----------------
    print("Export file:")
    oic_id = conn.execute("SELECT id FROM app_user WHERE edipi_hash = ?",
                          (identity.hash_edipi("9000000012", identity.load_edipi_key()),)
                          ).fetchone()["id"]
    m = export.export_csv(conn, oic_id, str(work))
    conn.close()
    blob = "".join(open(os.path.join(m["folder"], f), encoding="utf-8").read()
                   for f in os.listdir(m["folder"]))
    check("export", "AMS export has no DoD ID and no ID hash",
          not any(x in blob for x in EDIPIS) and not any(h in blob for h in hashes),
          ", ".join(f"{k} {v['rows']} rows" for k, v in m["files"].items()))
    sample = EVIDENCE / "sample-export"
    shutil.rmtree(sample, ignore_errors=True)
    shutil.copytree(m["folder"], sample)

    shutil.rmtree(work, ignore_errors=True)
    write_report(m)
    failed = [r for r in results if not r[2]]
    print(f"\nTest 3: {'PASS' if not failed else 'FAIL'} "
          f"({len(results) - len(failed)} of {len(results)} checks passed)")
    print(f"Results: {EVIDENCE / 'test3-results.md'}")
    print(f"Sample export: {sample}")
    return 1 if failed else 0


def write_report(m):
    failed = [r for r in results if not r[2]]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Security Test 3: data in transit and at rest (SR-4)",
        "",
        f"Run: {when}, by `python scripts/test3_data_protection.py`. Synthetic data only "
        "(fake cards 9000000001 to 13). Throwaway keys, made for this run and thrown away.",
        "",
        f"**Result: {'PASS' if not failed else 'FAIL'}** "
        f"({len(results) - len(failed)} of {len(results)} checks passed)",
        "",
        "Pass rule (backlog card Test 3): encrypted, no full DoD ID stored, only needed info kept.",
        "",
        "How: the script starts the real Gatekeeper app over HTTPS with the same TLS setup as "
        "`start_app.ps1 -Https`, puts a relay in front of it that records every byte on the "
        "wire, and uses the app the way the phone does (grader and OIC sign in by card, three "
        "AFT scores, one OIC correction, one IOCT run). Then it reads the database file as raw "
        "bytes, opens it with and without the key, and runs the AMS export.",
        "",
    ]
    for part, title in (("transit", "In transit"), ("rest", "At rest"),
                        ("export", "Export file")):
        out += [f"## {title}", "", "| Check | Result | Detail |", "|---|---|---|"]
        for p, name, ok, detail in results:
            if p == part:
                out.append(f"| {name} | {'PASS' if ok else '**FAIL**'} | {detail} |")
        out.append("")
    out += [
        "## Found and fixed while writing this test",
        "",
        "The app's HTTPS setting (`ssl_context=\"adhoc\"`) also accepted TLS 1.2, and SR-4 says "
        "TLS 1.3. `live_app.tls_context()` now sets TLS 1.3 as the minimum, and the app uses it. "
        "Every phone that can run the scanner supports TLS 1.3.",
        "",
        "## Limits, stated plainly",
        "",
        "- The certificate is self-signed and made fresh at each start, so phones warn once per "
        "start, and the phone cannot tell the real laptop from an impostor on the same network. "
        "A real deployment needs a certificate the phones trust (for example a DoD-issued one).",
        "- The relay records traffic on the laptop itself (127.0.0.1). It shows what anyone on "
        "the network would see: TLS records only.",
        "- At rest covers the database file. The key file `.app_keys` sits next to it on the same "
        "laptop; anyone with both can read everything (see `scripts/admin_tamper.py`, and the "
        "audit check catches what they change).",
        "",
        f"Sample export from this run: `docs/evidence/sample-export/` "
        f"(batch {m['batch_id']}).",
        "",
    ]
    (EVIDENCE / "test3-results.md").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
