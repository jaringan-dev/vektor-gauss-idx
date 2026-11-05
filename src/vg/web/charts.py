"""Server-side SVG charts for the stock page (RENCANA-ROMBAK-UI-v3 stage E) — no JavaScript, no chart library, no CDN.

Pure functions: plain numbers in, an SVG string out (or None when there is nothing honest to draw). Colours are CSS
variables (`--pos`, `--neg`, `--fg`, `--muted`, `--border`), so charts follow the light/dark theme. Every chart carries a
<title>/<desc> for screen readers and a native hover tooltip (<title>) on each data point; direction is never colour-only
(candles also differ in body fill, flow bars sit above/below a zero line). Text is escaped.
"""

from __future__ import annotations

from datetime import date
from html import escape

W = 720                      # default (desktop) viewBox width; a narrower variant is rendered for phones
W_MOBILE = 380
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


def _x_positions(n: int, width: int = W) -> tuple[list[float], float]:
    inner = width - PAD_L - PAD_R
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


def _svg(uid: str, height: int, title: str, desc: str, body: str, width: int = W) -> str:
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-labelledby="{uid}-t {uid}-d" '
            f'style="max-width:{width}px;display:block;font:11px -apple-system,Segoe UI,Roboto,sans-serif">'
            f'<title id="{uid}-t">{escape(title)}</title><desc id="{uid}-d">{escape(desc)}</desc>{body}</svg>')


def _axis_x(days, xs, y_top, y_bottom, width: int = W) -> str:
    out = [f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y_bottom}" y2="{y_bottom}" stroke="var(--border)"/>']
    for x, label in _month_ticks(days, xs):
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y_top}" y2="{y_bottom}" stroke="var(--border)" stroke-dasharray="2 4"/>'
                   f'<text x="{x:.1f}" y="{y_bottom + 14}" text-anchor="middle" fill="var(--muted)">{escape(label)}</text>')
    return "".join(out)


def candles(bars: list[dict], *, levels: list[dict] | None = None, tag_counts: dict | None = None,
            height: int = 300, width: int = W, uid: str = "price") -> str | None:
    """bars: [{d: date, o, h, l, c, v}] oldest first. levels: [{price, label, kind: 'stop'|'target'}] drawn as dashed lines.
    tag_counts: {date: n patterns flagged} drawn as a strip under the volume bars."""
    bars = [b for b in bars if b["h"] and b["l"]]
    if len(bars) < 5:
        return None
    n = len(bars)
    xs, step = _x_positions(n, width)
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
        body.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--border)" stroke-dasharray="2 4"/>')
        if all(abs(y(v) - ly) > 12 for ly in level_ys):       # a level label wins over a plain gridline label
            body.append(f'<text x="{width - PAD_R + 4}" y="{y(v) + 3:.1f}" fill="var(--muted)">{_fmt_price(v)}</text>')
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
        body.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{col}" stroke-dasharray="6 4" opacity=".85"/>'
                    f'<text x="{width - PAD_R + 4}" y="{yy - 3:.1f}" fill="{col}">{escape(lv["label"])} {_fmt_price(lv["price"])}</text>')
    vmax = max((b["v"] or 0) for b in bars) or 1
    for b, x in zip(bars, xs):                                # volume
        h = (b["v"] or 0) / vmax * (vol_bottom - vol_top)
        up = b["c"] >= (b["o"] if b["o"] else b["c"])
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{vol_bottom - h:.1f}" width="{cw:.1f}" height="{h:.1f}" '
                    f'fill="{"var(--pos)" if up else "var(--neg)"}" opacity=".45"/>')
    body.append(f'<text x="{width - PAD_R + 4}" y="{vol_bottom - 2}" fill="var(--muted)">Volume</text>')
    if tag_counts:                                            # patterns flagged per day
        cmax = max(tag_counts.values()) or 1
        body.append(f'<text x="{width - PAD_R + 4}" y="{strip_bottom - 2}" fill="var(--muted)">Patterns</text>')
        for d, x in zip(days, xs):
            c = tag_counts.get(d, 0)
            if c:
                h = c / cmax * (strip_bottom - strip_top)
                body.append(f'<rect x="{x - cw / 2:.1f}" y="{strip_bottom - h:.1f}" width="{cw:.1f}" height="{h:.1f}" fill="var(--fg)" opacity=".55">'
                            f'<title>{d.isoformat()}: {c} pattern{"s" if c != 1 else ""} flagged</title></rect>')
    body.append(_axis_x(days, xs, top, strip_bottom if tag_counts else vol_bottom, width))
    last = bars[-1]
    desc = (f"Daily candles for the last {n} sessions, from {days[0].isoformat()} to {days[-1].isoformat()}. "
            f"Range {_fmt_price(min(b['l'] for b in bars))} to {_fmt_price(max(b['h'] for b in bars))}; last close {_fmt_price(last['c'])}.")
    return _svg(uid, height if tag_counts else 262, "Price candles with volume", desc, "".join(body), width)


