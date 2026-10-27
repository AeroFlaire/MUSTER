"""Command line for the Vault.

Database files are encrypted; set MUSTER_DB_KEY first (see `new-key`).

    python -m muster_vault new-key                       # print a fresh 256-bit key
    python -m muster_vault init     --db muster.db
    python -m muster_vault verify   --db muster.db
    python -m muster_vault head     --db muster.db
    python -m muster_vault history  --db muster.db --entity fitness_event --id 3
    python -m muster_vault check    --db muster.db --event 3
    python -m muster_vault integrity --db muster.db      # chain + every score vs. its audit trail
    python -m muster_vault retention --db muster.db      # settings + records past retention
    python -m muster_vault set-retention --db muster.db --actor 1 --function FITNESS \
                                         --days 1825 --source "AR 25-400-2, ..."
    python -m muster_vault encrypt-db --plain old.db --db new.db   # one-time conversion
"""
from __future__ import annotations

import argparse
import json
import sys

from . import audit, db, fitness, retention
from .errors import VaultError


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="muster_vault", description="MUSTER Vault tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-key", help="print a fresh random database key (64 hex characters)")
    for name, help_ in [("init", "create the database and schema"),
                        ("verify", "walk the audit hash chain (PASS / first broken seq)"),
                        ("head", "print the latest audit entry (an outside anchor)"),
                        ("integrity", "audit chain + every score checked against its audit trail"),
                        ("retention", "show retention settings and records past retention")]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--db", required=True)
    sp = sub.add_parser("history", help="audit history of one record")
    sp.add_argument("--db", required=True)
    sp.add_argument("--entity", required=True)
    sp.add_argument("--id", type=int, required=True)
    sp = sub.add_parser("check", help="does a score still match what the cadet signed?")
    sp.add_argument("--db", required=True)
    sp.add_argument("--event", type=int, required=True)
    sp = sub.add_parser("set-retention", help="admin: set one function's retention period")
    sp.add_argument("--db", required=True)
    sp.add_argument("--actor", type=int, required=True, help="app_user id of the admin")
    sp.add_argument("--function", required=True, choices=retention.FUNCTIONS)
    sp.add_argument("--days", type=int, required=True)
    sp.add_argument("--source", required=True, help="where the number comes from")
    sp = sub.add_parser("encrypt-db", help="copy an old plaintext database into an encrypted one")
    sp.add_argument("--plain", required=True)
    sp.add_argument("--db", required=True, help="new encrypted file (must not exist)")
    args = p.parse_args(argv)

    try:
        return _run(args)
    except VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run(args) -> int:
    if args.cmd == "new-key":
        print(db.new_key())
        print("Keep this secret: set it as MUSTER_DB_KEY. Never commit it. "
              "Lose it and the database cannot be opened.", file=sys.stderr)
        return 0
    if args.cmd == "encrypt-db":
        db.encrypt_plaintext_db(args.plain, args.db)
        conn = db.connect(args.db)
        print(f"Encrypted copy written to {args.db}; audit chain: {audit.verify(conn)}")
        print(f"Check it, then delete the plaintext file {args.plain}.")
        return 0

    conn = db.connect(args.db)
    if args.cmd == "init":
        print(f"Vault ready at {args.db} (encrypted)")
        return 0
    if args.cmd == "verify":
        result = audit.verify(conn)
        print(result)
        return 0 if result.ok else 1
    if args.cmd == "head":
        print(json.dumps(audit.head(conn), indent=2))
        return 0
    if args.cmd == "history":
        for e in audit.entries_for(conn, args.entity, args.id):
            print(f"#{e['seq']:<5} {e['ts']}  {e['action']:<22} actor={e['actor_id']}  "
                  f"{e['data']}")
        return 0
    if args.cmd == "check":
        result = fitness.verify_event(conn, args.event)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    if args.cmd == "integrity":
        rep = fitness.integrity_report(conn)
        print(f"Audit chain : {rep['chain']}")
        for e in rep["events"]:
            mark = "OK      " if e["ok"] else "TAMPERED"
            print(f"Score {e['event_id']:<5} {mark} ({e['status']}"
                  f"{', cadet-signed' if e['signed'] else ''})")
            for p in e["problems"]:
                print(f"      - {p}")
        print("RESULT: " + ("PASS - every record matches its audit trail" if rep["ok"]
                            else "FAIL - tampering detected"))
        return 0 if rep["ok"] else 1
    if args.cmd == "retention":
        for r in retention.report(conn):
            flag = "  <-- PLACEHOLDER, not policy" if r["placeholder"] else ""
            print(f"{r['function']:<11} {r['days']:>6} days   past retention: "
                  f"{r['past_retention']:<4} source: {r['source']}{flag}")
        return 0
    if args.cmd == "set-retention":
        r = retention.set_days(conn, args.actor, args.function, args.days, args.source)
        print(f"{r['function']} retention is now {r['days']} days ({r['source']})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
