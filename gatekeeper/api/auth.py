"""Who is calling? (PBI-05 L25, SR-1)

The role never comes from the caller. Edge is untrusted, so a header like X-Role proves
nothing: anyone can type "oic". Instead:

  1. Every live write carries `Authorization: Bearer <token>`.
  2. The Gatekeeper looks the token up (only SHA-256 hashes of tokens are stored, never
     the tokens) and gets a Vault app_user id, or no id for a cadet.
  3. The role is then read from the Vault's own app_user row, so a role change or a
     deactivation in the Vault takes effect on the very next request.
  4. The Vault checks the role once more inside every write (defense in depth).

No token, a bad token, or a malformed header: 401, and nothing reaches the Vault.
A cadet token, or a user whose role is not allowed here or who is inactive: 403, and
nothing reaches the Vault.

Cadets are not Vault users (they sign with their CAC at the score compiler), so a cadet
credential carries no Vault user id and is refused on every write endpoint. A cadet
credential may carry the cadet's keyed ID hash (never the raw ID). That is what lets the
cadet read their OWN results and nobody else's (GET /v0/results).

DEV NOTE: tokens come from a credentials file made by `live_app.py --reset`. How real
staff get tokens (CAC sign-in at the Gatekeeper) is a later card.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

CADET = "cadet"


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    label: str
    actor_id: int | None  # Vault app_user id; None for a cadet
    cadet_hash: str | None = None  # a cadet's keyed ID hash (never the raw ID); None for staff


class CredentialStore:
    """token hash -> Principal. Holds hashes only."""

    def __init__(self):
        self._by_hash = {}

    def add(self, token, label, actor_id, cadet_hash=None):
        self._by_hash[hash_token(token)] = Principal(label, actor_id, cadet_hash)

    def lookup(self, token):
        return self._by_hash.get(hash_token(token))

    def issue(self, label, actor_id, cadet_hash=None):
        token = secrets.token_urlsafe(32)
        self.add(token, label, actor_id, cadet_hash)
        return token

    def save(self, path):
        rows = [{"token_sha256": h, "label": p.label, "actor_id": p.actor_id,
                 "cadet_hash": p.cadet_hash}
                for h, p in self._by_hash.items()]
        Path(path).write_text(json.dumps({"credentials": rows}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        store = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data["credentials"]:
            store._by_hash[row["token_sha256"]] = Principal(row["label"], row["actor_id"],
                                                     row.get("cadet_hash"))
        return store


def authenticate(headers, store):
    """Principal for a valid `Authorization: Bearer <token>`, else None."""
    value = headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme != "Bearer" or not token.strip():
        return None
    return store.lookup(token.strip())
