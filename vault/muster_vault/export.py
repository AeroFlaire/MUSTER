"""Sending signed scores out of the Vault (to DPE's system / AMS).

How MUSTER actually connects to AMS / Azimuth is an OPEN RFI with G6. So the
Vault talks to a small adapter interface (`Exporter`). Today there's a file
exporter for demos; when the RFI is answered, write one new adapter class —
nothing else in the Vault changes.

Every exported item carries the signed bytes and the signature, so the
receiving side can verify a score without trusting MUSTER.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
from typing import Protocol

from . import audit, clock, fitness, identity
from .canonical import canonical_bytes, sha256_hex
from .db import one, transaction


class Exporter(Protocol):
    name: str

    def send(self, batch: dict) -> str:
        """Deliver the batch. Return a receipt/reference from the other side.
        Raise on failure — nothing is marked EXPORTED unless send() returns."""


class JsonFileExporter:
    """Writes each batch to a JSON file. Stand-in until the AMS method is decided."""

    name = "json-file"

    def __init__(self, out_dir: str):
        self.out_dir = out_dir

    def send(self, batch: dict) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"muster-export-{batch['batch_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch, f, indent=2, sort_keys=True)
        return path


def _b64(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("ascii")


def pending(conn: sqlite3.Connection, test_type: str | None = None) -> list[int]:
    sql = "SELECT id FROM fitness_event WHERE status = 'SIGNED'"
    params: tuple = ()
    if test_type:
        sql += " AND test_type = ?"
        params = (test_type,)
    return [r["id"] for r in conn.execute(sql + " ORDER BY id", params)]


def build_item(conn: sqlite3.Connection, event_id: int) -> dict:
    ev = fitness.get_event(conn, event_id)
    att = one(conn, "SELECT a.*, k.key_fingerprint, k.algorithm, k.public_key_pem "
                    "FROM attestation a JOIN enrolled_key k ON k.id = a.key_id "
                    "WHERE a.id = ?", (ev["cadet_attestation_id"],))
    entry = one(conn, "SELECT seq, entry_hash FROM audit_log WHERE action = 'EVENT_SIGNED' "
                      "AND entity_type = 'fitness_event' AND entity_id = ? "
                      "ORDER BY seq DESC LIMIT 1", (event_id,))
    return {
        "scorecard": fitness.scorecard(conn, event_id),
        "cadet_signature": {
            "payload_b64": _b64(att["payload"]),
            "signature_b64": _b64(att["signature"]),
            "algorithm": att["algorithm"],
            "key_fingerprint": att["key_fingerprint"],
            "public_key_pem": att["public_key_pem"],
        },
        "audit_anchor": {"seq": entry["seq"], "entry_hash": entry["entry_hash"]},
    }


def export_signed(conn: sqlite3.Connection, actor_id: int, exporter: Exporter,
                  test_type: str | None = None) -> dict:
    """Export every SIGNED score not yet sent. Returns a summary."""
    identity.require_role(conn, actor_id, fitness.OIC_ROLES)
    ids = pending(conn, test_type)
    if not ids:
        return {"exported": 0, "reference": None}
    # Refuse to export anything that fails its own integrity check.
    bad = [r for r in (fitness.verify_event(conn, i) for i in ids) if not r["ok"]]
    if bad:
        raise ValueError(f"integrity check failed for events {[r['event_id'] for r in bad]}; "
                         "nothing exported")
    items = [build_item(conn, i) for i in ids]
    head = audit.head(conn)
    batch = {
        "batch_id": f"{clock.now_ms()}",
        "created_at": clock.now_iso(),
        "source": "MUSTER Vault",
        "audit_head": head,
        "items": items,
    }
    batch["batch_sha256"] = sha256_hex(canonical_bytes({k: v for k, v in batch.items()}))

    reference = exporter.send(batch)   # if this raises, nothing below runs

    with transaction(conn):
        now = clock.now_iso()
        for i in ids:
            conn.execute("UPDATE fitness_event SET status = 'EXPORTED', exported_at = ? "
                         "WHERE id = ? AND status = 'SIGNED'", (now, i))
            ev = fitness.get_event(conn, i)
            audit.append(conn, function="IOCT" if ev["test_type"] == "IOCT" else "FITNESS",
                         action="EVENT_EXPORTED", actor_id=actor_id,
                         entity_type="fitness_event", entity_id=i,
                         data={"batch_id": batch["batch_id"]})
        audit.append(conn, function="FITNESS", action="EXPORT_BATCH", actor_id=actor_id,
                     entity_type="export_batch", entity_id=None,
                     data={"batch_id": batch["batch_id"], "exporter": exporter.name,
                           "reference": str(reference), "count": len(ids),
                           "batch_sha256": batch["batch_sha256"]})
    return {"exported": len(ids), "reference": reference, "batch_id": batch["batch_id"]}
