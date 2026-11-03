"""Number formatting shared by the dashboard pages and the plain-language summaries (English UI, comma thousands,
dot decimals). Money magnitudes use T / B / M (trillion / billion / million rupiah), which matches how Indonesian
market data is usually abbreviated (T / B / Jt)."""

from __future__ import annotations


def pct(x: float | None, dp: int = 2) -> str:
    """Signed percent: +1.23%."""
    return f"{x * 100:+.{dp}f}%" if x is not None else "n/a"


def pct_plain(x: float | None, dp: int = 1) -> str:
    """Unsigned percent: 12.3%."""
    return f"{x * 100:.{dp}f}%" if x is not None else "n/a"


def signcls(x: float | None) -> str:
    return "" if x is None else ("pos" if x >= 0 else "neg")


def cell(x: float | None, dp: int = 2) -> dict:
    """A numeric cell: formatted text + a sign class for green/red (never colour-only — a +/− sign is always printed)."""
    return {"t": pct(x, dp), "c": signcls(x)}


def hit(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "-"


def price(v) -> str:
    return f"{float(v):,.0f}" if v is not None else "?"


def idr(x: float | None) -> str:
    """Rupiah magnitude with sign: +486.6B, -24.4B, +45M."""
    if x is None:
        return "n/a"
    a = abs(x)
    if a >= 1e12:
        return f"{x / 1e12:+.1f}T"
    if a >= 1e9:
        return f"{x / 1e9:+.1f}B"
    if a >= 1e6:
        return f"{x / 1e6:+.0f}M"
    return f"{x:+.0f}"


def money_words(x: float | None) -> str:
    """Unsigned rupiah in words, for sentences: 'Rp24.4 billion'."""
    if x is None:
        return "an unknown amount"
    a = abs(x)
    if a >= 1e12:
        return f"Rp{a / 1e12:.1f} trillion"
    if a >= 1e9:
        return f"Rp{a / 1e9:.1f} billion"
    if a >= 1e6:
        return f"Rp{a / 1e6:.0f} million"
    return f"Rp{a:,.0f}"
