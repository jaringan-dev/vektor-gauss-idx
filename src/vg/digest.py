"""Build the Telegram EOD digest and the weekly eval message.

RENCANA-FASE-1 §7. Plain, no trade calls (no target / stop-loss / BUY-SELL) —
a data-reading tool. Data-quality flags are surfaced so you know when a day's
numbers can't be trusted (PRINSIP-DATA §C).
"""

from __future__ import annotations

import html
from datetime import date

from . import calc, engine
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


def _action(prim: str | None, tags: set[str], held: bool) -> str:
    if prim == "AKUM-ASING":
        return "ADD" if held else "BUY"
    if prim == "MARKUP":
        return "HOLD" if held else "WATCH"           # already running — don't chase
    if prim in ("DISTRIBUSI-ASING", "MARKDOWN"):
        return "REDUCE/SELL" if held else "AVOID"
    if "KSEI-DIVERGEN" in tags:
        return "HATI-HATI"
    return "HOLD" if held else "WATCH"


def _trade_line(
    close: float | None, atr: float | None, prim: str | None, tags: set[str], held: bool
) -> str | None:
    """One-line trade call. Config-gated (VG_DIGEST_TRADE_CALLS). ATR-mechanical,
    not a forecast — MUST be reworked/removed if the digest is ever sold."""
    if not CONFIG.digest_trade_calls or close is None:
        return None
    action = _action(prim, tags, held)

    if action in ("BUY", "ADD") and atr:
        p = calc.plan(
            close, calc.atr_stop(close, atr, CONFIG.atr_sl_mult),
            target_r=CONFIG.target_r, balance=CONFIG.account_balance,
            risk_pct=CONFIG.risk_pct, fee_pct=CONFIG.fee_pct,
        )
        parts = [
            f"aksi <b>{action}</b>",
            f"SL {_price(p.stop_loss)} ({_pct(p.stop_loss / close - 1)})",
        ]
        if p.target:
            parts.append(f"target {_price(p.target)} ({_pct(p.target / close - 1)})")
        if p.rr:
            parts.append(f"R:R {p.rr:.1f}".replace(".", ","))
        if p.lots:
            parts.append(f"~{p.lots} lot (risiko {_pct(p.risk_pct_of_balance)} akun)")
        basis = (
            f"\n   dasar: SL = close − {CONFIG.atr_sl_mult:g}×ATR14"
            f" ({_price(atr)}); target = {CONFIG.target_r:g}R · mekanis, bukan prediksi"
        )
        return " · ".join(parts) + basis

    if action == "HOLD" and atr:
        ts = calc.atr_stop(close, atr, CONFIG.atr_sl_mult)
        return f"aksi <b>HOLD</b> · trailing SL {_price(ts)} ({_pct(ts / close - 1)})"
    if action == "REDUCE/SELL":
        return "aksi <b>REDUCE/SELL</b> · pemicu: asing distribusi / markdown"
    if action == "HATI-HATI":
        return "aksi <b>HATI-HATI</b> · flow harian vs kepemilikan KSEI bertentangan"
    return f"aksi {action}"


_QUAD_EMOJI = {
    "MARKUP": "📈",
    "AKUMULASI": "🟢",
    "DISTRIBUSI": "🔻",
    "MARKDOWN": "📉",
    "NETRAL": "▪️",
}

# Confirmation annotations read positive (KSEI agrees with the flow signal);
# everything else on the KSEI / crossing family is a caution.
_ANNOT_OK = {"KSEI-KONFIRM"}


def _annot(tag: str) -> str:
    if tag in _ANNOT_OK:
        return "✅ " + tag
    if tag.startswith("KSEI") or tag == "CROSSING-TINGGI":
        return "⚠️ " + tag
    return tag