def flow_bars(days: list[date], values: list[float | None], *, height: int = 170, width: int = W, uid: str = "flow") -> str | None:
    """Daily net foreign flow (rupiah) as bars above/below a zero line, plus the cumulative line."""
    pts = [(d, v) for d, v in zip(days, values) if v is not None]
    if len(pts) < 5:
        return None
    ds = [d for d, _ in pts]
    vs = [v for _, v in pts]
    xs, step = _x_positions(len(pts), width)
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
    body = [f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--muted)"/>',
            f'<text x="{width - PAD_R + 4}" y="{y(amp) + 8:.1f}" fill="var(--muted)">+{_fmt_b(amp)}</text>',
            f'<text x="{width - PAD_R + 4}" y="{y(-amp):.1f}" fill="var(--muted)">-{_fmt_b(amp)}</text>',
            f'<text x="{width - PAD_R + 4}" y="{y(0) + 4:.1f}" fill="var(--muted)">0</text>']
    for d, v, x in zip(ds, vs, xs):
        col = "var(--pos)" if v >= 0 else "var(--neg)"
        y0, y1 = sorted((y(0), y(v)))
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(0.8, y1 - y0):.1f}" fill="{col}" opacity=".8">'
                    f'<title>{d.isoformat()}: {"+" if v >= 0 else "-"}{_fmt_b(abs(v))}</title></rect>')
    line = " ".join(f"{x:.1f},{ycum(c):.1f}" for x, c in zip(xs, cum))
    body.append(f'<polyline points="{line}" fill="none" stroke="var(--fg)" stroke-width="1.5" opacity=".8"><title>Cumulative net foreign flow: {"+" if cum[-1] >= 0 else "-"}{_fmt_b(abs(cum[-1]))}</title></polyline>')
    body.append(_axis_x(ds, xs, top, bottom, width))
    desc = (f"Net foreign flow per day over {len(pts)} sessions, {ds[0].isoformat()} to {ds[-1].isoformat()}. "
            f"Cumulative {'+' if cum[-1] >= 0 else '-'}{_fmt_b(abs(cum[-1]))}.")
    return _svg(uid, height, "Daily net foreign flow", desc, "".join(body), width)


