"""Top movers (`/movers`): the five daily market rankings people reach for first - top gainer, top loser, top value, top volume,
top frequency - ranked from the stored end-of-day stock summary (`price_bars`).

It is end of day, not live: the page prints the session it ranks. Nothing here is a signal, a pattern or a score; it is a plain
sort of the day's numbers, with two switches that stop one odd stock from filling the list:

  * Liquid only   - keep only stocks that traded more than Rp5 billion that day (the same line the evaluation uses), so a
                    Rp50 stock that moved 30% on a handful of trades does not top the gainers.
  * Regular board - subtract the negotiated / cash board (`nonreg_*`) from value, volume and frequency, so one block deal does
                    not make a stock the "top value" of the day. A missing negotiated figure counts as zero.

`load()` is the only part that touches the database (psycopg, SELECT only); everything else is pure and unit-tested.
"""

from __future__ import annotations

from datetime import date

from ..db import connect
from . import fmt

# (key, label, what it ranks) - the order of the pills.
BOARDS = (
    ("gainer", "Top gainer", "biggest rise against the previous close"),
    ("loser", "Top loser", "biggest fall against the previous close"),
    ("value", "Top value", "most rupiah traded"),
    ("volume", "Top volume", "most shares (lots) traded"),
    ("freq", "Top frequency", "most trades done"),
)
BOARD_KEYS = tuple(k for k, _l, _n in BOARDS)
DEFAULT_BOARD = "gainer"
LIMIT = 20                       # rows in the table
PREVIEW = 3                      # leaders shown per board in the summary strip
RECENT_SESSIONS = 30             # sessions offered in the date picker
LIQUID_VALUE_IDR = 5_000_000_000  # same line as engine.LIQUID_VALUE_IDR (a test guards it)

_NUMERIC = ("close", "prev", "volume", "value", "frequency", "nonreg_volume", "nonreg_value", "nonreg_frequency")


def _f(v) -> float | None:
    return None if v is None else float(v)


