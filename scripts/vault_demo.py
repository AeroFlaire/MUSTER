"""MUSTER Vault demo — synthetic data only.

Walks one AFT score and one IOCT run end to end, then plays the attacker:
edits a signed score directly in the database and edits the audit log,
and shows both being caught.

Run from the repo root:
    python scripts/vault_demo.py

Software keys stand in for CACs. Real-card signing has NOT been tested yet
(pending a non-GFE laptop for OpenSC).
"""
from __future__ import annotations

import os
import sys
import tempfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from muster_vault import audit, clock, db, export, fitness, identity, ioct, receipts

DEMO_KEY = b"demo-only-hmac-key-never-use-in-production"


class FakeCAC:
    """Software stand-in for a CAC's PIV Authentication key (RSA-2048)."""

    def __init__(self, edipi: str):
        self.edipi_hash = identity.hash_edipi(edipi, DEMO_KEY)
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def sign(self, payload: bytes) -> bytes:
        return self._key.sign(payload, padding.PKCS1v15(), hashes.SHA256())


def step(text: str) -> None:
    print(f"\n== {text}")


def main() -> int:
    path = os.path.join(tempfile.mkdtemp(), "muster-demo.db")
    conn = db.connect(path)
    print(f"Demo database: {path}")

    step("Setup: staff, two synthetic cadets, CAC keys enrolled")
    admin = identity.bootstrap_admin(conn, "Demo Admin")
    oic = identity.add_user(conn, admin, "Demo OIC", "oic")
    grader = identity.add_user(conn, admin, "Demo Grader", "grader")
    station = identity.add_user(conn, admin, "Score Compiler 1", "compiler_station")
    gate = identity.add_user(conn, admin, "IOCT Gates", "gate_station")
    cadets = {}
    for edipi, last, first in [("1000000001", "Doe", "Jane"), ("1000000002", "Roe", "Rick")]:
        cac = FakeCAC(edipi)
        identity.add_cadet(conn, admin, cac.edipi_hash, last, first, class_year=2027)
        identity.enroll_key(conn, admin, cac.edipi_hash, cac.public_pem)
        cadets[last] = cac
    print("  stored: HMAC of each EDIPI (never the raw ID) + each card's public key")

    step("AFT: grader scans Cadet Doe's CAC barcode and enters results")
    aft = fitness.open_session(conn, oic, "AFT", "2026-10-15", "Arvin Gym")
    ev = fitness.open_event(conn, grader, aft, cadets["Doe"].edipi_hash)
    for comp, raw, pts in [("MDL", "210", 72), ("HRP", "42", 76), ("SDC", "1:52", 82),
                           ("PLK", "2:40", 75), ("2MR", "15:10", 88)]:
        fitness.record_component(conn, grader, ev, comp, raw, pts)
    fitness.submit(conn, grader, ev)
    print(f"  event {ev} SUBMITTED to score compiler")

    step("Score compiler: Cadet Doe reviews and signs with CAC + PIN")
    ch, payload = fitness.request_cadet_challenge(conn, station, ev)
    print(f"  bytes to sign ({len(payload)} bytes) include score + one-time nonce")
    fitness.accept(conn, station, ev, ch, cadets["Doe"].sign(payload))
    print(receipts.render_text(receipts.issue(conn, station, ev)))

    step("IOCT: Cadet Roe gets wristband TAG-017; gates send raw reads")
    io = fitness.open_session(conn, oic, "IOCT", "2026-10-16", "Hayes Gym")
    ioct.register_tag(conn, oic, "TAG-017", "17")
    run = ioct.pair(conn, grader, io, cadets["Roe"].edipi_hash, "TAG-017")
    t0 = clock.now_ms() + 1000
    for t, g in [(t0, "START"), (t0 + 400, "START"), (t0 + 172_480, "END"),
                 (t0 + 172_900, "END")]:
        ioct.record_gate_read(conn, gate, io, "TAG-017", g, t, "G1" if g == "START" else "G2")
    ms = ioct.compute_run(conn, grader, run)
    print(f"  Vault-computed time: {fitness.format_elapsed(ms)} "
          "(last START read -> first END read)")
    fitness.submit(conn, grader, run)
    run_found = ioct.active_event_for_tag(conn, io, "TAG-017")
    ch, payload = fitness.request_cadet_challenge(conn, station, run_found)

    step("ATTACK 0: Cadet Doe tries to sign Cadet Roe's time with Doe's own CAC")
    try:
        fitness.accept(conn, station, run_found, ch, cadets["Doe"].sign(payload))
    except Exception as exc:  # SignatureInvalid
        print(f"  refused: {exc}  (attempt written to the audit log)")

    fitness.accept(conn, station, run_found, ch, cadets["Roe"].sign(payload))
    print("  Cadet Roe signed with the right card; wristband released")

    step("Export signed scores (file stand-in until AMS/Azimuth RFI is answered)")
    out = export.export_signed(conn, oic, export.JsonFileExporter(os.path.dirname(path)))
    print(f"  exported {out['exported']} scores -> {out['reference']}")

    step("Integrity checks — everything clean")
    print(f"  audit chain: {audit.verify(conn)}")
    print(f"  score {ev} matches what the cadet signed: {fitness.verify_event(conn, ev)['ok']}")

    step("ATTACK 1: someone edits Doe's 2-mile points directly in SQL (88 -> 100)")
    conn.execute("UPDATE fitness_component SET points = 100 "
                 "WHERE event_id = ? AND component = '2MR'", (ev,))
    print(f"  score check: {fitness.verify_event(conn, ev)}")

    step("ATTACK 2: drop the append-only locks and rewrite an audit entry")
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET data = '{\"points\":100}' WHERE seq = 20")
    print(f"  audit chain: {audit.verify(conn)}")

    print("\nBoth edits were possible — and both were caught. That is tamper-EVIDENCE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
