"""Canonical bytes: one, and only one, way to turn a record into bytes.

Why this matters: a hash or a signature is over BYTES, not over "the same data".
{"a":1,"b":2} and {"b": 2, "a": 1} mean the same thing but hash differently.
So everything we hash or sign goes through `canonical_bytes` — sorted keys,
no spaces, UTF-8 — and the result is reproducible years later.

Signing payloads also carry a `muster` version tag and a `purpose`, so a
signature made to accept a fitness score can never be replayed as a weapon
sign-out (the purpose is inside the signed bytes).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

PAYLOAD_VERSION = "muster-sig-v1"


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_text(obj: Any) -> str:
    return canonical_bytes(obj).decode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signing_payload(purpose: str, nonce: str, issued_at: str, record: dict) -> bytes:
    """The exact bytes a cadet / grader / NCO signs with their CAC."""
    return canonical_bytes(
        {
            "muster": PAYLOAD_VERSION,
            "purpose": purpose,
            "nonce": nonce,
            "issued_at": issued_at,
            "record": record,
        }
    )
