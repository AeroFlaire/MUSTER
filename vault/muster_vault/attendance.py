"""Classroom attendance (secondary stakeholder: Dean).

Low assurance by design: the barcode scan fills in the roster, the instructor's
own eyes are the presence check. The Vault makes sure only the section's own
instructor can mark it, only enrolled cadets get marked, and every change is logged.
"""
from __future__ import annotations

import sqlite3

from . import audit, clock, identity
from .db import one, transaction
from .errors import NotFound, PermissionDenied

STATUSES = {"PRESENT", "LATE", "ABSENT", "EXCUSED"}


def add_section(conn: sqlite3.Connection, actor_id: int, course: str, section: str,
                period: str, term: str, instructor_id: int) -> int:
    with transaction(conn):
        identity.require_role(conn, actor_id, {"admin"})
        identity.require_role(conn, instructor_id, {"instructor"})
        cur = conn.execute(
            "INSERT INTO class_section(course, section, period, term, instructor_id) "
            "VALUES (?, ?, ?, ?, ?)", (course, section, period, term, instructor_id))
        audit.append(conn, function="ATTENDANCE", action="SECTION_ADDED", actor_id=actor_id,
                     entity_type="class_section", entity_id=cur.lastrowid,
                     data={"course": course, "section": section, "period": period,
                           "term": term, "instructor_id": instructor_id})
        return cur.lastrowid


def enroll(conn: sqlite3.Connection, actor_id: int, section_id: int, cadet_id: int) -> None:
    with transaction(conn):
        identity.require_role(conn, actor_id, {"admin"})
        conn.execute("INSERT OR IGNORE INTO enrollment(section_id, cadet_id) VALUES (?, ?)",
                     (section_id, cadet_id))
        audit.append(conn, function="ATTENDANCE", action="CADET_ENROLLED", actor_id=actor_id,
                     entity_type="class_section", entity_id=section_id,
                     data={"cadet_id": cadet_id})


def mark(conn: sqlite3.Connection, actor_id: int, section_id: int, cadet_edipi_hash: str,
         class_date: str, status: str = "PRESENT", client_ref: str | None = None) -> int:
    """Record (or correct) one cadet's attendance for one class meeting."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    if client_ref:
        existing = one(conn, "SELECT id FROM attendance WHERE client_ref = ?", (client_ref,))
        if existing:
            return existing["id"]
    with transaction(conn):
        identity.require_role(conn, actor_id, {"instructor"})
        sec = one(conn, "SELECT * FROM class_section WHERE id = ?", (section_id,))
        if sec is None:
            raise NotFound(f"section {section_id} not found")
        if sec["instructor_id"] != actor_id:
            raise PermissionDenied("only this section's instructor can mark attendance")
        cadet = identity.cadet_by_hash(conn, cadet_edipi_hash)
        if not one(conn, "SELECT 1 FROM enrollment WHERE section_id = ? AND cadet_id = ?",
                   (section_id, cadet["id"])):
            raise PermissionDenied("cadet is not enrolled in this section")
        prior = one(conn, "SELECT * FROM attendance WHERE section_id = ? AND cadet_id = ? "
                          "AND class_date = ?", (section_id, cadet["id"], class_date))
        now = clock.now_iso()
        if prior is None:
            cur = conn.execute(
                "INSERT INTO attendance(section_id, cadet_id, class_date, status, "
                "recorded_by, recorded_at, client_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (section_id, cadet["id"], class_date, status, actor_id, now, client_ref))
            att_id, action, data = cur.lastrowid, "ATTENDANCE_MARKED", {}
        else:
            if prior["status"] == status:
                return prior["id"]
            conn.execute("UPDATE attendance SET status = ?, recorded_by = ?, recorded_at = ? "
                         "WHERE id = ?", (status, actor_id, now, prior["id"]))
            att_id, action = prior["id"], "ATTENDANCE_CORRECTED"
            data = {"old_status": prior["status"]}
        data.update({"section_id": section_id, "cadet_id": cadet["id"],
                     "class_date": class_date, "status": status})
        audit.append(conn, function="ATTENDANCE", action=action, actor_id=actor_id,
                     entity_type="attendance", entity_id=att_id, data=data)
        return att_id


def roster(conn: sqlite3.Connection, section_id: int, class_date: str) -> list[dict]:
    """Everyone enrolled, with today's status (None = not yet marked)."""
    return [dict(r) for r in conn.execute(
        "SELECT c.id AS cadet_id, c.last_name, c.first_name, a.status "
        "FROM enrollment e JOIN cadet c ON c.id = e.cadet_id "
        "LEFT JOIN attendance a ON a.cadet_id = c.id AND a.section_id = e.section_id "
        "  AND a.class_date = ? "
        "WHERE e.section_id = ? ORDER BY c.last_name, c.first_name",
        (class_date, section_id))]