def bounds_band(days: list[date], lo_pct: list[float | None], hi_pct: list[float | None], *, height: int = 150,
                width: int = W, uid: str = "band") -> str | None:
    """Foreign-broker net flow as a certain range per day (percent of traded value). A narrow bar = a precise answer."""
    pts = [(d, a, b) for d, a, b in zip(days, lo_pct, hi_pct) if a is not None and b is not None]
    if len(pts) < 3:
        return None
    ds = [p[0] for p in pts]
    xs, step = _x_positions(len(pts), width)
    extremes = sorted(max(abs(p[1]), abs(p[2])) for p in pts)
    # Autoscale to the typical day, not the single biggest one: a couple of rebalancing/crossing days at ±50% would
    # flatten every other bar. Bars beyond the scale are clipped, marked with a triangle, and keep their real values in the tooltip.
    amp = max(extremes[min(len(extremes) - 1, int(len(extremes) * 0.9))] * 1.15, 0.01)
    amp = min(amp, extremes[-1]) if extremes[-1] > 0.01 else amp
    top, bottom = PAD_T, height - PAD_B
    y = _scale(-amp, amp, top, bottom)
    cw = max(2.0, min(10.0, step * 0.62))
    body = [f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--muted)"/>',
            f'<text x="{width - PAD_R + 4}" y="{y(amp) + 8:.1f}" fill="var(--muted)">+{amp * 100:.0f}%</text>',
            f'<text x="{width - PAD_R + 4}" y="{y(-amp):.1f}" fill="var(--muted)">-{amp * 100:.0f}%</text>',
            f'<text x="{width - PAD_R + 4}" y="{y(0) + 4:.1f}" fill="var(--muted)">0</text>']
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
    body.append(_axis_x(ds, xs, top, bottom, width))
    desc = (f"Certain range of foreign-broker net flow as a share of traded value, {len(pts)} sessions from {ds[0].isoformat()} to "
            f"{ds[-1].isoformat()}. A bar entirely above zero means net buying is certain; entirely below, net selling; across zero, undetermined.")
    return _svg(uid, height, "Foreign brokers: certain range of net flow", desc, "".join(body), width)


