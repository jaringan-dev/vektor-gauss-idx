"""The user's own watchlist (`user_watchlist`), edited from the dashboard (/today).

It starts EMPTY and only the user fills it: nothing is copied in from configuration. `VG_WATCHLIST` in `.env` is a different thing; it names
the stocks the nightly collection pulls tick data for, and it no longer decides what the dashboard shows as "yours".

`parse`, `clean_list`, `messages` and `redirect_url` are pure. `load`, `add` and `remove` are the database parts: one short transaction each,
and a stock code is only accepted when it is a listed stock (`tickers`), so a typo never becomes a ghost entry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlencode

from ..db import connect

MAX_ITEMS = 100             # size of the list
MAX_PER_REQUEST = 30        # codes read from one form submission
_TICKER = re.compile(r"^[A-Z0-9]{4}$")
_SPLIT = re.compile(r"[\s,;]+")
ALLOWED_NEXT = ("/today", "/")


def parse(raw: str | None) -> tuple[list[str], list[str]]:
    """(codes that look like stock codes, things that do not) from free text. Commas, spaces, semicolons and new lines separate; case does
    not matter; a repeat is dropped; the order typed is kept."""
    seen: set[str] = set()
    valid: list[str] = []
    invalid: list[str] = []
    for part in _SPLIT.split((raw or "")[:2000]):
        t = part.strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        (valid if _TICKER.match(t) else invalid).append(t[:12])
    return valid[:MAX_PER_REQUEST], invalid[:10]


def clean_list(raw: str | None) -> list[str]:
    """Stock codes out of a query-string value (the result of an earlier add / remove): only well-formed codes survive."""
    return parse(raw)[0][:MAX_PER_REQUEST]


def safe_next(value: str | None) -> str:
    """Where to go back to after a change: only a page of ours that shows the watchlist, never an address the form supplied."""
    return value if value in ALLOWED_NEXT else "/today"


# ---- database ---------------------------------------------------------------------------------------------------------------------

def load() -> list[str]:
    """The list, oldest addition first."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM user_watchlist ORDER BY added_at, ticker")
        return [r[0] for r in cur.fetchall()]


def add(raw: str | None) -> dict:
    """Add the stocks typed in `raw`. Returns what happened to each code: added / exists / unknown (not a listed stock) / invalid (not a
    stock code at all) and `full` when the list hit MAX_ITEMS."""
    valid, invalid = parse(raw)
    out: dict = {"added": [], "exists": [], "unknown": [], "invalid": invalid, "full": False, "empty": not valid and not invalid}
    if not valid:
        return out
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM tickers WHERE ticker = ANY(%s)", (valid,))
        known = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT ticker FROM user_watchlist")
        have = {r[0] for r in cur.fetchall()}
        for t in valid:
            if t not in known:
                out["unknown"].append(t)
            elif t in have:
                out["exists"].append(t)
            elif len(have) + len(out["added"]) >= MAX_ITEMS:
                out["full"] = True
            else:
                out["added"].append(t)
        if out["added"]:
            cur.executemany("INSERT INTO user_watchlist (ticker) VALUES (%s) ON CONFLICT DO NOTHING", [(t,) for t in out["added"]])
    return out


def remove(raw: str | None) -> list[str]:
    """Take one stock off the list. Returns the codes that were really removed (empty when it was not on the list)."""
    valid, _bad = parse(raw)
    if not valid:
        return []
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_watchlist WHERE ticker = ANY(%s) RETURNING ticker", (valid[:1],))
        return [r[0] for r in cur.fetchall()]


# ---- the answer shown after a change ----------------------------------------------------------------------------------------------

def redirect_url(next_page: str, *, result: dict | None = None, removed: list[str] | None = None, error: bool = False) -> str:
    """The address to go back to, carrying what happened as short query values (`wl_added=BBCA,TLKM`) that `messages` turns into sentences."""
    q: dict[str, str] = {}
    if error:
        q["wl_err"] = "1"
    if removed is not None:
        q["wl_removed" if removed else "wl_notfound"] = ",".join(removed) or "1"
    if result:
        for key in ("added", "exists", "unknown", "invalid"):
            if result.get(key):
                q[f"wl_{key}"] = ",".join(result[key])
        if result.get("full"):
            q["wl_full"] = "1"
        if result.get("empty"):
            q["wl_empty"] = "1"
    return safe_next(next_page) + ("?" + urlencode(q) if q else "") + "#watchlist"


def messages(params: Mapping[str, str]) -> list[dict]:
    """Sentences for the panel from the query values `redirect_url` wrote. Only well-formed stock codes are echoed back, whatever the address says."""
    out: list[dict] = []

    def codes(key: str) -> str:
        return ", ".join(clean_list(params.get(key)))

    if params.get("wl_err"):
        out.append({"kind": "bad", "text": "Could not save the change: the database did not answer. Try again."})
    if codes("wl_added"):
        out.append({"kind": "ok", "text": f"Added to your watchlist: {codes('wl_added')}."})
    if codes("wl_removed"):
        out.append({"kind": "ok", "text": f"Removed from your watchlist: {codes('wl_removed')}."})
    if params.get("wl_notfound"):
        out.append({"kind": "warn", "text": "That stock was not on your watchlist."})
    if codes("wl_exists"):
        out.append({"kind": "warn", "text": f"Already on your watchlist: {codes('wl_exists')}."})
    if codes("wl_unknown"):
        out.append({"kind": "warn", "text": f"Not a listed stock, so not added: {codes('wl_unknown')}."})
    if params.get("wl_invalid"):
        out.append({"kind": "warn", "text": "That is not a stock code (a code is 4 letters or digits, like BBCA), so it was skipped."})
    if params.get("wl_full"):
        out.append({"kind": "warn", "text": f"Your watchlist is full ({MAX_ITEMS} stocks). Remove one to add another."})
    if params.get("wl_empty"):
        out.append({"kind": "warn", "text": "Type a stock code first, for example BBCA. Several can be separated by commas or spaces."})
    return out