def _hr(label: str) -> str:
    return f"<b>━━━━━  {label}  ━━━━━</b>"


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

        ok_price = latest_price == sd
        ok_index = latest_index == sd
        ksei_str = f"{latest_own:%b %Y}" if latest_own else "-"

        L: list[str] = []
        L.append(f"\U0001F4CA  <b>VEKTOR GAUSS</b>  ·  EOD {sd:%d %b %Y}")
        L.append(
            f"harga {'✅' if ok_price else '⚠️'}  ·  "
            f"indeks {'✅' if ok_index else '⚠️'}  ·  "
            f"KSEI {ksei_str}"
        )
        if not ok_price:
            L.append(
                f"⚠️ harga terbaru = {latest_price} (bukan {sd}). "
                "Jangan analisa data lama."
            )

        # -- watchlist --------------------------------------------------
        tags_today = _tags_by_ticker(cur, sd, wl)
        prev = _prev_trading_day(cur, sd)
        tags_prev = _tags_by_ticker(cur, prev, wl) if prev else {}
        held = set(CONFIG.holdings)

        if wl:
            cur.execute(
                """
                SELECT pb.ticker, pb.close, pb.prev, sc.atr14, sc.nff_d0, sc.nff_w1,
                       sc.foreign_consistency_10d, sc.quadrant, sc.ksei_foreign_trend_3m,
                       sc.avg_ticket_z, sc.data_flags
                FROM price_bars pb
                LEFT JOIN signal_components sc
                       ON sc.session_date = pb.session_date AND sc.ticker = pb.ticker
                WHERE pb.session_date = %s AND pb.ticker = ANY(%s)
                """,
                (sd, wl),
            )
            byt = {r[0]: r for r in cur.fetchall()}
            L.append("")
            L.append(_hr("WATCHLIST"))
            for t in wl:
                L.append("")
                r = byt.get(t)
                if r is None:
                    L.append(f"▪️ <b>{t}</b>  — tidak diperdagangkan")
                    continue
                _, close, prv, atr, nff0, nff1, cons, quad, kf, atz, flags = r
                chg1 = (float(close) / float(prv) - 1) if (close and prv) else None
                tset = tags_today.get(t, set())
                prim = _primary(tset)
                changed = bool(prev) and _primary(tags_prev.get(t, set())) != prim
                ann = sorted(x for x in tset if x not in _FLOW_TAGS)

                head = (
                    f"{_QUAD_EMOJI.get(quad or '', '▪️')} <b>{t}</b>"
                    f"   {_price(close)}   <b>{_pct(chg1)}</b>"
                )
                if t in held:
                    head += "   📌"
                if changed:
                    head += "   ⚡"
                L.append(head)

                ctx: list[str] = []
                if quad:
                    ctx.append(quad)
                if cons is not None:
                    ctx.append(f"asing {cons}/10")
                ctx.append(f"KSEI %asing {_TREND.get(kf, '?')}")
                if atz is not None and abs(float(atz)) >= 1.0:
                    ctx.append(f"tiket z{float(atz):+.1f}".replace(".", ","))
                if prim in ("AKUM-ASING", "DISTRIBUSI-ASING"):
                    ctx.append(f"<b>{prim}</b>")
                L.append("   " + "  ·  ".join(ctx))

                L.append(
                    f"   Asing bersih — hari ini {_idr(_f(nff0))}"
                    f"   ·   1 minggu terakhir {_idr(_f(nff1))}"
                )

                marks = [_annot(a) for a in ann]
                for fl in flags or []:
                    if fl not in ann and fl != "CROSSING-TINGGI":
                        marks.append("⚠️ " + fl)
                if marks:
                    L.append("   " + "  ·  ".join(marks))

                tl = _trade_line(_f(close), _f(atr), prim, tset, t in held)
                if tl:
                    L.append("   " + tl)

        # -- radar ----------------------------------------------------
        radar_tags = list(CONFIG.radar_tags)
        if radar_tags:
            cur.execute(
                """
                SELECT sl.ticker,
                       string_agg(DISTINCT sl.tag, ', ' ORDER BY sl.tag) AS tags,
                       sc.nff_w1, sc.atr14, pb.close, pb.prev
                FROM signal_log sl
                JOIN signal_components sc
                  ON sc.session_date = sl.session_date AND sc.ticker = sl.ticker
                JOIN price_bars pb
                  ON pb.session_date = sl.session_date AND pb.ticker = sl.ticker
                WHERE sl.session_date = %s AND sl.tag = ANY(%s)
                  AND NOT (sl.ticker = ANY(%s))
                GROUP BY sl.ticker, sc.nff_w1, sc.atr14, pb.close, pb.prev
                ORDER BY sc.nff_w1 DESC NULLS LAST
                LIMIT 12
                """,
                (sd, radar_tags, wl or [""]),
            )
            radar = cur.fetchall()
            if radar:
                L.append("")
                L.append(_hr("RADAR"))
                L.append("<i>" + " / ".join(radar_tags) + "  ·  di luar watchlist</i>")
                for t, tags, nff1, atr, close, prv in radar:
                    cf, af = _f(close), _f(atr)
                    chg1 = (cf / float(prv) - 1) if (cf and prv) else None
                    L.append("")
                    L.append(f"<b>{t}</b>   {_price(close)}   <b>{_pct(chg1)}</b>")
                    sub = f"   Asing bersih 1 minggu terakhir {_idr(_f(nff1))}   ·   {tags}"
                    if CONFIG.digest_trade_calls and cf and af:
                        p = calc.plan(cf, calc.atr_stop(cf, af, CONFIG.atr_sl_mult),
                                      target_r=CONFIG.target_r, fee_pct=CONFIG.fee_pct)
                        sub += f"\n   SL {_price(p.stop_loss)}  →  target {_price(p.target)}"
                    L.append(sub)

        # -- totals ---------------------------------------------------
        cur.execute(
            "SELECT tag, count(*) FROM signal_log WHERE session_date = %s GROUP BY tag",
            (sd,),
        )
        counts = sorted(cur.fetchall(), key=lambda r: (-r[1], r[0]))
        if counts:
            w = max(len(tag) for tag, _ in counts)
            body = "\n".join(f"{tag.ljust(w)}  {n:>3}" for tag, n in counts)
            L.append("")
            L.append(_hr("SINYAL HARI INI"))
            L.append(f"<pre>{html.escape(body, quote=False)}</pre>")

    L.append("")
    legend = "⚡ tag utama berubah dari sesi lalu"
    if held:
        legend += "   ·   📌 posisi Anda"
    L.append(f"<i>{legend}</i>")

    foot = (
        "<i>Analisis data publik IDX/KSEI untuk pemakaian pribadi. "
        "Sinyal masih tahap uji (bobot 0)."
    )
    if CONFIG.digest_trade_calls:
        foot += (
            " Aksi/SL/target = aturan mekanis (ATR), bukan prediksi — "
            "keputusan di tangan Anda."
        )
    foot += "</i>"
    L.append(foot)
    return "\n".join(L)


