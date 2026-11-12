"""`ticket` CLI: create / move / assign / list / get / triage / note / sync."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ticket_sync.store import TicketError, TicketStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ticket", description="Kanban Markdown tickets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a ticket in backlog")
    c.add_argument("title"); c.add_argument("-o", "--objective", required=True)
    c.add_argument("-a", "--ac", action="append", default=[], help="acceptance criterion (repeatable)")
    c.add_argument("-c", "--context", action="append", default=[]); c.add_argument("--attach", action="append", default=[])
    c.add_argument("-p", "--priority", default="medium", choices=["high", "medium", "low"])
    c.add_argument("-l", "--label", action="append", default=[]); c.add_argument("--status", default="backlog")
    m = sub.add_parser("move"); m.add_argument("ticket_id"); m.add_argument("to_column")
    a = sub.add_parser("assign"); a.add_argument("ticket_id"); a.add_argument("bot_id")
    l = sub.add_parser("list"); l.add_argument("--status"); l.add_argument("--bot")
    g = sub.add_parser("get"); g.add_argument("ticket_id"); g.add_argument("--json", action="store_true")
    t = sub.add_parser("triage"); t.add_argument("ticket_id"); t.add_argument("--apply", action="store_true")
    n = sub.add_parser("note"); n.add_argument("ticket_id"); n.add_argument("note"); n.add_argument("--turn", type=int)
    s = sub.add_parser("sync"); s.add_argument("ticket_id")
    args = ap.parse_args(argv)
    st = TicketStore()
    try:
        if args.cmd == "create":
            out = st.create(args.title, args.objective, context=args.context, acceptance_criteria=args.ac,
                            attachments=args.attach, priority=args.priority, labels=args.label, status=args.status)
        elif args.cmd == "move":
            out = st.move(args.ticket_id, args.to_column)
        elif args.cmd == "assign":
            out = st.assign(args.ticket_id, args.bot_id)
        elif args.cmd == "list":
            rows = st.list(status=args.status, assignee_bot=args.bot)
            for r in rows:
                print(f"{r['status']:<12} {r['id']:<50} {str(r.get('assignee_bot') or '-'):<9} {r.get('priority') or '':<7} {r.get('title')}")
            return 0
        elif args.cmd == "get":
            t_ = st.get(args.ticket_id)
            print(json.dumps(t_, indent=2) if args.json else t_["markdown"])
            return 0
        elif args.cmd == "triage":
            from scripts.triage import triage
            out = triage(st, args.ticket_id, apply=args.apply)
        elif args.cmd == "note":
            out = st.add_review_note(args.ticket_id, args.note, turn=args.turn)
        elif args.cmd == "sync":
            from scripts.ticket_sync.github_sync import sync_ticket
            out = sync_ticket(args.ticket_id, event="manual", store=st)
    except TicketError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
