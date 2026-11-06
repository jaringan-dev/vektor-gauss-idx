"""CLI:  python -m vg <command>   (or `vg <command>` after `pip install -e .`)"""

from __future__ import annotations

import argparse
import html
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


def build_parser() -> argparse.ArgumentParser:
    """The CLI definition, separate from `main` so tests can check that a command the UI
    recommends (e.g. the coverage page's repair hints) really parses."""
    p = argparse.ArgumentParser(prog="vg", description="Vektor Gauss — Phase 1 collectors")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="apply db/schema.sql (idempotent)")

    cp = sub.add_parser("collect-price", help="IDX Stock Summary -> price_bars")
    g = cp.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", metavar="YYYY-MM-DD", help="one trading day")
    g.add_argument("--latest", action="store_true", help="most recent published day")
    g.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    cph = sub.add_parser(
        "collect-price-history",
        help="VG feed daily OHLCV+flow (Tier 3, Cloudflare-free) -> price_bars (gap-fill only)",
    )
    gph = cph.add_mutually_exclusive_group(required=True)
    gph.add_argument("--universe", action="store_true", help="watchlist + holdings")
    gph.add_argument("--ticker", metavar="CODE", help="one ticker (~120 td window)")
    gph.add_argument("--all", action="store_true",
                     help="EVERY ticker on the newest session — repairs older price gaps (1 feed call each)")

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

    bf = sub.add_parser(
        "collect-broker-full",
        help="COMPLETE broker tables (every broker that traded, feed all_data=true) -> broker_flows_full; feeds the stalker / inventory pages only",
    )
    bf.add_argument("--sessions", type=int, default=1, metavar="N", help="the newest N trading days, newest first (default 1 = today's pass)")
    bf.add_argument("--budget", type=int, default=None, metavar="CALLS", help="stop after about this many feed calls (a rerun carries on)")
    bf.add_argument("--plan", action="store_true", help="show what would be fetched, fetch nothing")

    cb = sub.add_parser(
        "collect-broker-summary",
        help="broker summary (vektorgauss, Tier 3, forward-only) -> broker_flows",
    )
    gb = cb.add_mutually_exclusive_group(required=True)
    gb.add_argument("--latest", action="store_true",
                    help="watchlist + holdings, stamped to the latest trading day")
    gb.add_argument("--radar", action="store_true",
                    help="watchlist + holdings + today's radar-tagged names (two-pass EOD)")
    gb.add_argument("--ticker", metavar="CODE", help="one ticker (e.g. BBCA)")
    gb.add_argument("--full", action="store_true",
                    help="watchlist + holdings + EVERY liquid name + radar extras (the EOD default)")
    cb.add_argument("--plan", action="store_true",
                    help="with --full: show what would be fetched, fetch nothing")
    cb.add_argument("--date", metavar="YYYY-MM-DD",
                    help="with --full: fill that PAST trading day instead of the newest (feed serves history)")
    cb.add_argument("--backfill", nargs=2, metavar=("START", "END"),
                    help="with --full: fill every trading day in the inclusive range, oldest first")
    cb.add_argument("--levels-only", action="store_true",
                    help="with --full: treat a name as stored only if broker_levels has it (re-fetch the gross sell side)")

    cbi = sub.add_parser(
        "collect-broker-inventory",
        help="VG feed broker-accumulation (~80 td cum net, Tier 3) -> broker_inventory",
    )
    gbi = cbi.add_mutually_exclusive_group(required=True)
    gbi.add_argument("--universe", action="store_true", help="watchlist + holdings")
    gbi.add_argument("--ticker", metavar="CODE", help="one ticker (~80 td history)")

    cdd = sub.add_parser(
        "collect-done-details",
        help="VG feed done-details -> done_by_price aggregate (Tier 3, on-demand, expensive)",
    )
    grp = cdd.add_mutually_exclusive_group(required=True)
    grp.add_argument("--ticker", metavar="CODE", help="one ticker")
    grp.add_argument(
        "--watchlist-only", action="store_true",
        help="WATCHLIST + holdings, then VG_LQ45_TICKERS (what the EOD uses); capped by VG_DONE_BUDGET_MINUTES. "
             "There is intentionally no whole-market mode.")
    cdd.add_argument("--date", metavar="YYYY-MM-DD", help="one day (default: latest trading day)")
    cdd.add_argument("--max-pages", type=int, default=200, help="page cap (100 ticks/page)")
    cdd.add_argument("--force", action="store_true", help="re-fetch even if already stored")

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

    rp = sub.add_parser("repair-prices", help="fix known-bad price rows (adjusted fallback prices, bad open); dry-run unless --apply")
    rp.add_argument("--apply", action="store_true", help="write the repairs (default: only print the plan)")

    qg = sub.add_parser("quality-gate", help="check the day's inputs before compute; exit 1 on a BLOCK finding")
    qg.add_argument("--date", metavar="YYYY-MM-DD", help="default: the latest confirmed trading day")
    qg.add_argument("--no-alert", action="store_true", help="do not send the Telegram alert")

    sub.add_parser("compute-adj", help="rebuild adj_factor + price_adj (split/bonus/dividend) from corp_actions")
    sub.add_parser("adj-report", help="validate adjustment factors vs realised ex-date moves + effect on 5d returns")

    cm = sub.add_parser("compute", help="engine v0: components + probation tags -> signal_*")
    gm = cm.add_mutually_exclusive_group(required=True)
    gm.add_argument("--date", metavar="YYYY-MM-DD", help="one trading day")
    gm.add_argument("--latest", action="store_true", help="the latest confirmed trading day")
    gm.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="inclusive range")

    sub.add_parser("backfill-outcomes", help="fill forward returns on resolved signal_log rows")
    bw = sub.add_parser(
        "backfill-wash",
        help="IN-SAMPLE history of AKUM-NETRAL-WASH from the stored broker scores; only sessions before VG_FORWARD_SINCE",
    )
    bw.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="first session (default: the first one with a broker score)")
    bw.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="last session (default and maximum: the day before VG_FORWARD_SINCE)")
    bw.add_argument("--dry-run", action="store_true", help="count what would be written, write nothing")
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

    rv = sub.add_parser("risk-eval", help="backtest the mechanical stop/target rule per pattern (retrospective)")
    rv.add_argument("--since", type=date.fromisoformat, default=None, help="first signal day (default: all signal_log history)")
    rv.add_argument("--scope", choices=("all", "liquid"), default="all", help="liquid = signals on stocks trading > Rp5B that day")
    rv.add_argument("--min-n", type=int, default=30)
    rv.add_argument("--save", action="store_true", help="store both scopes in analysis_snapshot for the dashboard (/risk-test)")

    rt = sub.add_parser(
        "record-trades",
        help="record the feed's running-trade WebSocket into trade_flow_1m (one row per stock per minute) until the market closes")
    rt.add_argument("--max-seconds", type=float, default=None, help="stop after this many seconds (a smoke test); default: until 16:05 WIB")
    rt.add_argument("--flush-seconds", type=float, default=10.0, help="how often the minute buckets are written to the database")
    rt.add_argument("--dry-run", action="store_true", help="connect and count, write nothing (no database rows, no run_log)")

    st = sub.add_parser("status", help="is the system running? checks + Telegram status message (daily); the dashboard shows the same at /health")
    st.add_argument("--telegram", action="store_true", help="send the report to Telegram (OK reports from evening/backfill are silent)")
    st.add_argument("--kind", choices=("morning", "evening", "backfill"), default="morning")
    st.add_argument("--no-save", action="store_true", help="do not store the report for the dashboard")

    rm = sub.add_parser(
        "remind-verify",
        help="send the post-EOD verification checklist to Telegram, ONCE, after a healthy EOD (called at the end of eod.sh)")
    rm.add_argument("--force", action="store_true", help="send now regardless of readiness; does not count as the one-off send")
    rm.add_argument("--dry-run", action="store_true", help="print the message, send nothing, touch no database")

    bd = sub.add_parser("broker-deep", help="K7: fill broker history newest->oldest inside a feed-call budget (resumable; run nightly)")
    bd.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 2))
    bd.add_argument("--end", type=date.fromisoformat, default=date(2026, 6, 2))
    bd.add_argument("--budget", type=int, default=15000, help="max feed calls this run (the API allows ~21k/day, EOD needs ~1k)")
    bd.add_argument("--plan", action="store_true", help="show what is left, fetch nothing")
    bd.add_argument("--compute", action="store_true", help="after fetching, compute signals for the filled days and refresh outcomes")

    rc = sub.add_parser("research", help="is a pattern's edge real? dependence-aware CIs, FDR, size/liquidity-neutral edge, walk-forward, cost model")
    rc.add_argument("--scope", choices=("all", "liquid"), default="all")
    rc.add_argument("--sample", choices=("full", "forward"), default="full")
    rc.add_argument("--reps", type=int, default=2000, help="bootstrap replicates")
    rc.add_argument("--save", action="store_true", help="store the result in analysis_snapshot for the dashboard (/research)")

    sv = sub.add_parser("serve", help="local superadmin dashboard — forward-test tracker (needs .[web])")
    sv.add_argument("--host", default="127.0.0.1",
                    help="bind host (default: local-only). Any other address REQUIRES VG_WEB_PASSWORD (>= 12 chars) in .env")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--reload", action="store_true", help="auto-reload on code change (dev)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    if args.cmd == "collect-price-history":
        if args.ticker:
            _print(collect.collect_price_history_ticker(args.ticker))
        elif args.all:
            outs = collect.collect_price_history_all()
            for o in outs:
                _print(o)
            return 0 if outs and outs[-1].status == "ok" else 1
        else:
            outs = collect.collect_price_history_universe()
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} tickers with data")
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

    if args.cmd == "collect-broker-full":
        from . import brokerfull

        if args.plan:
            for d in brokerfull.recent_sessions(args.sessions):
                plan = collect.plan_broker_full(session_date=d, have_table=brokerfull.TABLE)
                print(f"session {plan['latest']}: universe {len(plan['universe'])} · already stored {len(plan['have'])} · would fetch {len(plan['todo'])}")
            return 0
        outs = brokerfull.collect_recent(args.sessions, budget=args.budget)
        for o in outs:
            _print(o)
        return 0 if outs and outs[-1].status == "ok" else 1

    if args.cmd == "collect-broker-summary":
        if (args.plan or args.date or args.backfill or args.levels_only) and not args.full:
            print("--plan / --date / --backfill / --levels-only only apply together with --full")
            return 2
        if args.backfill and (args.date or args.plan):
            print("--backfill cannot be combined with --date or --plan")
            return 2
        if args.ticker:
            with connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(session_date) FROM trading_calendar")
                latest = cur.fetchone()[0]
            if latest is None:
                print("no trading_calendar rows — run collect-price first")
                return 1
            _print(collect.collect_broker_summary_ticker(latest, args.ticker))
        elif args.full:
            day = _d(args.date) if args.date else None
            have = "broker_levels" if args.levels_only else "broker_flows"
            if args.plan:
                plan = collect.plan_broker_full(session_date=day, have_table=have)
                if plan["latest"] is None:
                    print(plan.get("reason") or "no trading_calendar rows — run collect-price first")
                    return 1
                print(f"session {plan['latest']}: universe {len(plan['universe'])} "
                      f"({plan['n_liquid']} liquid) · already stored {len(plan['have'])} · "
                      f"would fetch {len(plan['todo'])}")
                if plan["todo"]:
                    print("  e.g. " + ", ".join(plan["todo"][:15]) + (" …" if len(plan["todo"]) > 15 else ""))
                return 0
            if args.backfill:
                outs = collect.backfill_broker_full(_d(args.backfill[0]), _d(args.backfill[1]), have_table=have)
            else:
                outs = collect.collect_broker_summary_full(session_date=day, have_table=have)
            for o in outs:
                _print(o)
            return 0 if outs and outs[-1].status == "ok" else 1
        else:
            outs = (
                collect.collect_broker_summary_radar() if args.radar
                else collect.collect_broker_summary_latest()
            )
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} tickers with data")
        return 0

    if args.cmd == "collect-broker-inventory":
        if args.ticker:
            _print(collect.collect_broker_inventory_ticker(args.ticker))
        else:
            outs = collect.collect_broker_inventory_universe()
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            print(f"done: {ok}/{len(outs)} tickers with data")
        return 0

    if args.cmd == "collect-done-details":
        day = _d(args.date) if args.date else None
        if args.watchlist_only:
            targets = collect.done_targets()
            if not targets:
                print("no targets: set VG_WATCHLIST (and optionally VG_LQ45_TICKERS) in .env")
                return 0
            outs = collect.collect_done_details_universe(
                targets, day, max_pages=args.max_pages, force=args.force)
            for o in outs:
                _print(o)
            ok = sum(1 for o in outs if o.status == "ok")
            skipped = sum(1 for o in outs if o.status == "skipped")
            print(f"done: {ok}/{len(outs)} tickers with data" + (f", {skipped} skipped (budget)" if skipped else ""))
        else:
            _print(collect.collect_done_details_ticker(
                args.ticker, day, max_pages=args.max_pages, force=args.force,
            ))
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

    if args.cmd == "repair-prices":
        from . import repair
        r = repair.run(apply=args.apply)
        print(f"{'APPLIED' if args.apply else 'DRY-RUN'}: fallback rows checked {r['fallback_candidates']}, "
              f"repaired {r['repaired']}, flagged {r['flagged']}, open to NULL {r['open_nulled']}")
        for d, tk, old, new, what in r["plan"]:
            print(f"  {d} {tk:<6} close {old:>10.2f} -> {new:>10.2f}  ({what})")
        return 0

    if args.cmd == "quality-gate":
        from . import quality
        facts = quality.load_facts(date.fromisoformat(args.date) if args.date else None)
        findings = quality.evaluate(facts)
        report = quality.format_report(facts.day, findings)
        print(report)
        stop = quality.blocked(findings)
        try:
            with connect() as conn, conn.cursor() as cur:
                collect._log(cur, facts.day, "blocked" if stop else "ok", len(findings),
                             report if findings else None, "quality_gate")
        except Exception as exc:  # the gate's verdict must survive a logging failure
            print(f"(run_log write failed: {type(exc).__name__}: {exc})")
        if stop and not args.no_alert:
            sender = TelegramSender()
            if sender.configured:
                try:
                    sender.send("⚠️ Vektor Gauss — " + html.escape(report))
                except Exception as exc:  # noqa: BLE001 — alert failure must not mask the BLOCK exit code
                    print(f"(telegram alert failed: {type(exc).__name__}: {exc})")
        return 1 if stop else 0

    if args.cmd == "compute-adj":
        from . import adjust
        r = adjust.rebuild()
        print(f"adj_factor events: {r['events']}  {r['by_kind']}  price_adj rows: {r['price_adj_rows']}")
        print(f"skipped (not adjusted): {r['skipped']}")
        return 0

    if args.cmd == "adj-report":
        from . import adjust
        v = adjust.validate()
        for kind in ("split", "bonus", "dividend"):
            d = v[kind]
            print(f"{kind:<9}", "no events" if d is None else
                  f"n={d['n']} within5%={d['within_5pct']:.0%} median|rel err|={d['median_abs_rel_err']:.3f} "
                  f"mean(realised-expected)={d['mean_gap']:+.4f}")
        print("sample cases (kind ticker ex_date expected realised):")
        for kind, tk, ex, f, real in v["cases"]:
            print(f"  {kind:<9} {tk:<6} {ex}  expected {f:.4f}  realised {real:.4f}")
        print("effect on 5-bar returns by year (bp avg; % rows moved >50bp):")
        for y, n, sp, dv, ps, pd in adjust.h5_effect():
            print(f"  {y}  n={n:>8}  split {sp:>7}bp  dividend {dv:>7}bp  rows moved: split {ps}% dividend {pd}%")
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

    if args.cmd == "backfill-wash":
        try:
            res = engine.backfill_wash(_d(args.start) if args.start else None, _d(args.end) if args.end else None, dry_run=args.dry_run)
        except engine.WashBackfillRefused as exc:
            print(f"refused: {exc}")
            return 2
        if res["start"] is None:
            print("no stored broker scores (signal_components.bpi3), nothing to do")
            return 0
        verb = "would write" if args.dry_run else "wrote"
        print(f"{engine.WASH_TAG}: {verb} {res['rows']} rows on {res['days']} sessions, {res['start']} .. {res['end']}")
        for month, n in res["by_month"].items():
            print(f"  {month}: {n}")
        print("IN-SAMPLE: the threshold was chosen looking at 5 Nov 2026 data, so this is what the tag WOULD have said, not proof it works.")
        if not args.dry_run:
            print(f"signal_log rows updated with forward returns: {engine.backfill_outcomes()}")
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

    if args.cmd == "risk-eval":
        from . import risk_eval

        groups, window = risk_eval.run(args.since)
        print(risk_eval.format_text(risk_eval.table(groups, args.scope, args.min_n), window))
        if args.save:
            risk_eval.save_snapshot(risk_eval.snapshot_payload(groups, window, min_n=args.min_n))
            print("saved to analysis_snapshot (dashboard: /risk-test)")
        return 0

    if args.cmd == "record-trades":
        from . import tradeflow as _tradeflow

        return _tradeflow.record(max_seconds=args.max_seconds, flush_seconds=args.flush_seconds, dry_run=args.dry_run)

    if args.cmd == "remind-verify":
        from . import reminder as _reminder

        return _reminder.run(force=args.force, dry_run=args.dry_run)

    if args.cmd == "status":
        import re as _re

        from . import status as _status

        level, text, facts, checks = _status.report(args.kind)
        print(_re.sub(r"</?b>", "", text))
        if not args.no_save:
            try:
                _status.save_snapshot(facts, checks)
            except Exception as exc:  # noqa: BLE001
                print(f"(could not store the report: {type(exc).__name__}: {exc})")
        if args.telegram:
            sender = TelegramSender()
            if not sender.configured:
                print("Telegram is not configured (VG_TELEGRAM_BOT_TOKEN / VG_TELEGRAM_CHAT_ID): nothing sent")
                return 2
            try:
                sender.send(text, silent=_status.should_be_silent(level, args.kind))
            except Exception as exc:  # noqa: BLE001 — surface it: an unsent status report is itself a problem
                print(f"TELEGRAM SEND FAILED: {type(exc).__name__}: {exc}")
                return 2
        return 1 if level == _status.BAD else 0

    if args.cmd == "broker-deep":
        res = collect.backfill_broker_deep(args.start, args.end, budget=args.budget, plan_only=args.plan)
        print(f"range {args.start}..{args.end}: {res['days_total']} trading days; left to fetch: {res['pending_days']} days, "
              f"~{res['pending_calls']:,} calls (~{res['pending_calls'] / max(args.budget, 1):.1f} nights at {args.budget:,}/night)")
        if args.plan:
            return 0
        print(f"this run: {res['calls']:,} calls over {len(res['filled'])} days"
              + (f" ({res['filled'][-1]}..{res['filled'][0]})" if res["filled"] else ""))
        for o in res["outcomes"][:20]:
            print(f"  {o.session_date} {o.status}: {o.detail}")
        try:
            from . import status as _status

            _status.log_deep_run(res["calls"], res["error"])
        except Exception as exc:  # noqa: BLE001 — logging must never hide the run's own result
            print(f"(run_log write failed: {type(exc).__name__}: {exc})")
        if res["filled"] and args.compute:
            for d in sorted(res["filled"]):
                engine.compute_day(d)
            print(f"computed {len(res['filled'])} days; refreshing outcomes")
            engine.backfill_outcomes()
        return 1 if res["error"] else 0

    if args.cmd == "research":
        from . import research
        from .config import CONFIG

        result = research.run(CONFIG.forward_since, reps=args.reps)
        print(research.format_text(result, args.scope, args.sample))
        if args.save:
            research.save_snapshot(result)
            print("saved to analysis_snapshot (dashboard: /research)")
        return 0

    if args.cmd == "serve":
        try:
            import uvicorn
        except ModuleNotFoundError:
            print('web extra not installed. Run:  pip install -e ".[web]"')
            return 1
        from .config import CONFIG
        from .web.auth import startup_error

        problem = startup_error(args.host, CONFIG.web_password)
        if problem:
            print(problem)
            return 2
        gate = "password required" if CONFIG.web_password else "no password (local only)"
        print(f"superadmin dashboard → http://{args.host}:{args.port}  [{gate}]  (Ctrl-C to stop)")
        uvicorn.run("vg.web.app:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