def group_panels(days: list[date], net_panels: list[tuple[str, list[float | None], list[float | None]]],
                 share: tuple[str, list[float | None]] | None = None, *, width: int = W, uid: str = "groups") -> str | None:
    """Stacked small-multiples sharing one time axis. Each net panel is (label, lo_pct, hi_pct): the certain range of that
    group's net flow per day as a share of traded value (same scale in every net panel, so they compare). The optional
    last panel is (label, floor_pct): bars from zero, a lower bound on a share. Days without data leave a gap."""
    have = [i for i in range(len(days)) if any(p[1][i] is not None and p[2][i] is not None for p in net_panels)]
    if len(have) < 3:
        return None
    xs, step = _x_positions(len(days), width)
    extremes = sorted(max(abs(p[1][i]), abs(p[2][i])) for p in net_panels for i in have if p[1][i] is not None and p[2][i] is not None)
    # Same autoscale rule as bounds_band: the typical day sets the scale; rare huge days are clipped and marked.
    amp = max(extremes[min(len(extremes) - 1, int(len(extremes) * 0.9))] * 1.15, 0.01)
    amp = min(amp, extremes[-1]) if extremes[-1] > 0.01 else amp
    ph, gap = 74, 18                                             # panel height, space between panels (holds the label)
    cw = max(2.0, min(10.0, step * 0.62))
    body: list[str] = []
    clipped = sum(1 for p in net_panels for i in have
                  if p[1][i] is not None and p[2][i] is not None and not (-amp <= p[1][i] <= amp and -amp <= p[2][i] <= amp))
    top0 = PAD_T + 10 + (14 if clipped else 0)                 # a clipped-bars note gets its own line above the first label
    for k, (label, lo, hi) in enumerate(net_panels):
        pt = top0 + k * (ph + gap)
        pb = pt + ph
        y = _scale(-amp, amp, pt, pb)
        body.append(f'<text x="{PAD_L}" y="{pt - 5:.1f}" fill="var(--fg)" font-weight="600">{escape(label)}</text>'
                    f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="var(--muted)"/>'
                    f'<text x="{width - PAD_R + 4}" y="{y(amp) + 8:.1f}" fill="var(--muted)">+{amp * 100:.0f}%</text>'
                    f'<text x="{width - PAD_R + 4}" y="{y(-amp):.1f}" fill="var(--muted)">-{amp * 100:.0f}%</text>'
                    f'<text x="{width - PAD_R + 4}" y="{y(0) + 4:.1f}" fill="var(--muted)">0</text>')
        for i in have:
            a, b = lo[i], hi[i]
            if a is None or b is None:
                continue
            ca, cb = max(-amp, min(amp, a)), max(-amp, min(amp, b))
            y0, y1 = y(cb), y(ca)
            col = "var(--pos)" if a > 0 else ("var(--neg)" if b < 0 else "var(--muted)")
            body.append(f'<rect x="{xs[i] - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(2.0, y1 - y0):.1f}" fill="{col}" opacity=".75" rx="1">'
                        f'<title>{days[i].isoformat()} · {escape(label)}: between {a * 100:+.1f}% and {b * 100:+.1f}% of traded value</title></rect>')
            if cb != b or ca != a:                              # beyond the scale: arrow at the clipped end
                up = cb != b
                ty = y0 - 1 if up else y1 + 1
                body.append(f'<path d="M{xs[i] - 3:.1f},{ty:.1f} l3,{-5 if up else 5} l3,{5 if up else -5} z" fill="{col}"/>')
    bottom = top0 + len(net_panels) * (ph + gap) - gap
    if share is not None:
        label, vals = share
        pt = top0 + len(net_panels) * (ph + gap)
        pb = pt + ph * 0.7
        vmax = max((v for v in vals if v is not None), default=0.0)
        smax = max(vmax * 1.15, 0.01)
        y = _scale(0.0, smax, pt, pb)
        body.append(f'<text x="{PAD_L}" y="{pt - 5:.1f}" fill="var(--fg)" font-weight="600">{escape(label)}</text>'
                    f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{pb:.1f}" y2="{pb:.1f}" stroke="var(--muted)"/>'
                    f'<text x="{width - PAD_R + 4}" y="{pt + 8:.1f}" fill="var(--muted)">{smax * 100:.0f}%</text>'
                    f'<text x="{width - PAD_R + 4}" y="{pb + 4:.1f}" fill="var(--muted)">0</text>')
        for i, v in enumerate(vals):
            if v is None:
                continue
            body.append(f'<rect x="{xs[i] - cw / 2:.1f}" y="{y(v):.1f}" width="{cw:.1f}" height="{max(1.0, pb - y(v)):.1f}" fill="var(--fg)" opacity=".4" rx="1">'
                        f'<title>{days[i].isoformat()} · at least {v * 100:.1f}% of traded value was both bought and sold by the same brokers</title></rect>')
        bottom = pb
    if clipped:
        body.append(f'<text x="{PAD_L}" y="{PAD_T + 4}" fill="var(--muted)">{clipped} bar{"s" if clipped != 1 else ""} beyond the scale (arrow); hover for the real range</text>')
    body.append(_axis_x(days, xs, top0, bottom, width))
    height = int(bottom + PAD_B)
    names = ", ".join(p[0] for p in net_panels) + (f", {share[0]}" if share else "")
    desc = (f"Net flow of broker groups per day, {len(have)} sessions with data from {days[have[0]].isoformat()} to {days[have[-1]].isoformat()}. "
            f"Panels: {names}. Net-flow bars show a range guaranteed to contain the real value, as a share of traded value; "
            f"a bar entirely above zero means net buying is certain, entirely below means net selling is certain, across zero means undetermined.")
    return _svg(uid, height, "Net flow by broker group per day", desc, "".join(body), width)


