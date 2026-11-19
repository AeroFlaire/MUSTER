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


# ---------------------------------------------------------------------------
# AMS export v0: CSV files of the saved records (PBI-10)
# ---------------------------------------------------------------------------
# The AMS / Azimuth field list and format are still an open RFI with DPE. Until it is
# answered, the card says: export a simple CSV of the fields already in the API contract.
# This is that file. When the RFI is answered, only the column lists below change.
#
# Rules this follows:
#   - Nothing leaves if the Vault fails its own integrity check (audit chain, every score
#     against its audit trail, every attendance row against its audit trail). A refused
#     export is written to the audit log with the reason.
#   - No raw DoD ID (the Vault never has one) and no ID hash. A cadet is named by the
#     Vault's own cadet number, name, company and class year. AMS will need the DoD ID to
#     match records; how it gets it is part of the open RFI.
#   - Row counts are checked against the database after the files are written.
#   - The export is itself written to the audit log (counts and a SHA-256 of each file),
#     so a file that is changed after it leaves can be caught.
#   - It is a report: it does not change any record's status. EXPORTED stays the status
#     for a cadet-signed score pushed to AMS (export_signed above).

import csv  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import shutil  # noqa: E402

from .errors import VaultError  # noqa: E402

CSV_FORMAT = "MUSTER CSV v0 (API contract fields; AMS format is an open RFI)"
COUNTED_STATUSES = ("SUBMITTED", "SIGNED", "EXPORTED")   # scores that count
AFT_EVENTS = ("MDL", "HRP", "SDC", "PLK", "2MR")
MIN_EVENT_POINTS = 60   # pass rule, contract v1 section 4.7 (confirmed 17-Nov)

_CADET_COLS = ["cadet_id", "last_name", "first_name", "company", "class_year"]
AFT_COLUMNS = (["muster_event_id", "status", "cadet_signed", "corrects_event_id",
                "session_date", "location"] + _CADET_COLS + ["entered_by"]
               + [f"{c}_{k}" for c in AFT_EVENTS for k in ("raw", "points")]
               + ["total_points", "result", "audit_seq", "audit_entry_hash"])
IOCT_COLUMNS = (["muster_event_id", "status", "cadet_signed", "corrects_event_id",
                 "session_date", "location"] + _CADET_COLS + ["entered_by"]
                + ["elapsed", "elapsed_ms", "audit_seq", "audit_entry_hash"])
ATTENDANCE_COLUMNS = (["attendance_id", "class_date", "course", "section", "period", "term"]
                      + _CADET_COLS + ["status", "recorded_by", "recorded_at",
                                       "audit_seq", "audit_entry_hash"])


class ExportRefused(VaultError):
    """The integrity check failed, so nothing was exported."""


