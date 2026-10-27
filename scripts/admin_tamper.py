"""DEMO ONLY: play a rogue admin who edits the Vault database directly.

The admin has the database file AND the key (MUSTER_DB_KEY), so encryption doesn't
stop them. They go around MUSTER and change a score with SQL. This script shows
that the edit is possible, and then
    python -m muster_vault integrity --db <db>
shows that it is caught. Synthetic data only.

Run from the repo root, in a window with the same MUSTER_DB_KEY as the server:

    python scripts/admin_tamper.py show         --event 1
    python scripts/admin_tamper.py edit-score   --event 1 --component MDL --points 60
    python scripts/admin_tamper.py cover-tracks --event 1 --component MDL

--db defaults to the Gatekeeper demo database (gatekeeper/api/muster_demo.db).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
try:
    import muster_vault  # noqa: F401
except ImportError:
    sys.path.insert(0, str(REPO / "vault"))

from muster_vault import db  # noqa: E402
from muster_vault.canonical import canonical_text  # noqa: E402
from muster_vault.errors import VaultError  # noqa: E402

DEFAULT_DB = REPO / "gatekeeper" / "api" / "muster_demo.db"
BANNER = "*** SIMULATED INSIDER ATTACK (demo, synthetic data) ***"


def _event(conn, event_id):
    ev = conn.execute(
        "SELECT e.id, e.status, e.test_type, c.last_name, c.first_name FROM fitness_event e "
        "JOIN cadet c ON c.id = e.cadet_id WHERE e.id = ?", (event_id,)).fetchone()
    if ev is None:
        raise SystemExit(f"no score with event id {event_id}")
    return ev


def show(conn, args):
    ev = _event(conn, args.event)
    print(f"Score {ev['id']}: {ev['last_name']}, {ev['first_name']}  "
          f"{ev['test_type']}  status {ev['status']}")
    for r in conn.execute("SELECT component, raw_value, points FROM fitness_component "
                          "WHERE event_id = ? ORDER BY component", (args.event,)):
        print(f"  {r['component']:<4} {r['raw_value']:>8}  {r['points']} pts")


def edit_score(conn, args):
    print(BANNER)
    ev = _event(conn, args.event)
    row = conn.execute("SELECT raw_value, points FROM fitness_component "
                       "WHERE event_id = ? AND component = ?",
                       (args.event, args.component)).fetchone()
    if row is None:
        raise SystemExit(f"score {args.event} has no {args.component} result")
    raw = args.raw if args.raw is not None else row["raw_value"]
    conn.execute("UPDATE fitness_component SET raw_value = ?, points = ? "
                 "WHERE event_id = ? AND component = ?",
                 (raw, args.points, args.event, args.component))
    print(f"Admin changed {ev['last_name']}'s {args.component} straight in the database: "
          f"{row['raw_value']} / {row['points']} pts  ->  {raw} / {args.points} pts")
    print("No MUSTER screen was used and no audit entry was written.")
    print("Now run:  python -m muster_vault integrity --db <db>")


def cover_tracks(conn, args):
    print(BANNER)
    row = conn.execute("SELECT raw_value, points FROM fitness_component "
                       "WHERE event_id = ? AND component = ?",
                       (args.event, args.component)).fetchone()
    entry = None
    for e in conn.execute("SELECT seq, action, data FROM audit_log WHERE entity_type = "
                          "'fitness_event' AND entity_id = ? ORDER BY seq", (args.event,)):
        data = json.loads(e["data"])
        if (e["action"] in ("COMPONENT_ENTERED", "COMPONENT_CHANGED")
                and data.get("component") == args.component):
            entry = (e["seq"], data)
    if row is None or entry is None:
        raise SystemExit(f"nothing to cover: no {args.component} result for score {args.event}")
    seq, data = entry
    data.update({"raw_value": row["raw_value"], "points": row["points"]})
    # The app's lock: a trigger refuses any UPDATE on audit_log. Someone with the file
    # and the key can simply remove it.
    conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    conn.execute("UPDATE audit_log SET data = ? WHERE seq = ?", (canonical_text(data), seq))
    db.init_schema(conn)  # put the lock back so it looks untouched
    print(f"Admin removed the append-only lock, rewrote audit entry #{seq} to say "
          f"{args.component} = {row['raw_value']} / {row['points']} pts, and put the lock back.")
    print("The database and the log now agree. Now run:  "
          "python -m muster_vault integrity --db <db>")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("show", "edit-score", "cover-tracks"):
        sp = sub.add_parser(name)
        sp.add_argument("--db", default=str(DEFAULT_DB))
        sp.add_argument("--event", type=int, required=True)
        if name != "show":
            sp.add_argument("--component", required=True,
                            choices=["MDL", "HRP", "SDC", "PLK", "2MR"])
        if name == "edit-score":
            sp.add_argument("--points", type=int, required=True)
            sp.add_argument("--raw", help="new raw value (default: leave it)")
    args = p.parse_args(argv)
    try:
        conn = db.connect(args.db)
    except VaultError as exc:
        raise SystemExit(f"error: {exc}")
    {"show": show, "edit-score": edit_score, "cover-tracks": cover_tracks}[args.cmd](conn, args)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
