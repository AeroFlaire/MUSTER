"""CAC signature checking against an ENROLLED public key.

The question we answer is NOT "is this a valid DoD card?" (that would need live
DoD PKI). It is: "was this signed by the same card that was enrolled, by someone
who knew its PIN?" — the card only signs after the PIN is entered on it.

On a real CAC the PIV Authentication key is normally RSA-2048, and
`pkcs11-tool --sign --mechanism SHA256-RSA-PKCS` produces exactly the
'RSA-PKCS1v15-SHA256' format below — the same one `openssl dgst -sha256 -verify`
checks. ECDSA is supported for newer cards.

Honesty note: this code is tested with SOFTWARE keys that stand in for a CAC.
It has not yet been run against a physical card (needs a non-GFE laptop).
"""
from __future__ import annotations

import hashlib

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .errors import SignatureInvalid

ALGORITHMS = {"RSA-PKCS1v15-SHA256", "ECDSA-SHA256", "ECDSA-SHA384"}


def load_public_key(pem: str):
    """Accept either a certificate PEM (as read off the CAC) or a public-key PEM."""
    data = pem.encode("ascii") if isinstance(pem, str) else pem
    if b"BEGIN CERTIFICATE" in data:
        return x509.load_pem_x509_certificate(data).public_key()
    return serialization.load_pem_public_key(data)


def public_key_pem(pem: str) -> str:
    """Normalize to a bare public-key PEM (what we store)."""
    key = load_public_key(pem)
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def fingerprint(pem: str) -> str:
    """SHA-256 of the DER-encoded public key. Same card -> same fingerprint."""
    key = load_public_key(pem)
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def default_algorithm(pem: str) -> str:
    key = load_public_key(pem)
    if isinstance(key, rsa.RSAPublicKey):
        return "RSA-PKCS1v15-SHA256"
    if isinstance(key, ec.EllipticCurvePublicKey):
        return "ECDSA-SHA384" if key.curve.key_size >= 384 else "ECDSA-SHA256"
    raise ValueError("unsupported key type (need RSA or EC)")


def verify_signature(pem: str, payload: bytes, signature: bytes, algorithm: str) -> None:
    """Raise SignatureInvalid unless `signature` is valid over `payload`."""
    if algorithm not in ALGORITHMS:
        raise SignatureInvalid(f"unsupported algorithm {algorithm!r}")
    key = load_public_key(pem)
    try:
        if algorithm == "RSA-PKCS1v15-SHA256":
            if not isinstance(key, rsa.RSAPublicKey):
                raise SignatureInvalid("key is not RSA")
            key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
        else:
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise SignatureInvalid("key is not EC")
            h = hashes.SHA384() if algorithm == "ECDSA-SHA384" else hashes.SHA256()
            key.verify(signature, payload, ec.ECDSA(h))
    except InvalidSignature as exc:
        raise SignatureInvalid("signature does not match the enrolled key") from exc
