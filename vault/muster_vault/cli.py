"""Command line for the Vault.

    python -m muster_vault init     --db muster.db
    python -m muster_vault verify   --db muster.db
    python -m muster_vault head     --db muster.db
    python -m muster_vault history  --db muster.db --entity fitness_event --id 3
    python -m muster_vault check    --db muster.db --event 3
"""
from __future__ import annotations

import argparse
import json
import sys

from . import audit, db, fitness


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="muster_vault", description="MUSTER Vault tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in [("init", "create the database and schema"),
                        ("verify", "walk the audit hash chain (PASS / first broken seq)"),
                        ("head", "print the latest audit entry (an outside anchor)")]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--db", required=True)
    sp = sub.add_parser("history", help="audit history of one record")
    sp.add_argument("--db", required=True)
    sp.add_argument("--entity", required=True)
    sp.add_argument("--id", type=int, required=True)
    sp = sub.add_parser("check", help="does a score still match what the cadet signed?")
    sp.add_argument("--db", required=True)
    sp.add_argument("--event", type=int, required=True)
    args = p.parse_args(argv)

    conn = db.connect(args.db)
    if args.cmd == "init":
        print(f"Vault ready at {args.db}")
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
    return 2


if __name__ == "__main__":
    sys.exit(main())
