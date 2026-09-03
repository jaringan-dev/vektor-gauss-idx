"""Build the Telegram EOD digest and the weekly eval message.

RENCANA-FASE-1 §7. Plain, no trade calls (no target / stop-loss / BUY-SELL) —
a data-reading tool. Data-quality flags are surfaced so you know when a day's
numbers can't be trusted (PRINSIP-DATA §C).
"""

from __future__ import annotations

import html
from datetime import date

from . import engine
from .config import CONFIG
from .db import connect

_TREND = {"up": "↑", "down": "↓", "flat": "→", "nodata": "?"}


def _idr(x: float | None) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1e12:
        return f"{x / 1e12:+.1f}T".replace(".", ",")
    if a >= 1e9:
        return f"{x / 1e9:+.1f}B".replace(".", ",")
    if a >= 1e6:
        return f"{x / 1e6:+.0f}Jt".replace(".", ",")
    return f"{x:+.0f}"


def _pct(x: float | None, dp: int = 1) -> str:
    return f"{x * 100:+.{dp}f}%".replace(".", ",") if x is not None else "n/a"


def _f(x) -> float | None:
    return float(x) if x is not None else None


def _price(v) -> str:
    return f"{float(v):,.0f}".replace(",", ".") if v is not None else "?"


def _prev_trading_day(cur, session_date: date) -> date | None:
    cur.execute(
        "SELECT session_date FROM trading_calendar WHERE session_date < %s "
        "ORDER BY session_date DESC LIMIT 1",
        (session_date,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _tags_by_ticker(cur, session_date: date, tickers: list[str]) -> dict[str, set[str]]:
    if not tickers:
        return {}
    cur.execute(
        "SELECT ticker, tag FROM signal_log WHERE session_date = %s AND ticker = ANY(%s)",
        (session_date, tickers),
    )
    out: dict[str, set[str]] = {}
    for t, tag in cur.fetchall():
        out.setdefault(t, set()).add(tag)
    return out


_FLOW_TAGS = ("AKUM-ASING", "DISTRIBUSI-ASING", "MARKUP", "MARKDOWN")


def _primary(tags: set[str]) -> str | None:
    for t in _FLOW_TAGS:
        if t in tags:
            return t
    return None


def build_daily(session_date: date | None = None) -> str:
    wl = list(CONFIG.watchlist)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(session_date) FROM price_bars")
        latest_price = cur.fetchone()[0]
        sd = session_date or latest_price
        if sd is None:
            return "VEKTOR GAUSS: belum ada data price_bars."

        cur.execute("SELECT max(session_date) FROM index_bars")
        latest_index = cur.fetchone()[0]
        cur.execute("SELECT max(month_end) FROM ownership_monthly")
        latest_own = cur.fetchone()[0]

        L: list[str] = []
        ok_price = latest_price == sd
        ok_index = latest_index == sd
        ksei_str = f"{latest_own:%b %Y}" if latest_own else "-"
        L.append(f"\U0001F4CA <b>VEKTOR GAUSS</b> — EOD {sd:%d %b %Y}")
        L.append(
            f"harga {'✓' if ok_price else '⚠'} · "
            f"indeks {'✓' if ok_index else '⚠'} · "
            f"KSEI {ksei_str}"
        )
        if not ok_price:
            L.append(f"⚠ harga terbaru = {latest_price} (bukan {sd}). Jangan analisa data lama.")

        # -- watchlist ----------------------------------------------------
        tags_today = _tags_by_ticker(cur, sd, wl)
        prev = _prev_trading_day(cur, sd)
        tags_prev = _tags_by_ticker(cur, prev, wl) if prev else {}

        if wl:
            cur.execute(
                """
                SELECT pb.ticker, pb.close, pb.prev, sc.price_chg_5d, sc.nff_d0, sc.nff_w1,
                       sc.nff_w2, sc.foreign_consistency_10d, sc.quadrant,
                       sc.ksei_foreign_trend_3m, sc.ksei_retail_trend_3m, sc.data_flags
                FROM price_bars pb
                LEFT JOIN signal_components sc
                       ON sc.session_date = pb.session_date AND sc.ticker = pb.ticker
                WHERE pb.session_date = %s AND pb.ticker = ANY(%s)
                """,
                (sd, wl),
            )
            byt = {r[0]: r for r in cur.fetchall()}
            L.append("\n<b>WATCHLIST</b>")
            for t in wl:
                r = byt.get(t)
                if r is None:
                    L.append(f"{t}  — tidak diperdagangkan")
                    continue
                _, close, prv, chg5, nff0, nff1, nff2, cons, quad, kf, kr, flags = r
                chg1 = (float(close) / float(prv) - 1) if (close and prv) else None
                tset = tags_today.get(t, set())
                prim = _primary(tset)
                changed = prev and _primary(tags_prev.get(t, set())) != prim
                ann = [x for x in tset if x not in _FLOW_TAGS]

                line1 = (
                    f"<b>{t}</b>  {_price(close)} ({_pct(chg1)})   "
                    f"NFF D0 {_idr(_f(nff0))}  W1 {_idr(_f(nff1))}"
                )
                if cons is not None:
                    line1 += f"  asing {cons}/10"
                L.append(line1)

                bits = []
                if quad:
                    bits.append(quad.lower())
                bits.append(f"KSEI %asing {_TREND.get(kf, '?')}")
                if prim:
                    bits.append(f"<b>{prim}</b>" + (" ⚡" if changed else ""))
                for a in sorted(ann):
                    bits.append("⚠ " + a if a.startswith("KSEI") or a == "CROSSING-TINGGI" else a)
                for fl in (flags or []):
                    if fl not in ann and fl != "CROSSING-TINGGI":
                        bits.append("⚠ " + fl)
                L.append("   " + " · ".join(html.escape(b, quote=False) for b in bits))

        # -- radar ------------------------------------------------------
        cur.execute(
            """
            SELECT sl.ticker, sc.nff_w1, pb.close, pb.prev
            FROM signal_log sl
            JOIN signal_components sc
              ON sc.session_date = sl.session_date AND sc.ticker = sl.ticker
            JOIN price_bars pb
              ON pb.session_date = sl.session_date AND pb.ticker = sl.ticker
            WHERE sl.session_date = %s AND sl.tag = 'AKUM-ASING'
              AND NOT (sl.ticker = ANY(%s))
            ORDER BY sc.nff_w1 DESC NULLS LAST
            LIMIT 12
            """,
            (sd, wl or [""]),
        )
        radar = cur.fetchall()
        if radar:
            L.append("\n<b>RADAR</b> — AKUM-ASING hari ini (di luar watchlist)")
            for t, nff1, close, prv in radar:
                chg1 = (float(close) / float(prv) - 1) if (close and prv) else None
                L.append(f"{t}  {_price(close)} ({_pct(chg1)})  W1 {_idr(_f(nff1))}")

        # -- totals ---------------------------------------------------
        cur.execute(
            "SELECT tag, count(*) FROM signal_log WHERE session_date = %s GROUP BY tag ORDER BY tag",
            (sd,),
        )
        counts = cur.fetchall()
        if counts:
            L.append("\n" + " · ".join(f"{tag}:{n}" for tag, n in counts))

    L.append(
        "\n<i>Analisis data publik IDX/KSEI. Bukan nasihat / ajakan jual-beli. "
        "Semua sinyal masih tahap uji (bobot 0).</i>"
    )
    return "\n".join(L)


def build_weekly_eval() -> str:
    stats = engine.eval_tags()
    if not stats:
        return (
            "\U0001F4C8 <b>VEKTOR GAUSS — EVAL MINGGUAN</b>\n"
            "Belum ada baris signal_log yang matang (butuh ~2 minggu data + backfill-outcomes)."
        )
    rows = [f"{'tag':<17}{'n':>4}{'avgH5':>9}{'vsIHSG':>9}{'hit':>6}{'avgH10':>9}"]
    for s in stats:
        rows.append(
            f"{s['tag']:<17}{s['n']:>4}"
            f"{_pct(s['avg_h5'], 2):>9}{_pct(s['avg_vs_ihsg_h5'], 2):>9}"
            f"{(s['hit_rate'] or 0) * 100:>5.0f}%{_pct(s['avg_h10'], 2):>9}"
        )
    body = html.escape("\n".join(rows), quote=False)
    return (
        "\U0001F4C8 <b>VEKTOR GAUSS — EVAL MINGGUAN</b>\n"
        f"<pre>{body}</pre>\n"
        "<i>avgH5 = rata-rata return 5 hari bursa sejak tag muncul. "
        "vsIHSG = relatif IHSG. Sinyal 'lulus' ke lapis berbobot butuh "
        "n≥50, ≥2 bulan, dan edge yang jelas setelah biaya.</i>"
    )