def _safe(v):
    """Stop a spreadsheet from running a cell as a formula (CSV injection)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


def _anchor(conn, entity_type, entity_id):
    e = one(conn, "SELECT seq, entry_hash FROM audit_log WHERE entity_type = ? AND "
                  "entity_id = ? ORDER BY seq DESC LIMIT 1", (entity_type, entity_id))
    return (e["seq"], e["entry_hash"]) if e else ("", "")


def _cadet(conn, cadet_id):
    r = one(conn, "SELECT c.id, c.last_name, c.first_name, c.class_year, co.name AS company "
                  "FROM cadet c LEFT JOIN company co ON co.id = c.company_id WHERE c.id = ?",
            (cadet_id,))
    return {"cadet_id": r["id"], "last_name": r["last_name"], "first_name": r["first_name"],
            "company": r["company"], "class_year": r["class_year"]}


def _user_name(conn, user_id):
    r = one(conn, "SELECT display_name FROM app_user WHERE id = ?", (user_id,))
    return r["display_name"] if r else ""


def _aft_result(points: dict) -> str:
    if any(points.get(c) is None for c in AFT_EVENTS):
        return "INCOMPLETE"
    return "PASS" if all(points[c] >= MIN_EVENT_POINTS for c in AFT_EVENTS) else "FAIL"


def _score_rows(conn, test_type):
    rows = []
    for ev in conn.execute(
            f"SELECT * FROM fitness_event WHERE test_type = ? AND status IN "
            f"({','.join('?' * len(COUNTED_STATUSES))}) ORDER BY id",
            (test_type, *COUNTED_STATUSES)).fetchall():
        card = fitness.scorecard(conn, ev["id"])
        seq, h = _anchor(conn, "fitness_event", ev["id"])
        row = {"muster_event_id": ev["id"], "status": ev["status"],
               "cadet_signed": "yes" if ev["cadet_attestation_id"] else "no",
               "corrects_event_id": ev["supersedes_event_id"],
               "session_date": card["session_date"], "location": card["location"],
               **_cadet(conn, ev["cadet_id"]), "entered_by": _user_name(conn, ev["grader_id"]),
               "audit_seq": seq, "audit_entry_hash": h}
        if test_type == "AFT":
            pts = {}
            for c in card["components"]:
                row[f"{c['component']}_raw"] = c["raw_value"]
                row[f"{c['component']}_points"] = c["points"]
                pts[c["component"]] = c["points"]
            row["total_points"] = card["total_points"]
            row["result"] = _aft_result(pts)
        else:
            run = card["ioct"] or {}
            row["elapsed"], row["elapsed_ms"] = run.get("elapsed"), run.get("elapsed_ms")
        rows.append(row)
    return rows


def _attendance_rows(conn):
    rows = []
    for a in conn.execute(
            "SELECT a.*, s.course, s.section, s.period, s.term FROM attendance a "
            "JOIN class_section s ON s.id = a.section_id ORDER BY a.class_date, a.id").fetchall():
        seq, h = _anchor(conn, "attendance", a["id"])
        rows.append({"attendance_id": a["id"], "class_date": a["class_date"],
                     "course": a["course"], "section": a["section"], "period": a["period"],
                     "term": a["term"], **_cadet(conn, a["cadet_id"]), "status": a["status"],
                     "recorded_by": _user_name(conn, a["recorded_by"]),
                     "recorded_at": a["recorded_at"], "audit_seq": seq, "audit_entry_hash": h})
    return rows


def _csv_bytes(columns, rows) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: _safe(r.get(c)) for c in columns})
    return buf.getvalue().encode("utf-8")


def _refuse(conn, actor_id, reasons):
    with transaction(conn):
        audit.append(conn, function="CORE", action="EXPORT_REFUSED", actor_id=actor_id,
                     entity_type="export_batch", entity_id=None,
                     data={"format": "csv-v0", "reasons": reasons[:20]})
    raise ExportRefused("Export stopped: the integrity check failed, so nothing was "
                        "exported. " + " ".join(reasons[:5]))


def export_csv(conn: sqlite3.Connection, actor_id: int, out_dir: str) -> dict:
    """OIC: write the saved AFT, IOCT and attendance records to CSV files for AMS.

    Makes <out_dir>/muster-export-<batch_id>/ with aft.csv, ioct.csv, attendance.csv
    and manifest.json. Returns the manifest (plus the folder path)."""
    identity.require_role(conn, actor_id, fitness.OIC_ROLES)

    rep = fitness.integrity_report(conn)
    if not rep["ok"]:
        reasons = []
        if not rep["chain"].ok:
            reasons.append(f"Audit chain broken at entry {rep['chain'].first_bad_seq} "
                           f"({rep['chain'].reason}).")
        reasons += [f"Score {e['event_id']}: {p}." for e in rep["events"] if not e["ok"]
                    for p in e["problems"]]
        reasons += [f"Attendance {a['attendance_id']}: {p}." for a in rep["attendance"]
                    for p in a["problems"]]
        _refuse(conn, actor_id, reasons)

    head = audit.head(conn)
    files = {"aft.csv": (AFT_COLUMNS, _score_rows(conn, "AFT")),
             "ioct.csv": (IOCT_COLUMNS, _score_rows(conn, "IOCT")),
             "attendance.csv": (ATTENDANCE_COLUMNS, _attendance_rows(conn))}

    # What the database holds, counted separately from the rows written, so the two can be
    # compared (PBI-10: export row count equals the saved record count).
    def count(sql, *p):
        return conn.execute(sql, p).fetchone()[0]
    placeholders = ",".join("?" * len(COUNTED_STATUSES))
    in_db = {"aft.csv": count(f"SELECT COUNT(*) FROM fitness_event WHERE test_type = 'AFT' "
                              f"AND status IN ({placeholders})", *COUNTED_STATUSES),
             "ioct.csv": count(f"SELECT COUNT(*) FROM fitness_event WHERE test_type = 'IOCT' "
                               f"AND status IN ({placeholders})", *COUNTED_STATUSES),
             "attendance.csv": count("SELECT COUNT(*) FROM attendance")}
    left_out = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, COUNT(*) AS n FROM fitness_event WHERE status NOT IN ({placeholders}) "
        f"GROUP BY status", COUNTED_STATUSES)}

    batch_id = str(clock.now_ms())
    folder = os.path.join(out_dir, f"muster-export-{batch_id}")
    os.makedirs(folder, exist_ok=False)
    try:
        written = {}
        for name, (cols, rows) in files.items():
            data = _csv_bytes(cols, rows)
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)
            with open(os.path.join(folder, name), newline="", encoding="utf-8") as f:
                n = sum(1 for _ in csv.DictReader(f))
            if n != in_db[name]:
                raise VaultError(f"{name}: wrote {n} rows but the database holds "
                                 f"{in_db[name]}; export stopped")
            written[name] = {"rows": n, "sha256": hashlib.sha256(data).hexdigest()}

        manifest = {
            "batch_id": batch_id,
            "format": CSV_FORMAT,
            "created_at": clock.now_iso(),
            "created_by": _user_name(conn, actor_id),
            "integrity_check": f"PASS ({rep['chain'].entries_checked} audit entries, every "
                               f"score and attendance row matches its audit trail)",
            "audit_head_at_export": head,
            "files": written,
            "scores_left_out": left_out,
            "what_is_in_it": {
                "aft.csv / ioct.csv": "scores that count: " + ", ".join(COUNTED_STATUSES)
                                      + ". A correction has corrects_event_id set; the score "
                                        "it replaced is SUPERSEDED and left out.",
                "attendance.csv": "every attendance mark",
            },
            "limits": [
                "No DoD ID and no ID hash: AMS matching by DoD ID is part of the open RFI.",
                "AFT age group and scoring column are not stored in the Vault yet.",
                "IOCT grade is worked out by the Gatekeeper from the DPE scale and is not "
                "in this file; the time is.",
                "A typed (manual) IOCT time is not yet labeled as manual.",
                "cadet_signed is 'no' until CAC signing at the score compiler is live.",
            ],
        }
        mbytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        with open(os.path.join(folder, "manifest.json"), "wb") as f:
            f.write(mbytes)

        with transaction(conn):
            audit.append(conn, function="CORE", action="EXPORT_CSV", actor_id=actor_id,
                         entity_type="export_batch", entity_id=None,
                         data={"batch_id": batch_id, "format": "csv-v0",
                               "files": written,
                               "manifest_sha256": hashlib.sha256(mbytes).hexdigest(),
                               "audit_head_seq": head.get("seq")})
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)   # never leave a half-written export
        raise
    return {**manifest, "folder": folder}
