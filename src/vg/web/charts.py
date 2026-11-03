"""Server-side SVG charts for the stock page (RENCANA-ROMBAK-UI-v3 stage E) — no JavaScript, no chart library, no CDN.

Pure functions: plain numbers in, an SVG string out (or None when there is nothing honest to draw). Colours are CSS
variables (`--pos`, `--neg`, `--fg`, `--muted`, `--border`), so charts follow the light/dark theme. Every chart carries a
<title>/<desc> for screen readers and a native hover tooltip (<title>) on each data point; direction is never colour-only
(candles also differ in body fill, flow bars sit above/below a zero line). Text is escaped.
"""

from __future__ import annotations

from datetime import date
from html import escape

W = 720                      # viewBox width; the SVG scales to the container
PAD_L, PAD_R, PAD_T, PAD_B = 8, 58, 10, 22


def _fmt_price(v: float) -> str:
    return f"{v:,.0f}"


def _fmt_b(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:.1f}T"
    if a >= 1e9:
        return f"{v / 1e9:.0f}B"
    if a >= 1e6:
        return f"{v / 1e6:.0f}M"
    return f"{v:.0f}"


def _scale(lo: float, hi: float, top: float, bottom: float):
    span = (hi - lo) or 1.0
    return lambda v: bottom - (v - lo) / span * (bottom - top)


def _x_positions(n: int) -> tuple[list[float], float]:
    inner = W - PAD_L - PAD_R
    step = inner / max(n, 1)
    return [PAD_L + step * (i + 0.5) for i in range(n)], step


def _month_ticks(days: list[date], xs: list[float]) -> list[tuple[float, str]]:
    out, last = [], None
    for d, x in zip(days, xs):
        key = (d.year, d.month)
        if key != last:
            out.append((x, d.strftime("%b %y" if d.month == 1 else "%b")))
            last = key
    return out[1:] if len(out) > 1 else out


def _svg(uid: str, height: int, title: str, desc: str, body: str) -> str:
    return (f'<svg viewBox="0 0 {W} {height}" width="100%" role="img" aria-labelledby="{uid}-t {uid}-d" '
            f'style="max-width:{W}px;display:block;font:11px -apple-system,Segoe UI,Roboto,sans-serif">'
            f'<title id="{uid}-t">{escape(title)}</title><desc id="{uid}-d">{escape(desc)}</desc>{body}</svg>')


def _axis_x(days, xs, y_top, y_bottom) -> str:
    out = [f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y_bottom}" y2="{y_bottom}" stroke="var(--border)"/>']
    for x, label in _month_ticks(days, xs):
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y_top}" y2="{y_bottom}" stroke="var(--border)" stroke-dasharray="2 4"/>'
                   f'<text x="{x:.1f}" y="{y_bottom + 14}" text-anchor="middle" fill="var(--muted)">{escape(label)}</text>')
    return "".join(out)