SPARK_MIN_MONTHS = 3


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def sparkline(points: list[tuple[date, int, float | None]], *, y_max: float, small_n: int, uid: str, months: int = 12,
              width: int = 120, height: int = 30) -> str | None:
    """Tiny month-by-month trend of a pattern's 5-day edge (checklist F5). `points` = [(month_start, n, edge)], oldest first.

    Honesty rules: every sparkline in one table shares the SAME vertical scale (`y_max`, ± around a dashed zero line), so a
    flat line really means a small edge and not a zoomed-in one; a month with fewer than `small_n` signals is a HOLLOW dot
    that is never joined by a line (a thin month is shown but not allowed to draw a trend); a calendar month with no data
    is a gap, not an interpolation. Returns None with fewer than SPARK_MIN_MONTHS usable months: two points are not a trend.
    Direction is position (above/below the zero line) plus the title text, never colour alone.
    """
    pts = [(m, n, e) for m, n, e in points if e is not None]
    if not pts or y_max <= 0:
        return None
    last = _month_index(pts[-1][0])
    pts = [p for p in pts if last - _month_index(p[0]) < months]
    if len(pts) < SPARK_MIN_MONTHS:
        return None
    first = _month_index(pts[0][0])
    span = max(last - first, 1)
    pad = 5
    top, bottom = pad, height - pad
    y = _scale(-y_max, y_max, top, bottom)
    xs = [pad + (_month_index(m) - first) / span * (width - 2 * pad) for m, _, _ in pts]
    ys = [y(max(-y_max, min(y_max, e))) for _, _, e in pts]
    zero = y(0)
    body = [f'<line x1="{pad}" x2="{width - pad}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="var(--border)" stroke-dasharray="3 3"/>']
    for i in range(1, len(pts)):
        solid = pts[i][1] >= small_n and pts[i - 1][1] >= small_n and _month_index(pts[i][0]) - _month_index(pts[i - 1][0]) == 1
        if solid:
            body.append(f'<line x1="{xs[i - 1]:.1f}" y1="{ys[i - 1]:.1f}" x2="{xs[i]:.1f}" y2="{ys[i]:.1f}" stroke="var(--fg)" stroke-width="1.5"/>')
    for (m, n, e), x, yy in zip(pts, xs, ys):
        fill = "var(--fg)" if n >= small_n else "var(--panel)"
        note = f" (n {n:,}, small sample)" if n < small_n else f" (n {n:,})"
        body.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="2.6" fill="{fill}" stroke="var(--fg)" stroke-width="1.2">'
                    f'<title>{m.strftime("%b %Y")}: {e * 100:+.2f}%{note}</title></circle>')
    desc = ("Edge after 5 days, month by month (above the dashed line means the pattern beat the market that month; hollow dots are months with "
            f"fewer than {small_n} occurrences; a month beyond the scale is drawn at the edge of the box, its real value is listed here). " + "; ".join(f"{m.strftime('%b %Y')} {e * 100:+.2f}% (n {n:,})" for m, n, e in pts) + ".")
    svg = _svg(uid, height, "Monthly trend of the 5-day edge", desc, "".join(body), width)
    # a percentage width collapses inside an auto-width table cell, so a sparkline gets its real pixel width
    return svg.replace('width="100%"', f'width="{width}"', 1)


SPARK_SCALE_PERCENTILE = 0.9


def spark_scale(series: list[list[tuple[date, int, float | None]]], *, small_n: int, months: int = 12) -> float:
    """One shared vertical half-range for a whole table of sparklines: the 90th percentile of |edge| over the well-sampled months in
    the window. A percentile, not the maximum: one rare pattern with a +9% month would otherwise flatten every other line to a ruler.
    Months beyond the scale are drawn at the edge of the box (their true value stays in the tooltip and the text), thin months
    (< `small_n`) never count, and the floor 0.2% stops an all-quiet table from being magnified into noise."""
    vals: list[float] = []
    for pts in series:
        usable = [p for p in pts if p[2] is not None]
        if not usable:
            continue
        last = _month_index(usable[-1][0])
        vals += [abs(e) for m, n, e in usable if n >= small_n and last - _month_index(m) < months]
    if not vals:
        return 0.002
    vals.sort()
    return max(vals[int(SPARK_SCALE_PERCENTILE * (len(vals) - 1) + 0.5)], 0.002)


