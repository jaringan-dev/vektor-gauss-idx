"""CLI:  python -m vg <command>   (or `vg <command>` after `pip install -e .`)"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import calc, collect, digest, engine, paper
from .config import CONFIG
from .db import connect, run_sql_file
from .telegram import TelegramSender

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

    ci = sub.add_parser("collect-index", help="IDX Index Summary -> index_bars (COMPOSITE = IHSG)")
    gi = ci.add_mutually_exclusive_group(required=True)
    gi.add_argument("--date", metavar="YYYY-MM-DD", help="one trading day")
    gi.add_argument("--latest", action="store_true", help="most recent published day")
    gi.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    cd_ = sub.add_parser("collect-disclosure",
                         help="IDX ownership-disclosure filings (>5% + insider) -> disclosure_filings")
    gd = cd_.add_mutually_exclusive_group(required=True)
    gd.add_argument("--date", metavar="YYYY-MM-DD", help="one filing day")
    gd.add_argument("--latest", action="store_true", help="the latest confirmed trading day")
    gd.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    cb = sub.add_parser(
        "collect-broker-summary",
        help="broker summary (arjum_idxedge, Tier 3, forward-only) -> broker_flows",
    )
    gb = cb.add_mutually_exclusive_group(required=True)
    gb.add_argument("--latest", action="store_true",
                    help="watchlist + holdings, stamped to the latest trading day")
    gb.add_argument("--ticker", metavar="CODE", help="one ticker (e.g. BBCA)")

    ch = sub.add_parser("collect-halts",
                        help="IDX trading-halt announcements -> trading_halts")
    gh_ = ch.add_mutually_exclusive_group(required=True)
    gh_.add_argument("--date", metavar="YYYY-MM-DD", help="one announcement day")
    gh_.add_argument("--latest", action="store_true", help="the latest confirmed trading day")
    gh_.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    pd_ = sub.add_parser(
        "parse-disclosure",
        help="Phase B: parse archived disclosure PDFs -> disclosure_holdings (direction)",
    )
    pd_.add_argument("--backfill", nargs=2, metavar=("START", "END"),
                      help="inclusive filed_date range; omit to catch up on everything unparsed")

    cc = sub.add_parser("collect-corpactions", help="KSEI CA schedule -> corp_actions")
    gc = cc.add_mutually_exclusive_group(required=True)
    gc.add_argument("--year", type=int, metavar="YYYY", help="one calendar year")
    gc.add_argument("--backfill", nargs=2, type=int, metavar=("START", "END"), help="inclusive years")

    sub.add_parser(
        "detect-ca",
        help="derive share_count_change events from price_bars.listed_shares jumps",
    )

    cm = sub.add_parser("compute", help="engine v0: components + probation tags -> signal_*")
    gm = cm.add_mutually_exclusive_group(required=True)
    gm.add_argument("--date", metavar="YYYY-MM-DD", help="one trading day")
    gm.add_argument("--latest", action="store_true", help="the latest confirmed trading day")
    gm.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    sub.add_parser("backfill-outcomes", help="fill forward returns on resolved signal_log rows")
    sub.add_parser(
        "paper-update",
        help="open new paper trades for FLOAT-ASING-PADAT/TIKET-BESAR-AKUM, resolve open ones",
    )
    pr = sub.add_parser("paper-report", help="paper-trading win/loss/timeout stats per tag")
    pr.add_argument("--since", metavar="YYYY-MM-DD", help="only trades signalled on/after this date")

    ev = sub.add_parser("eval", help="per-tag forward-return stats (the Phase 1 payoff)")
    ev.add_argument("--since", metavar="YYYY-MM-DD",
                    help="only signals on/after this date (forward-test window); "
                         "without it, prints in-sample + a forward block if VG_FORWARD_SINCE is set")

    dg = sub.add_parser("digest", help="build the Telegram EOD digest (or --weekly eval)")
    dg.add_argument("--date", metavar="YYYY-MM-DD", help="digest for a specific trading day")
    dg.add_argument("--weekly", action="store_true", help="the weekly eval message instead")
    dg.add_argument("--dry-run", action="store_true", help="print, don't send")

    cl = sub.add_parser("calc", help="position-sizing / trade-plan calculator")
    cl.add_argument("--entry", type=float, action="append", required=True,
                    metavar="PRICE", help="entry price (repeat for averaging)")
    cl.add_argument("--sl", type=float, required=True, help="stop-loss price")
    cl.add_argument("--target", type=float, help="target price")
    cl.add_argument("--target-r", type=float, help="target as R-multiple (default from env)")
    cl.add_argument("--balance", type=float, help="account balance IDR (default from env)")
    cl.add_argument("--risk", type=float, metavar="PCT", help="risk %% per trade (default from env)")
    cl.add_argument("--fee", type=float, metavar="PCT", help="round-trip fee %% (default from env)")

    se = sub.add_parser("season", help="day-of-week + monthly seasonality from price history")
    se.add_argument("ticker", help="ticker, or COMPOSITE / IHSG")
    se.add_argument("--years", type=int, default=12, help="lookback (our data starts ~2023)")

    ow = sub.add_parser("ownership", help="KSEI monthly ownership history for one ticker")
    ow.add_argument("ticker")
    ow.add_argument("--months", type=int, default=12)

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

    if args.cmd == "collect-index":
        if args.latest:
            _print(collect.collect_index_latest())
        elif args.date:
            _print(collect.collect_index_day(_d(args.date)))
        else:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            print(f"backfill {start} .. {end}")
            outs = collect.collect_index_range(start, end)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} days with data")
        return 0

    if args.cmd == "collect-disclosure":
        if args.latest:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(session_date) FROM trading_calendar")
                latest = cur.fetchone()[0]
            if latest is None:
                print("no trading_calendar rows — run collect-price first")
                return 1
            _print(collect.collect_disclosure_day(latest))
        elif args.date:
            _print(collect.collect_disclosure_day(_d(args.date)))
        else:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            print(f"backfill {start} .. {end}")
            outs = collect.collect_disclosure_range(start, end)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} days with filings")
        return 0

    if args.cmd == "collect-broker-summary":
        if args.ticker:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(session_date) FROM trading_calendar")
                latest = cur.fetchone()[0]
            if latest is None:
                print("no trading_calendar rows — run collect-price first")
                return 1
            _print(collect.collect_broker_summary_ticker(latest, args.ticker))
        else:
            outs = collect.collect_broker_summary_latest()
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} tickers with data")
        return 0

    if args.cmd == "collect-halts":
        if args.latest:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(session_date) FROM trading_calendar")
                latest = cur.fetchone()[0]
            if latest is None:
                print("no trading_calendar rows — run collect-price first")
                return 1
            _print(collect.collect_trading_halts_day(latest))
        elif args.date:
            _print(collect.collect_trading_halts_day(_d(args.date)))
        else:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            print(f"backfill {start} .. {end}")
            outs = collect.collect_trading_halts_range(start, end)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} days with halt announcements")
        return 0

    if args.cmd == "parse-disclosure":
        if args.backfill:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            _print(collect.parse_disclosure_pdfs(start, end))
        else:
            _print(collect.parse_disclosure_pdfs())
        return 0

    if args.cmd == "collect-corpactions":
        if args.year:
            _print(collect.collect_corpactions_year(args.year))
        else:
            outs = collect.collect_corpactions_range(args.backfill[0], args.backfill[1])
            for o in outs:
                _print(o)
        return 0

    if args.cmd == "detect-ca":
        n = collect.detect_share_count_changes()
        print(f"share_count_change events written/updated: {n}")
        return 0

    if args.cmd == "compute":
        def _cs(s: engine.ComputeSummary) -> None:
            line = f"  {s.session_date}  {s.status:<8} tickers={s.tickers} tags={s.signal_rows}"
            print(line + (f"  ({s.detail})" if s.detail else ""))

        if args.latest:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(session_date) FROM trading_calendar")
                latest = cur.fetchone()[0]
            if latest is None:
                print("no trading_calendar rows — run collect-price first")
                return 1
            _cs(engine.compute_day(latest))
        elif args.date:
            _cs(engine.compute_day(_d(args.date)))
        else:
            start, end = _d(args.backfill[0]), _d(args.backfill[1])
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT session_date FROM trading_calendar "
                    "WHERE session_date BETWEEN %s AND %s ORDER BY session_date",
                    (start, end),
                )
                days = [r[0] for r in cur.fetchall()]
            for d in days:
                _cs(engine.compute_day(d))
            print(f"computed {len(days)} trading days")
        return 0

    if args.cmd == "backfill-outcomes":
        print(f"signal_log rows updated: {engine.backfill_outcomes()}")
        return 0

    if args.cmd == "digest":
        text = (
            digest.build_weekly_eval() if args.weekly
            else digest.build_daily(_d(args.date) if args.date else None)
        )
        if args.dry_run:
            print(text)
            return 0
        sender = TelegramSender()
        if not sender.configured:
            print("VG_TELEGRAM_BOT_TOKEN / VG_TELEGRAM_CHAT_ID not set. Preview:\n")
            print(text)
            return 1
        n = sender.send(text)
        print(f"sent ({n} message{'s' if n > 1 else ''})")
        return 0

    if args.cmd == "calc":
        tr = None if args.target else (args.target_r or CONFIG.target_r)
        pl = calc.plan(
            args.entry, args.sl,
            target=args.target, target_r=tr,
            balance=args.balance if args.balance is not None else CONFIG.account_balance,
            risk_pct=(args.risk / 100.0) if args.risk is not None else CONFIG.risk_pct,
            fee_pct=(args.fee / 100.0) if args.fee is not None else CONFIG.fee_pct,
        )
        print(f"  avg entry   : {pl.avg_entry:,.2f}")
        print(f"  stop-loss   : {pl.stop_loss:,.2f}  ({pl.stop_loss / pl.avg_entry - 1:+.2%})")
        if pl.target:
            print(f"  target      : {pl.target:,.2f}  ({pl.target / pl.avg_entry - 1:+.2%})")
        if pl.rr:
            print(f"  R : R       : {pl.rr:.2f}")
        print(f"  risk/share  : {pl.risk_per_share:,.2f}  (incl. fee)")
        if pl.lots is not None:
            print(f"  size        : {pl.lots:,} lots ({pl.shares:,} shares)")
            print(f"  position    : Rp {pl.position_idr:,.0f}")
            print(f"  risk        : Rp {pl.risk_idr:,.0f} target, "
                  f"Rp {pl.shares * pl.risk_per_share:,.0f} actual "
                  f"({pl.risk_pct_of_balance:+.2%} of balance)")
        return 0

    if args.cmd == "paper-update":
        s = paper.update_paper_trades()
        print(
            f"opened={s.opened}  win={s.resolved_win}  loss={s.resolved_loss}  "
            f"timeout={s.resolved_timeout}  still_open={s.still_open}"
        )
        return 0

    if args.cmd == "paper-report":
        def pr_table(rows: list[dict], label: str) -> None:
            print(f"\n{label}")
            print(
                f"{'tag':<19} {'open':>7} {'win':>7} {'loss':>7} {'timeout':>7} "
                f"{'winrate':>7} {'avgR':>6} {'avgR(timeout)':>13}"
            )
            for r in rows:
                wr = f"{r['win_rate'] * 100:.0f}%" if r["win_rate"] is not None else "  -"
                avg_r = f"{r['avg_r_resolved']:+.2f}" if r["avg_r_resolved"] is not None else "  -"
                avg_rt = f"{r['avg_r_timeout']:+.2f}" if r["avg_r_timeout"] is not None else "  -"
                print(
                    f"{r['tag']:<19} {r['n_open']:>7} {r['n_win']:>7} {r['n_loss']:>7} "
                    f"{r['n_timeout']:>7} {wr:>7} {avg_r:>6} {avg_rt:>13}"
                )

        intro = (
            "winrate = win / (win+loss), timeouts excluded. avgR = realized R-multiple "
            f"on resolved trades. Mechanical ATR entry/SL/target, {paper.MAX_HOLD_DAYS}td max hold — "
            "see src/vg/paper.py. Probation: informational, not the forward-test gate itself."
        )
        if args.since:
            since = _d(args.since)
            rows = paper.report(since)
            if not any(r["n_open"] + r["n_win"] + r["n_loss"] + r["n_timeout"] for r in rows):
                print(f"no paper trades signalled on/after {since} yet")
                return 0
            print(intro)
            pr_table(rows, f"signals on/after {since}")
            return 0

        allrows = paper.report()
        if not any(r["n_open"] + r["n_win"] + r["n_loss"] + r["n_timeout"] for r in allrows):
            print("no paper trades yet — run `vg paper-update` first")
            return 0
        print(intro)
        pr_table(allrows, "in-sample — full history")
        if CONFIG.forward_since:
            fwd = paper.report(CONFIG.forward_since)
            if any(r["n_open"] + r["n_win"] + r["n_loss"] + r["n_timeout"] for r in fwd):
                pr_table(fwd, f"forward — signals on/after {CONFIG.forward_since} (VG_FORWARD_SINCE)")
            else:
                print(f"\nforward (on/after {CONFIG.forward_since}): no paper trades yet")
        return 0

    if args.cmd == "eval":
        def pc(x: float | None) -> str:
            return f"{x * 100:+.2f}%" if x is not None else "   -  "

        def _table(stats: list[dict], label: str) -> None:
            print(f"\n{label}")
            print(
                f"{'tag':<17}{'n':>7} {'avgH5':>8} {'edgeH1':>8} {'edgeH5':>8} {'edgeH10':>8} "
                f"{'hit':>5} {'uHit':>5}  | {'nLiq':>6} {'edgeH5L':>8} {'hitL':>5}"
            )
            for s in stats:
                print(
                    f"{s['tag']:<17}{s['n']:>7} {pc(s['avg_h5']):>8} {pc(s['edge_h1']):>8} "
                    f"{pc(s['edge_h5']):>8} {pc(s['edge_h10']):>8} "
                    f"{(s['hit_rate'] or 0) * 100:>4.0f}% {(s['univ_hit_rate'] or 0) * 100:>4.0f}%  | "
                    f"{s['n_liquid'] or 0:>6} {pc(s['edge_h5_liquid']):>8} "
                    f"{(s['hit_rate_liquid'] or 0) * 100:>4.0f}%"
                )

        intro = (
            "edge = tag return minus equal-weight universe on the same day. "
            "hit/uHit = % positive for tag vs universe. "
            f"liquid = signals on names > Rp{engine.LIQUID_VALUE_IDR/1e9:g}B traded that day."
        )

        if args.since:
            since = _d(args.since)
            stats = engine.eval_tags(since)
            if not stats:
                print(f"no resolved signal_log rows on/after {since} yet")
                return 0
            print(intro)
            _table(stats, f"forward — signals on/after {since}")
            return 0

        allstats = engine.eval_tags()
        if not allstats:
            print("no resolved signal_log rows yet (need ~2 weeks of data + backfill-outcomes)")
            return 0
        print(intro)
        _table(allstats, "in-sample — full history")
        if CONFIG.forward_since:
            fwd = engine.eval_tags(CONFIG.forward_since)
            if fwd:
                _table(fwd, f"forward — signals on/after {CONFIG.forward_since} (VG_FORWARD_SINCE)")
            else:
                print(f"\nforward (on/after {CONFIG.forward_since}): no resolved rows yet")
        return 0

    if args.cmd == "season":
        from . import season

        s = season.seasonality(args.ticker, args.years)
        if not s["n_days"]:
            print(f"no price history for {s['ticker']}")
            return 1

        def _row(r: dict) -> str:
            up = f"{r['pct_up'] * 100:.0f}%" if r["pct_up"] is not None else "-"
            mr = f"{r['mean_ret'] * 100:+.2f}%" if r["mean_ret"] is not None else "-"
            return f"  {r['label']:<5}{r['n']:>5}{up:>7}{mr:>10}"

        print(f"{s['ticker']} seasonality  {s['span'][0]}..{s['span'][1]}  ({s['n_days']} days)\n")
        print("day-of-week (close-to-close):")
        print(f"  {'day':<5}{'n':>5}{'up%':>7}{'meanRet':>10}")
        for r in s["day_of_week"]:
            print(_row(r))
        print("\nmonth  (n = years observed — thin, read with care):")
        print(f"  {'mon':<5}{'n':>5}{'up%':>7}{'meanRet':>10}")
        for r in s["month"]:
            print(_row(r))
        return 0

    if args.cmd == "ownership":
        rows = engine.ownership_history(args.ticker.upper(), args.months)
        if not rows:
            print(f"no KSEI ownership rows for {args.ticker.upper()}")
            return 1

        def _p(x: float | None) -> str:
            return f"{x * 100:.2f}%" if x is not None else "  -  "

        print(f"{args.ticker.upper()} — KSEI monthly ownership (% of the scripless base)\n")
        print(f"  {'month':<10}{'%foreign':>10}{'%locInst':>10}{'%locIndiv':>11}"
              f"{'scripless':>11}{'holders':>10}")
        for r in rows:
            hc = f"{int(r['holders']):,}" if r["holders"] else "-"
            print(f"  {r['month'].isoformat():<10}{_p(r['foreign_pct']):>10}"
                  f"{_p(r['inst_pct']):>10}{_p(r['indiv_pct']):>11}"
                  f"{_p(r['scripless_pct']):>11}{hc:>10}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