def load(want: date | None = None) -> dict:
    """The sessions that have price bars (newest first), the session to rank (`want` if it is one of them, else the newest) and its rows."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.session_date FROM trading_calendar c "
            "WHERE EXISTS (SELECT 1 FROM price_bars p WHERE p.session_date = c.session_date) "
            "ORDER BY c.session_date DESC LIMIT %s",
            (RECENT_SESSIONS,),
        )
        days = [r[0] for r in cur.fetchall()]
        session = want if want in days else (days[0] if days else None)
        rows: list[dict] = []
        if session is not None:
            cur.execute(
                "SELECT ticker, close, prev, volume, value, frequency, nonreg_volume, nonreg_value, nonreg_frequency "
                "FROM price_bars WHERE session_date = %s AND close IS NOT NULL",
                (session,),
            )
            cols = [d[0] for d in cur.description]
            rows = [{c: (v if c == "ticker" else _f(v)) for c, v in zip(cols, r, strict=True)} for r in cur.fetchall()]
    return {"session_date": session, "days": days, "wanted": want, "rows": rows}


def change(row: dict) -> float | None:
    """Fraction the close moved against the previous close; None when either is missing or the previous close is not positive."""
    close, prev = row.get("close"), row.get("prev")
    return (close / prev - 1) if close is not None and prev else None


def _traded(row: dict, regular: bool) -> dict:
    """Value, volume and frequency of the row, all boards or the regular board only (never below zero)."""
    out = {}
    for key, nonreg in (("value", "nonreg_value"), ("volume", "nonreg_volume"), ("frequency", "nonreg_frequency")):
        v = row.get(key)
        if v is not None and regular:
            v = max(v - (row.get(nonreg) or 0.0), 0.0)
        out[key] = v
    return out


def _metric(board: str, row: dict, traded: dict) -> float | None:
    if board in ("gainer", "loser"):
        return change(row)
    return traded[{"value": "value", "volume": "volume", "freq": "frequency"}[board]]


def rank(rows: list[dict], board: str, *, liquid: bool = False, regular: bool = False, limit: int = LIMIT) -> list[dict]:
    """The top `limit` rows of one board. Gainers must be up and losers down (a day with three risers lists three); the other
    boards need a positive figure. Ties break by ticker so the order never flips between loads."""
    if board not in BOARD_KEYS:
        board = DEFAULT_BOARD
    scored = []
    for r in rows:
        t = _traded(r, regular)
        if liquid and (t["value"] or 0.0) <= LIQUID_VALUE_IDR:
            continue
        m = _metric(board, r, t)
        if m is None or (board == "gainer" and m <= 0) or (board == "loser" and m >= 0) or (board in ("value", "volume", "freq") and m <= 0):
            continue
        scored.append((m, r, t))
    scored.sort(key=lambda s: (s[0] if board == "loser" else -s[0], s[1]["ticker"]))
    top = scored[:limit]
    lead = abs(top[0][0]) if top else 0.0
    out = []
    for i, (m, r, t) in enumerate(top, 1):
        ch = change(r)
        out.append({
            "rank": i, "ticker": r["ticker"], "price": fmt.price(r.get("close")), "chg": fmt.cell(ch, 2),
            "value": _money(t["value"]), "lots": _int(None if t["volume"] is None else t["volume"] / 100),
            "freq": _int(t["frequency"]), "metric": m,
            "bar": round(100 * abs(m) / lead) if lead else 0,                   # the leader fills the bar
        })
    return out


def _money(x: float | None) -> str:
    return "n/a" if x is None else fmt.idr(x).lstrip("+")


def _int(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def _href(board: str, liquid: bool, regular: bool, session: date | None, latest: date | None) -> str:
    q = [f"board={board}"]
    if liquid:
        q.append("liquid=1")
    if regular:
        q.append("reg=1")
    if session is not None and session != latest:
        q.append(f"date={session.isoformat()}")
    return "/movers?" + "&".join(q)


def build_view(raw: dict, board: str = DEFAULT_BOARD, *, liquid: bool = False, regular: bool = False) -> dict:
    """Everything the template needs: the pills, the summary strip (leaders of every board), the table of the chosen board."""
    board = board if board in BOARD_KEYS else DEFAULT_BOARD
    session = raw.get("session_date")
    days = raw.get("days") or []
    latest = days[0] if days else None
    rows = raw.get("rows") or []
    pills = [{"key": k, "label": label, "title": note, "on": k == board,
              "href": _href(k, liquid, regular, session, latest)} for k, label, note in BOARDS]
    toggles = [
        {"label": "Liquid only", "on": liquid, "title": "Only stocks that traded more than Rp5 billion that day",
         "href": _href(board, not liquid, regular, session, latest)},
        {"label": "Regular board only", "on": regular, "title": "Leave out the negotiated board from value, volume and frequency",
         "href": _href(board, liquid, not regular, session, latest)},
    ]
    summary = [{"key": k, "label": label, "on": k == board, "href": _href(k, liquid, regular, session, latest),
                "leaders": [{"ticker": r["ticker"], "chg": r["chg"], "value": r["value"], "lots": r["lots"], "freq": r["freq"]}
                            for r in rank(rows, k, liquid=liquid, regular=regular, limit=PREVIEW)]}
               for k, label, _n in BOARDS]
    label, note = next((lb, n) for k, lb, n in BOARDS if k == board)
    notice = None
    wanted = raw.get("wanted")
    if wanted is not None and wanted != session:
        notice = f"{wanted} is not one of the last {RECENT_SESSIONS} sessions with price data, so the newest session is shown."
    return {
        "session_date": session, "latest": latest, "is_latest": session == latest, "board": board, "board_label": label, "board_note": note,
        "pills": pills, "toggles": toggles, "summary": summary, "rows": rank(rows, board, liquid=liquid, regular=regular),
        "n_stocks": len(rows), "liquid": liquid, "regular": regular, "notice": notice,
        "days": [{"iso": d.isoformat(), "on": d == session} for d in days],
    }
