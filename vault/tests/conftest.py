"""Shared test setup. SYNTHETIC DATA ONLY — no real names, EDIPIs, or CAC keys.

`FakeCAC` stands in for a real CAC: it holds a software RSA-2048 key and signs
with PKCS#1 v1.5 / SHA-256, the same format a real PIV Authentication key produces
via `pkcs11-tool --sign --mechanism SHA256-RSA-PKCS`. Real-card testing is still
pending (non-GFE laptop).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from muster_vault import db, fitness, identity, ioct

TEST_HMAC_KEY = b"test-only-key-not-for-production-0123456789"
# Database key for tests that use a real file. Synthetic, never a real secret.
TEST_DB_KEY = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def dump_all_rows(conn) -> str:
    """Every value in every table, as one string (SQLCipher has no iterdump())."""
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")]
    parts = []
    for t in tables:
        for row in conn.execute(f'SELECT * FROM "{t}"'):
            parts.extend(str(v) for v in tuple(row))
    return "\n".join(parts)


class FakeCAC:
    def __init__(self, edipi: str, kind: str = "rsa"):
        self.edipi = edipi
        self.edipi_hash = identity.hash_edipi(edipi, TEST_HMAC_KEY)
        self.kind = kind
        if kind == "rsa":
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_pem(self) -> str:
        return self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def sign(self, payload: bytes) -> bytes:
        if self.kind == "rsa":
            return self._key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return self._key.sign(payload, ec.ECDSA(hashes.SHA256()))


@dataclass
class World:
    conn: object
    admin: int
    oic: int
    grader: int
    station: int
    gate: int
    instructor: int
    nco: int
    grader_cac: FakeCAC
    cadets: dict = field(default_factory=dict)      # name -> (cadet_id, FakeCAC)
    aft_session: int = 0
    ioct_session: int = 0


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def world(conn) -> World:
    return build_world(conn)


def build_world(conn) -> World:
    """Staff, three synthetic cadets, two sessions, three tags — on any connection."""
    admin = identity.bootstrap_admin(conn, "Admin (synthetic)")
    grader_cac = FakeCAC("2000000001")
    w = World(
        conn=conn,
        admin=admin,
        oic=identity.add_user(conn, admin, "OIC Test", "oic"),
        grader=identity.add_user(conn, admin, "Grader Test", "grader",
                                 edipi_hash=grader_cac.edipi_hash),
        station=identity.add_user(conn, admin, "Compiler Station 1", "compiler_station"),
        gate=identity.add_user(conn, admin, "Gate Controller 1", "gate_station"),
        instructor=identity.add_user(conn, admin, "Instructor Test", "instructor"),
        nco=identity.add_user(conn, admin, "Arms NCO Test", "arms_nco"),
        grader_cac=grader_cac,
    )
    identity.enroll_key(conn, admin, grader_cac.edipi_hash, grader_cac.public_pem)
    company = identity.add_company(conn, admin, "E-2")
    for i, (last, first) in enumerate([("Alpha", "Ann"), ("Bravo", "Ben"),
                                       ("Charlie", "Cal")], start=1):
        cac = FakeCAC(f"100000000{i}")
        cid = identity.add_cadet(conn, admin, cac.edipi_hash, last, first, company, 2027)
        identity.enroll_key(conn, admin, cac.edipi_hash, cac.public_pem)
        w.cadets[last.lower()] = (cid, cac)
    w.aft_session = fitness.open_session(conn, w.oic, "AFT", "2026-10-15", "Arvin Gym")
    w.ioct_session = fitness.open_session(conn, w.oic, "IOCT", "2026-10-16", "Hayes Gym")
    for n in range(1, 4):
        ioct.register_tag(conn, w.oic, f"TAG-{n:03d}", label=str(n))
    return w


AFT_SAMPLE = {"MDL": ("200", 70), "HRP": ("40", 75), "SDC": ("1:55", 80),
              "PLK": ("2:30", 72), "2MR": ("15:30", 85)}


def entered_aft(w: World, who: str = "alpha", client_ref: str | None = None) -> int:
    _, cac = w.cadets[who]
    ev = fitness.open_event(w.conn, w.grader, w.aft_session, cac.edipi_hash, client_ref)
    for comp, (raw, pts) in AFT_SAMPLE.items():
        fitness.record_component(w.conn, w.grader, ev, comp, raw, pts)
    return ev


def submitted_aft(w: World, who: str = "alpha") -> int:
    ev = entered_aft(w, who)
    fitness.submit(w.conn, w.grader, ev)
    return ev


def signed_aft(w: World, who: str = "alpha") -> int:
    ev = submitted_aft(w, who)
    _, cac = w.cadets[who]
    ch, payload = fitness.request_cadet_challenge(w.conn, w.station, ev)
    fitness.accept(w.conn, w.station, ev, ch, cac.sign(payload))
    return ev