def candles(bars: list[dict], *, levels: list[dict] | None = None, tag_counts: dict | None = None,
            height: int = 300) -> str | None:
    """bars: [{d: date, o, h, l, c, v}] oldest first. levels: [{price, label, kind: 'stop'|'target'}] drawn as dashed lines.
    tag_counts: {date: n patterns flagged} drawn as a strip under the volume bars."""
    bars = [b for b in bars if b["h"] and b["l"]]
    if len(bars) < 5:
        return None
    n = len(bars)
    xs, step = _x_positions(n)
    days = [b["d"] for b in bars]
    top, price_bottom = PAD_T, 188
    vol_top, vol_bottom = 200, 244
    strip_top, strip_bottom = 250, 272
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    shown = []
    for lv in (levels or []):
        if lv["kind"] == "stop" or lv["label"].startswith("1R"):
            lo, hi = min(lo, lv["price"]), max(hi, lv["price"])
            shown.append(lv)
        elif lo * 0.92 <= lv["price"] <= hi * 1.08:
            shown.append(lv)
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    y = _scale(lo, hi, top, price_bottom)
    body = []
    level_ys = [y(lv["price"]) for lv in shown]
    for i in range(5):                                        # horizontal grid + price labels
        v = lo + (hi - lo) * i / 4
        body.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--border)" stroke-dasharray="2 4"/>')
        if all(abs(y(v) - ly) > 12 for ly in level_ys):       # a level label wins over a plain gridline label
            body.append(f'<text x="{W - PAD_R + 4}" y="{y(v) + 3:.1f}" fill="var(--muted)">{_fmt_price(v)}</text>')
    cw = max(1.5, min(9.0, step * 0.62))
    for b, x in zip(bars, xs):
        up = b["c"] >= (b["o"] if b["o"] else b["c"])
        col = "var(--pos)" if up else "var(--neg)"
        o = b["o"] if b["o"] else b["c"]
        top_b, bot_b = y(max(o, b["c"])), y(min(o, b["c"]))
        tip = f'{b["d"].isoformat()}  O {_fmt_price(o)}  H {_fmt_price(b["h"])}  L {_fmt_price(b["l"])}  C {_fmt_price(b["c"])}  Vol {_fmt_b(b["v"] or 0)}'
        body.append(
            f'<g><title>{escape(tip)}</title>'
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y(b["h"]):.1f}" y2="{y(b["l"]):.1f}" stroke="{col}"/>'
            f'<rect x="{x - cw / 2:.1f}" y="{top_b:.1f}" width="{cw:.1f}" height="{max(1.0, bot_b - top_b):.1f}" '
            f'fill="{col if not up else "none"}" stroke="{col}"/></g>')
    for lv in shown:                                          # risk levels
        col = "var(--neg)" if lv["kind"] == "stop" else "var(--pos)"
        yy = y(lv["price"])
        body.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{col}" stroke-dasharray="6 4" opacity=".85"/>'
                    f'<text x="{W - PAD_R + 4}" y="{yy - 3:.1f}" fill="{col}">{escape(lv["label"])} {_fmt_price(lv["price"])}</text>')
    vmax = max((b["v"] or 0) for b in bars) or 1
    for b, x in zip(bars, xs):                                # volume
        h = (b["v"] or 0) / vmax * (vol_bottom - vol_top)
        up = b["c"] >= (b["o"] if b["o"] else b["c"])
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{vol_bottom - h:.1f}" width="{cw:.1f}" height="{h:.1f}" '
                    f'fill="{"var(--pos)" if up else "var(--neg)"}" opacity=".45"/>')
    body.append(f'<text x="{W - PAD_R + 4}" y="{vol_bottom - 2}" fill="var(--muted)">Volume</text>')
    if tag_counts:                                            # patterns flagged per day
        cmax = max(tag_counts.values()) or 1
        body.append(f'<text x="{W - PAD_R + 4}" y="{strip_bottom - 2}" fill="var(--muted)">Patterns</text>')
        for d, x in zip(days, xs):
            c = tag_counts.get(d, 0)
            if c:
                h = c / cmax * (strip_bottom - strip_top)
                body.append(f'<rect x="{x - cw / 2:.1f}" y="{strip_bottom - h:.1f}" width="{cw:.1f}" height="{h:.1f}" fill="var(--fg)" opacity=".55">'
                            f'<title>{d.isoformat()}: {c} pattern{"s" if c != 1 else ""} flagged</title></rect>')
    body.append(_axis_x(days, xs, top, strip_bottom if tag_counts else vol_bottom))
    last = bars[-1]
    desc = (f"Daily candles for the last {n} sessions, from {days[0].isoformat()} to {days[-1].isoformat()}. "
            f"Range {_fmt_price(min(b['l'] for b in bars))} to {_fmt_price(max(b['h'] for b in bars))}; last close {_fmt_price(last['c'])}.")
    return _svg("price", height if tag_counts else 262, "Price candles with volume", desc, "".join(body))


def flow_bars(days: list[date], values: list[float | None], *, height: int = 170) -> str | None:
    """Daily net foreign flow (rupiah) as bars above/below a zero line, plus the cumulative line."""
    pts = [(d, v) for d, v in zip(days, values) if v is not None]
    if len(pts) < 5:
        return None
    ds = [d for d, _ in pts]
    vs = [v for _, v in pts]
    xs, step = _x_positions(len(pts))
    cum, run = [], 0.0
    for v in vs:
        run += v
        cum.append(run)
    top, bottom = PAD_T, height - PAD_B
    amp = max(max(abs(v) for v in vs), 1.0)
    y = _scale(-amp, amp, top, bottom)
    ycum_lo, ycum_hi = min(cum + [0.0]), max(cum + [0.0])
    ycum = _scale(ycum_lo, ycum_hi, top, bottom)
    cw = max(1.5, min(9.0, step * 0.62))
    body = [f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--muted)"/>',
            f'<text x="{W - PAD_R + 4}" y="{y(amp) + 8:.1f}" fill="var(--muted)">+{_fmt_b(amp)}</text>',
            f'<text x="{W - PAD_R + 4}" y="{y(-amp):.1f}" fill="var(--muted)">-{_fmt_b(amp)}</text>',
            f'<text x="{W - PAD_R + 4}" y="{y(0) + 4:.1f}" fill="var(--muted)">0</text>']
    for d, v, x in zip(ds, vs, xs):
        col = "var(--pos)" if v >= 0 else "var(--neg)"
        y0, y1 = sorted((y(0), y(v)))
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(0.8, y1 - y0):.1f}" fill="{col}" opacity=".8">'
                    f'<title>{d.isoformat()}: {"+" if v >= 0 else "-"}{_fmt_b(abs(v))}</title></rect>')
    line = " ".join(f"{x:.1f},{ycum(c):.1f}" for x, c in zip(xs, cum))
    body.append(f'<polyline points="{line}" fill="none" stroke="var(--fg)" stroke-width="1.5" opacity=".8"><title>Cumulative net foreign flow: {"+" if cum[-1] >= 0 else "-"}{_fmt_b(abs(cum[-1]))}</title></polyline>')
    body.append(_axis_x(ds, xs, top, bottom))
    desc = (f"Net foreign flow per day over {len(pts)} sessions, {ds[0].isoformat()} to {ds[-1].isoformat()}. "
            f"Cumulative {'+' if cum[-1] >= 0 else '-'}{_fmt_b(abs(cum[-1]))}.")
    return _svg("flow", height, "Daily net foreign flow", desc, "".join(body))


