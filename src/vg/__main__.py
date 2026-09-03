"""CLI:  python -m vg <command>   (or `vg <command>` after `pip install -e .`)"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import collect
from .db import run_sql_file

_SCHEMA = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _m(s: str) -> date:
    return datetime.strptime(s, "%Y-%m").date()  # day defaults to 1


def _print(o: collect.CollectOutcome) -> None:
    line = f"  {o.session_date}  {o.status:<13} rows={o.rows}"
    if o.detail:
        line += f"  ({o.detail})"
    print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vg", description="Vektor Gauss — Phase 1 collectors")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="apply db/schema.sql (idempotent)")

    cp = sub.add_parser("collect-price", help="IDX Stock Summary -> price_bars")
    g = cp.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", metavar="YYYY-MM-DD", help="one trading day")
    g.add_argument("--latest", action="store_true", help="most recent published day")
    g.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    co = sub.add_parser("collect-ownership", help="KSEI month-end balance -> ownership_monthly")
    go = co.add_mutually_exclusive_group(required=True)
    go.add_argument("--month", metavar="YYYY-MM", help="one month-end snapshot")
    go.add_argument("--latest", action="store_true", help="this month + last month")
    go.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive YYYY-MM range")

    args = p.parse_args(argv)

    if args.cmd == "init-db":
        run_sql_file(_SCHEMA)
        print(f"schema applied from {_SCHEMA}")
        return 0

    if args.cmd == "collect-price":
        if args.latest:
            _print(collect.collect_price_latest())
        elif args.date:
            _print(collect.collect_price_day(_d(args.date)))
        else:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            print(f"backfill {start} .. {end}")
            outs = collect.collect_price_range(start, end)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} days with data")
        return 0

    if args.cmd == "collect-ownership":
        if args.latest:
            for o in collect.collect_ownership_latest():
                _print(o)
        elif args.month:
            _print(collect.collect_ownership_month(_m(args.month)))
        else:
            start, end = _m(args.backfill[0]), _m(args.backfill[1])
            print(f"backfill {start:%Y-%m} .. {end:%Y-%m}")
            outs = collect.collect_ownership_range(start, end)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} months with data")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