def count_bars(days: list[date], counts: list[int], *, height: int = 140, label: str = "stocks flagged",
               width: int = W, uid: str = "count") -> str | None:
    """How many stocks a pattern flagged on each day (zero days are drawn as empty, not skipped)."""
    if len(days) < 5 or not any(counts):
        return None
    xs, step = _x_positions(len(days), width)
    top, bottom = PAD_T, height - PAD_B
    cmax = max(counts)
    y = _scale(0, cmax, top, bottom)
    cw = max(1.5, min(9.0, step * 0.62))
    body = [f'<text x="{width - PAD_R + 4}" y="{y(cmax) + 8:.1f}" fill="var(--muted)">{cmax:,}</text>',
            f'<text x="{width - PAD_R + 4}" y="{bottom:.1f}" fill="var(--muted)">0</text>']
    for d, c, x in zip(days, counts, xs):
        h = bottom - y(c)
        body.append(f'<rect x="{x - cw / 2:.1f}" y="{y(c):.1f}" width="{cw:.1f}" height="{max(0.0, h):.1f}" fill="var(--fg)" opacity=".6">'
                    f'<title>{d.isoformat()}: {c:,} {escape(label)}</title></rect>')
    body.append(_axis_x(days, xs, top, bottom, width))
    desc = (f"Number of {label} on each of the last {len(days)} sessions, from {days[0].isoformat()} to {days[-1].isoformat()}. "
            f"Highest day {cmax:,}.")
    return _svg(uid, height, f"Daily number of {label}", desc, "".join(body), width)


# ---- KSEI balance position (stacked ownership by holder type, month by month) ----------------------------------------------
# KSEI's nine holder categories x local/foreign. Mid-tone colours that read on both themes; a foreign slice reuses its category's
# hue at half strength. The slices are never identified by colour alone: every slice has a hover title and the page prints the
# latest figures in a table next to the chart.
OWNER_ORDER = ("ID", "CP", "MF", "SC", "IB", "IS", "PF", "FD", "OT")
OWNER_LABEL = {"ID": "Individual", "CP": "Corporate", "MF": "Mutual fund", "SC": "Securities company", "IB": "Bank", "IS": "Insurance",
               "PF": "Pension fund", "FD": "Foundation", "OT": "Other"}
OWNER_COLOR = {"ID": "#f4a259", "CP": "#e4572e", "MF": "#17bebb", "SC": "#e86a92", "IB": "#8f6bd9", "IS": "#3a6ea5",
               "PF": "#2fa36b", "FD": "#a3c16b", "OT": "#d98cf0"}
SHARES_PER_LOT = 100
# the three-group strip under the stack: bottom to top
GROUP_STRIP = (("local_ind", "Local individual", "#f4a259"), ("local_inst", "Local institution", "#4f7cff"), ("foreign", "Foreign", "#a66cff"))