def _eval_table(stats: list[dict]) -> str:
    rows = [f"{'tag':<17}{'n':>7}{'edgeH5':>9}{'hit':>5}{'uHit':>5}{'nLiq':>7}{'edgeH5L':>9}"]
    for s in stats:
        rows.append(
            f"{s['tag']:<17}{s['n']:>7}"
            f"{_pct(s['edge_h5'], 2):>9}"
            f"{(s['hit_rate'] or 0) * 100:>4.0f}%{(s['univ_hit_rate'] or 0) * 100:>4.0f}%"
            f"{s['n_liquid'] or 0:>7}{_pct(s['edge_h5_liquid'], 2):>9}"
        )
    return html.escape("\n".join(rows), quote=False)


def build_weekly_eval() -> str:
    stats = engine.eval_tags()
    if not stats:
        return (
            "\U0001F4C8 <b>VEKTOR GAUSS — EVAL MINGGUAN</b>\n"
            "Belum ada baris signal_log yang matang (butuh ~2 minggu data + backfill-outcomes)."
        )
    parts = [
        "\U0001F4C8 <b>VEKTOR GAUSS — EVAL MINGGUAN</b>",
        "<b>in-sample (seluruh riwayat)</b>",
        f"<pre>{_eval_table(stats)}</pre>",
    ]
    if CONFIG.forward_since:
        fwd = engine.eval_tags(CONFIG.forward_since)
        if fwd:
            parts.append(f"<b>forward (sejak {CONFIG.forward_since})</b>")
            parts.append(f"<pre>{_eval_table(fwd)}</pre>")
        else:
            parts.append(
                f"<i>forward (sejak {CONFIG.forward_since}): belum ada baris matang.</i>"
            )
    parts.append(
        "<i>edgeH5 = return 5 hari tag minus rata-rata universe hari yang sama. "
        "hit/uHit = % positif tag vs universe. nLiq/edgeH5L = idem untuk saham "
        "&gt;Rp5B/hari. 'Lulus' ke lapis berbobot butuh n≥50, ≥2 bulan, edge jelas "
        "setelah biaya (~150-190bp).</i>"
    )
    return "\n".join(parts)