def bounds_band(days: list[date], lo_pct: list[float | None], hi_pct: list[float | None], *, height: int = 150) -> str | None:
    """Foreign-broker net flow as a certain range per day (percent of traded value). A narrow bar = a precise answer."""
    pts = [(d, a, b) for d, a, b in zip(days, lo_pct, hi_pct) if a is not None and b is not None]
    if len(pts) < 3:
        return None
    ds = [p[0] for p in pts]
    xs, step = _x_positions(len(pts))
    extremes = sorted(max(abs(p[1]), abs(p[2])) for p in pts)
    # Autoscale to the typical day, not the single biggest one: a couple of rebalancing/crossing days at ±50% would
    # flatten every other bar. Bars beyond the scale are clipped, marked with a triangle, and keep their real values in the tooltip.
    amp = max(extremes[min(len(extremes) - 1, int(len(extremes) * 0.9))] * 1.15, 0.01)
    amp = min(amp, extremes[-1]) if extremes[-1] > 0.01 else amp
    top, bottom = PAD_T, height - PAD_B
    y = _scale(-amp, amp, top, bottom)
    cw = max(2.0, min(10.0, step * 0.62))
    body = [f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--muted)"/>',
            f'<text x="{W - PAD_R + 4}" y="{y(amp) + 8:.1f}" fill="var(--muted)">+{amp * 100:.0f}%</text>',
            f'<text x="{W - PAD_R + 4}" y="{y(-amp):.1f}" fill="var(--muted)">-{amp * 100:.0f}%</text>',
            f'<text x="{W - PAD_R + 4}" y="{y(0) + 4:.1f}" fill="var(--muted)">0</text>']
    clipped = 0
    for (d, a, b), x in zip(pts, xs):
        ca, cb = max(-amp, min(amp, a)), max(-amp, min(amp, b))
        y0, y1 = y(cb), y(ca)
        col = "var(--pos)" if a > 0 else ("var(--neg)" if b < 0 else "var(--muted)")
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(2.0, y1 - y0):.1f}" fill="{col}" opacity=".75" rx="1">'
                    f'<title>{d.isoformat()}: between {a * 100:+.1f}% and {b * 100:+.1f}% of traded value</title></rect>')
        if cb != b or ca != a:                                  # beyond the scale: arrow at the clipped end
            clipped += 1
            up = cb != b
            ty = y0 - 1 if up else y1 + 1
            body.append(f'<path d="M{x - 3:.1f},{ty + (0 if up else 0):.1f} l3,{-5 if up else 5} l3,{5 if up else -5} z" fill="{col}"/>')
    if clipped:
        body.append(f'<text x="{PAD_L}" y="{top + 8}" fill="var(--muted)">{clipped} day{"s" if clipped != 1 else ""} beyond the scale (arrow); hover for the real range</text>')
    body.append(_axis_x(ds, xs, top, bottom))
    desc = (f"Certain range of foreign-broker net flow as a share of traded value, {len(pts)} sessions from {ds[0].isoformat()} to "
            f"{ds[-1].isoformat()}. A bar entirely above zero means net buying is certain; entirely below, net selling; across zero, undetermined.")
    return _svg("band", height, "Foreign brokers: certain range of net flow", desc, "".join(body))


def count_bars(days: list[date], counts: list[int], *, height: int = 140, label: str = "stocks flagged") -> str | None:
    """How many stocks a pattern flagged on each day (zero days are drawn as empty, not skipped)."""
    if len(days) < 5 or not any(counts):
        return None
    xs, step = _x_positions(len(days))
    top, bottom = PAD_T, height - PAD_B
    cmax = max(counts)
    y = _scale(0, cmax, top, bottom)
    cw = max(1.5, min(9.0, step * 0.62))
    body = [f'<text x="{W - PAD_R + 4}" y="{y(cmax) + 8:.1f}" fill="var(--muted)">{cmax:,}</text>',
            f'<text x="{W - PAD_R + 4}" y="{bottom:.1f}" fill="var(--muted)">0</text>']
    for d, c, x in zip(days, counts, xs):
        h = bottom - y(c)
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{y(c):.1f}" width="{cw:.1f}" height="{max(0.0, h):.1f}" fill="var(--fg)" opacity=".6">'
                    f'<title>{d.isoformat()}: {c:,} {escape(label)}</title></rect>')
    body.append(_axis_x(days, xs, top, bottom))
    desc = (f"Number of {label} on each of the last {len(days)} sessions, from {days[0].isoformat()} to {days[-1].isoformat()}. "
            f"Highest day {cmax:,}.")
    return _svg("count", height, f"Daily number of {label}", desc, "".join(body))