def _fmt_lot(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{v / 1e6:,.0f}M"
    if v >= 1e3:
        return f"{v / 1e3:,.0f}K"
    return f"{v:,.0f}"


def _nice_ceil(v: float) -> float:
    """Round a maximum up to a clean number (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8 or 10 times a power of ten) that hugs it, so the bars fill the panel."""
    if v <= 0:
        return 1.0
    base = 10 ** (len(str(int(v))) - 1) if v >= 1 else 1.0
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= base * m:
            return base * m
    return base * 10


def group_shares(m: dict) -> dict[str, float]:
    """One month -> shares of the three strip groups (local individual, local institution = local total minus individual, foreign)."""
    loc, fgn = m.get("local") or {}, m.get("foreign") or {}
    loc_total = sum(v or 0 for k, v in loc.items() if k in OWNER_ORDER)
    return {"local_ind": loc.get("ID") or 0, "local_inst": max(loc_total - (loc.get("ID") or 0), 0),
            "foreign": sum(v or 0 for k, v in fgn.items() if k in OWNER_ORDER)}


def ownership_stack(months: list[dict], *, width: int = W, uid: str = "own", tips: bool = True) -> str | None:
    """months: [{d: date, local: {code: shares}, foreign: {code: shares}}], oldest first.

    Top panel: shares held per holder category, in lots, local slices at the bottom and foreign above them. Bottom strip: the share
    of local individuals / local institutions / foreign in the total. Returns None with fewer than 3 usable months.
    `tips=False` drops the per-slice hover text (a phone has no hover, and the page would carry it twice)."""
    months = [m for m in months if sum(group_shares(m).values()) > 0]
    if len(months) < 3:
        return None
    n = len(months)
    xs, step = _x_positions(n, width)
    cw = max(3.0, min(22.0, step * 0.72))
    top, stack_bottom = PAD_T, 176
    strip_top, strip_bottom = 196, 244
    totals = [sum(group_shares(m).values()) / SHARES_PER_LOT for m in months]
    ymax = _nice_ceil(max(totals))
    y = _scale(0, ymax, top, stack_bottom)
    body = [f'<text x="{width - PAD_R + 4}" y="{top + 8}" fill="var(--muted)">{_fmt_lot(ymax)} lot</text>',
            f'<text x="{width - PAD_R + 4}" y="{stack_bottom}" fill="var(--muted)">0</text>']
    for i in (1, 2, 3):                                           # light gridlines at quarters of the scale
        gy = y(ymax * i / 4)
        body.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="var(--border)" stroke-dasharray="2 4"/>')
    for m, x, tot in zip(months, xs, totals):
        label = m["d"].strftime("%b %Y")
        run = 0.0
        for side, name in (("local", "Local"), ("foreign", "Foreign")):
            for code in OWNER_ORDER:
                v = ((m.get(side) or {}).get(code) or 0) / SHARES_PER_LOT
                if v <= 0:
                    continue
                y0, y1 = y(run + v), y(run)
                run += v
                op = "1" if side == "local" else ".5"
                tip = f"<title>{label} · {name} {OWNER_LABEL[code].lower()}: {_fmt_lot(v)} lot ({v / tot * 100:.1f}% of all)</title>" if tips else ""
                body.append(f'<rect x="{x - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(0.4, y1 - y0):.1f}" fill="{OWNER_COLOR[code]}" '
                            f'opacity="{op}">{tip}</rect>')
    body.append(f'<line x1="{PAD_L}" x2="{width - PAD_R}" y1="{stack_bottom}" y2="{stack_bottom}" stroke="var(--border)"/>')
    ys = _scale(0, 1, strip_top, strip_bottom)
    body.append(f'<text x="{width - PAD_R + 4}" y="{strip_top + 8}" fill="var(--muted)">100%</text>'
                f'<text x="{width - PAD_R + 4}" y="{strip_bottom}" fill="var(--muted)">0</text>')
    for m, x in zip(months, xs):
        g = group_shares(m)
        tot = sum(g.values())
        run = 0.0
        for key, name, col in GROUP_STRIP:
            share = g[key] / tot
            y0, y1 = ys(run + share), ys(run)
            run += share
            tip = f'<title>{m["d"].strftime("%b %Y")} · {name}: {share * 100:.1f}% of all shares</title>' if tips else ""
            body.append(f'<rect x="{x - cw / 2:.1f}" y="{y0:.1f}" width="{cw:.1f}" height="{max(0.4, y1 - y0):.1f}" fill="{col}">{tip}</rect>')
    every = 1 if step >= 24 else 3                                # phone width: a label every third month
    for i, (m, x) in enumerate(zip(months, xs)):
        if i % every == 0:
            lab = m["d"].strftime("%b %y" if m["d"].month == 1 else "%b")           # the year only on January: a year on the first label would run into the next one
            body.append(f'<text x="{x:.1f}" y="{strip_bottom + 14}" text-anchor="middle" fill="var(--muted)">{escape(lab)}</text>')
    last, g = months[-1], group_shares(months[-1])
    tot = sum(g.values())
    desc = (f"Shares held by holder type over {n} month-ends, {months[0]['d'].strftime('%b %Y')} to {last['d'].strftime('%b %Y')}, "
            f"with the share of local individuals, local institutions and foreigners underneath. Latest month: foreign {g['foreign'] / tot * 100:.1f}%, "
            f"local individual {g['local_ind'] / tot * 100:.1f}%, local institution {g['local_inst'] / tot * 100:.1f}%. "
            "Every slice has the exact figure in its hover text, and the table below the chart lists the latest month.")
    return _svg(uid, strip_bottom + PAD_B, "KSEI balance position by holder type", desc, "".join(body), width)
