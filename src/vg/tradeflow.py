"""Recorder for the feed's running-trade WebSocket (`wss://.../ws/running-trade`): every match order the exchange reports, kept as
one row per stock per minute in `trade_flow_1m`.

Why record it ourselves: the feed keeps nothing. The stream is in memory on its side and a reconnect only replays the last 50
trades, so a day that is not recorded while it happens is gone. It costs no REST quota and has no rate limit, but the feed allows
ONE connection per account (a second one takes over the first, close code 4408), so exactly one recorder may run.

What a trade carries (documented fields): ticker `t`, action `c` ("buy" / "sell"), exchange time `a` (HH:MM:SS), price `p`, lots `l`
(1 lot = 100 shares), value `v` (rupiah), change percent `pc`, exchange sequence `tn` (a string that starts with the session date as
YYYYMMDD). It carries NO broker code, so it complements the broker table, it does not replace it. Whether `c` is the AGGRESSIVE side
(the feed says it applies the tick rule to the exchange order numbers, as `done-details` does) is documented but not yet verified here:
compare a day against `done-details` before any signal leans on it.

What is stored per (session, stock, minute): trades and lots and rupiah on each side, the biggest single trade, the price range. Raw
trades are not stored: a full day is well over a million of them, and every question asked of them so far fits in a minute bucket.

Design:
  * `parse_trade` / `MinuteBook` are pure: validate one message, drop a repeated `tn`, add into a bucket.
  * `Recorder` owns the flushing (every few seconds, additive upsert, a failed write keeps the buckets for the next try) and the
    connection loop (backoff on a dropped connection, stop on 4401 / 4403, patient on 4408).
  * The server drops the connection about every ten minutes (6 times in the first hour recorded, "no close frame"), and every second
    of being disconnected is ~78 trades. Two things keep that loss small: after a connection that had been healthy the recorder asks
    again AT ONCE (the growing waits are only for a feed that is really down), and the snapshot the server sends after every connect
    (the last 50 trades) is counted on a RECONNECT, de-duplicated by trade number against everything this process already counted.
    The snapshot of the FIRST connection of a run is not counted: after a restart it would repeat what the previous run's last flush
    stored, and `tn` is not strictly increasing, so there is no safe cut. The summary reports the seconds spent blind (drop to the first
    message of the next connection) and the trades the snapshots gave back, so the loss is a number, not a guess.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .config import CONFIG
from .db import connect

WIB = ZoneInfo("Asia/Jakarta")
PATH = "/ws/running-trade"
PHASE = "trade_record"

# Weekdays the exchange is closed in 2026: the 22 holidays of the IDX calendar (announcement Peng-00171/BEI.POP/09-2025: national
# holidays and joint leave per the three-minister decree, plus the exchange's own closed day on 31 December). Cross-checked on
# 2026-11-06 against our own `trading_calendar`: it has no session on exactly the 19 listed days up to 5 Nov, and 19 + the three
# December days = the 22 the exchange announced. A holiday the government adds later is not here: add it with VG_EXTRA_NON_TRADING_DAYS
# (comma-separated ISO dates). A year that is not listed here is treated as weekdays only (the recorder then just idles on a holiday).
NON_TRADING_DAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-01-01", "2026-01-16", "2026-02-16", "2026-02-17", "2026-03-18", "2026-03-19", "2026-03-20", "2026-03-23", "2026-03-24",
    "2026-04-03", "2026-05-01", "2026-05-14", "2026-05-15", "2026-05-27", "2026-05-28", "2026-06-01", "2026-06-16", "2026-08-17",
    "2026-08-25", "2026-12-24", "2026-12-25", "2026-12-31"))
CALENDAR_YEARS = frozenset({2026})


def non_trading_days() -> frozenset[date]:
    """The listed holidays plus VG_EXTRA_NON_TRADING_DAYS; an entry that is not an ISO date is ignored."""
    extra = set()
    for part in os.environ.get("VG_EXTRA_NON_TRADING_DAYS", "").split(","):
        with contextlib.suppress(ValueError):
            if part.strip():
                extra.add(date.fromisoformat(part.strip()))
    return NON_TRADING_DAYS | extra

OPEN_AT = time(8, 50)             # the recorder may start this early and waits for the market
CLOSE_AT = time(16, 5)            # pre-closing runs to 16:00; nothing is reported after that
FLUSH_SECONDS = 10.0
STALL_SECONDS = 90.0              # the server sends a top-5 about every 5 s: silence this long means a dead connection
SEEN_KEEP = 400_000               # trade numbers remembered for the duplicate check (a day's busiest hour is ~150k)
BACKOFFS = (2.0, 5.0, 10.0, 30.0)   # reconnect waits (seconds), the last one repeats
HEALTHY_SECONDS = 60.0            # a connection that stayed up this long starts the waits again; a shorter one (a feed that accepts
                                  # and drops at once) must NOT reset them or it would be asked again every 2 s for ever
TAKEOVER_WAIT = 60.0              # close 4408 = another client of this account took the connection: wait before asking again
TAKEOVER_TRIES = 5
FATAL_CLOSE = {4401: "the API key was refused (invalid, banned or expired)",
               4403: "the account is not a paid user, which this stream requires"}

_TICKER = re.compile(r"^[A-Z0-9]{4}$")
_CLOCK = re.compile(r"^(\d{2}):(\d{2}):(\d{2})$")


# ---- one trade --------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    session: date
    ticker: str
    side: str          # "buy" | "sell"
    minute: int        # minutes since 00:00 (exchange time)
    price: float
    lots: float
    value: float
    tn: str


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def parse_trade(d: Any, today: date) -> Trade | None:
    """One `data` object of a `trade` message, or None when it is not a usable trade (the recorder counts those, it never guesses)."""
    if not isinstance(d, dict):
        return None
    ticker, side, clock, tn = d.get("t"), d.get("c"), d.get("a"), d.get("tn")
    price, lots, value = _num(d.get("p")), _num(d.get("l")), _num(d.get("v"))
    if not (isinstance(ticker, str) and _TICKER.match(ticker.upper())) or side not in ("buy", "sell"):
        return None
    m = _CLOCK.match(clock) if isinstance(clock, str) else None
    if not m or not isinstance(tn, str) or not tn:
        return None
    hh, mm, ss = int(m[1]), int(m[2]), int(m[3])
    if hh > 23 or mm > 59 or ss > 59 or price is None or lots is None or value is None or price <= 0 or lots <= 0 or value < 0:
        return None
    session = today
    if re.fullmatch(r"\d{8}\d*", tn):
        try:
            session = date(int(tn[:4]), int(tn[4:6]), int(tn[6:8]))      # the sequence number starts with the session date
        except ValueError:
            session = today
    return Trade(session, ticker.upper(), side, hh * 60 + mm, price, lots, value, tn)


# ---- the minute buckets -----------------------------------------------------------------------------------------------------------

@dataclass
class Bucket:
    n_buy: int = 0
    n_sell: int = 0
    buy_lot: float = 0.0
    sell_lot: float = 0.0
    buy_val: float = 0.0
    sell_val: float = 0.0
    max_val: float = 0.0
    px_high: float = 0.0
    px_low: float = float("inf")

    def add(self, t: Trade) -> None:
        if t.side == "buy":
            self.n_buy, self.buy_lot, self.buy_val = self.n_buy + 1, self.buy_lot + t.lots, self.buy_val + t.value
        else:
            self.n_sell, self.sell_lot, self.sell_val = self.n_sell + 1, self.sell_lot + t.lots, self.sell_val + t.value
        self.max_val = max(self.max_val, t.value)
        self.px_high, self.px_low = max(self.px_high, t.price), min(self.px_low, t.price)


@dataclass
class Counts:
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    snapshots: int = 0


# Fills of at least this many lots are also counted per (stock, side, price, size) for the iceberg proxy (`trade_lots_daily`).
# Smaller fills are retail-sized and would only multiply the table.
LOT_MIN = 50


class MinuteBook:
    """Trades in, minute buckets out. A trade number seen before is dropped (the stream may repeat one after a reconnect).
    Fills of LOT_MIN+ lots are also counted in `lots` per (session, ticker, side, price, lots): [n, first minute, last minute]."""

    def __init__(self, today: date, seen_keep: int = SEEN_KEEP) -> None:
        self.today = today
        self.buckets: dict[tuple[date, str, int], Bucket] = {}
        self.lots: dict[tuple[date, str, str, float, float], list[int]] = {}
        self.counts = Counts()
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._keep = seen_keep

    def add(self, data: Any, *, replay: bool = False) -> bool:
        """Count one trade. `replay` = it comes from a snapshot, where most trades were counted before: a repeat is then expected and
        is not tallied as a duplicate (a malformed one is still a reject)."""
        t = parse_trade(data, self.today)
        if t is None:
            self.counts.rejected += 1
            return False
        if t.tn in self._seen:
            if not replay:
                self.counts.duplicates += 1
            return False
        self._seen.add(t.tn)
        self._order.append(t.tn)
        if len(self._order) > self._keep:
            self._seen.discard(self._order.popleft())
        self.buckets.setdefault((t.session, t.ticker, t.minute), Bucket()).add(t)
        if t.lots >= LOT_MIN:
            k = (t.session, t.ticker, "B" if t.side == "buy" else "S", t.price, t.lots)
            c = self.lots.get(k)
            if c is None:
                self.lots[k] = [1, t.minute, t.minute]
            else:
                c[0], c[1], c[2] = c[0] + 1, min(c[1], t.minute), max(c[2], t.minute)
        self.counts.accepted += 1
        return True

    def drain_lots(self) -> list[tuple]:
        """The size counts as database rows, and emptied. `restore_lots` puts them back when the write failed."""
        rows = [(*k, n, lo, hi) for k, (n, lo, hi) in self.lots.items()]
        self.lots = {}
        return rows

    def restore_lots(self, rows: list[tuple]) -> None:
        for s, tk, side, px, lots, n, lo, hi in rows:
            c = self.lots.setdefault((s, tk, side, px, lots), [0, lo, hi])
            c[0], c[1], c[2] = c[0] + n, min(c[1], lo), max(c[2], hi)

    def drain(self) -> list[tuple]:
        """Every bucket as a database row, and the book emptied. Call `restore` with the rows when writing them failed."""
        rows = [(s, tk, mn, b.n_buy, b.n_sell, b.buy_lot, b.sell_lot, b.buy_val, b.sell_val, b.max_val, b.px_high, b.px_low)
                for (s, tk, mn), b in self.buckets.items()]
        self.buckets = {}
        return rows

    def restore(self, rows: list[tuple]) -> None:
        """Put rows that could not be written back, merging with whatever arrived meanwhile (counts add, extremes combine)."""
        for s, tk, mn, nb, ns, bl, sl, bv, sv, mx, hi, lo in rows:
            b = self.buckets.setdefault((s, tk, mn), Bucket())
            b.n_buy, b.n_sell, b.buy_lot, b.sell_lot = b.n_buy + nb, b.n_sell + ns, b.buy_lot + bl, b.sell_lot + sl
            b.buy_val, b.sell_val, b.max_val = b.buy_val + bv, b.sell_val + sv, max(b.max_val, mx)
            b.px_high, b.px_low = max(b.px_high, hi), min(b.px_low, lo)


# ---- the database -----------------------------------------------------------------------------------------------------------------

_UPSERT = (
    "INSERT INTO trade_flow_1m (session_date, ticker, minute, n_buy, n_sell, buy_lot, sell_lot, buy_val, sell_val, max_val, px_high, px_low) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (session_date, ticker, minute) DO UPDATE SET "
    "n_buy = trade_flow_1m.n_buy + EXCLUDED.n_buy, n_sell = trade_flow_1m.n_sell + EXCLUDED.n_sell, "
    "buy_lot = trade_flow_1m.buy_lot + EXCLUDED.buy_lot, sell_lot = trade_flow_1m.sell_lot + EXCLUDED.sell_lot, "
    "buy_val = trade_flow_1m.buy_val + EXCLUDED.buy_val, sell_val = trade_flow_1m.sell_val + EXCLUDED.sell_val, "
    "max_val = GREATEST(trade_flow_1m.max_val, EXCLUDED.max_val), px_high = GREATEST(trade_flow_1m.px_high, EXCLUDED.px_high), "
    "px_low = LEAST(trade_flow_1m.px_low, EXCLUDED.px_low), updated_at = now()"
)


def write_rows(rows: list[tuple]) -> int:
    """Add the rows to `trade_flow_1m` (additive: every row is trades not stored before). One transaction."""
    if not rows:
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT, rows)
    return len(rows)


_UPSERT_LOTS = (
    "INSERT INTO trade_lots_daily (session_date, ticker, side, price, lots, n, first_minute, last_minute) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (session_date, ticker, side, price, lots) DO UPDATE SET n = trade_lots_daily.n + EXCLUDED.n, "
    "first_minute = LEAST(trade_lots_daily.first_minute, EXCLUDED.first_minute), "
    "last_minute = GREATEST(trade_lots_daily.last_minute, EXCLUDED.last_minute), updated_at = now()"
)


def write_lot_rows(rows: list[tuple]) -> int:
    """Add the size counts to `trade_lots_daily` (additive, like write_rows). One transaction."""
    if not rows:
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(_UPSERT_LOTS, rows)
    return len(rows)


def log_run(today: date, status: str, accepted: int, detail: str) -> None:
    """One `run_log` row per recorder run, so the system page can show that it ran and how it ended. Never raises."""
    with contextlib.suppress(Exception), connect() as conn, conn.cursor() as cur:       # a failed log line must not crash a good recording
        cur.execute("INSERT INTO run_log (phase, batch_date, status, rows_affected, error, finished_at) VALUES (%s, %s, %s, %s, %s, now())",
                    (PHASE, today, status, accepted, detail[:900]))


# ---- the recorder -----------------------------------------------------------------------------------------------------------------

def ws_url(base: str | None = None) -> str:
    base = (CONFIG.feed_base_url if base is None else base).rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + PATH
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + PATH
    raise ValueError(f"VG_FEED_BASE_URL must start with http(s)://, got {base!r}")


def close_code(exc: BaseException) -> int | None:
    """The WebSocket close code a `ConnectionClosed` carries (from the server's frame, else ours), None for any other error."""
    for frame in (getattr(exc, "rcvd", None), getattr(exc, "sent", None)):
        code = getattr(frame, "code", None)
        if isinstance(code, int):
            return code
    return None


@dataclass
class Stats:
    connections: int = 0
    flushes: int = 0
    rows_written: int = 0
    write_errors: int = 0
    lot_rows_written: int = 0
    lot_write_errors: int = 0
    last_error: str = ""
    gaps: int = 0               # reconnects after a drop
    blind_seconds: float = 0.0  # time from each drop to the first message of the next connection (an upper bound on what was missed)
    recovered: int = 0          # trades a reconnect's snapshot gave back that the dropped connection had not delivered
    stopped: str = ""           # why the run ended
    note: str = ""              # something worth saying about the run that is not an error (e.g. no holiday calendar for this year)


@dataclass
class Recorder:
    """Run one recording day. Everything that touches the world is injected, so the loop can be driven by a script in tests."""
    connect_ws: Any = None                       # () -> a context manager yielding an object with recv(timeout=...) ; default: the real feed
    writer: Any = write_rows
    # The iceberg-proxy size counts; a failure there never touches the minute rows. None = the real table only when `writer` is the real
    # one too, otherwise discard: a test or a dry run that injects its own writer must never write size counts into the live database.
    lot_writer: Any = None
    clock: Any = field(default=lambda: datetime.now(WIB))
    sleep: Any = _time.sleep
    mono: Any = _time.monotonic
    flush_seconds: float = FLUSH_SECONDS
    open_at: time = OPEN_AT
    close_at: time = CLOSE_AT
    max_seconds: float | None = None             # stop after this long (a smoke test); None = until close_at
    stall_seconds: float = STALL_SECONDS
    book: MinuteBook = None  # type: ignore[assignment]
    stats: Stats = field(default_factory=Stats)

    def __post_init__(self) -> None:
        self.today = self.clock().date()
        self.book = MinuteBook(self.today)
        if self.lot_writer is None:
            self.lot_writer = write_lot_rows if self.writer is write_rows else (lambda rows: 0)
        self._started = self.mono()
        self._last_flush = self._started
        self._last_msg = self._started
        self._dropped_at: float | None = None

    # -- messages --
    def handle(self, raw: str | bytes) -> None:
        """One frame from the server. Only `trade` frames are counted; the snapshot, top-5 and anything unknown are not."""
        self._last_msg = self.mono()
        if self._dropped_at is not None:             # first message after a drop: that is how long we were blind
            self.stats.gaps += 1
            self.stats.blind_seconds += max(self._last_msg - self._dropped_at, 0.0)
            self._dropped_at = None
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            self.book.counts.rejected += 1
            return
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "trade":
            self.book.add(msg.get("data"))
        elif kind == "snapshot":
            self.book.counts.snapshots += 1
            recent = msg.get("recent")
            if self.stats.connections > 1 and isinstance(recent, list):     # a reconnect: the seen-set makes this safe (see the docstring)
                before = self.book.counts.accepted
                for item in recent:
                    self.book.add(item, replay=True)
                self.stats.recovered += self.book.counts.accepted - before

    # -- writing --
    def flush(self, force: bool = False) -> None:
        if not force and self.mono() - self._last_flush < self.flush_seconds:
            return
        self._last_flush = self.mono()
        lot_rows = self.book.drain_lots()
        if lot_rows:
            try:
                self.stats.lot_rows_written += self.lot_writer(lot_rows)
            except Exception as exc:  # noqa: BLE001 - same rule as the minute rows: keep them for the next try
                self.book.restore_lots(lot_rows)
                self.stats.lot_write_errors += 1
                self.stats.last_error = f"lots: {type(exc).__name__}: {exc}"[:200]
        rows = self.book.drain()
        if not rows:
            return
        try:
            self.stats.rows_written += self.writer(rows)
            self.stats.flushes += 1
        except Exception as exc:  # noqa: BLE001 - keep the trades for the next try instead of losing a minute of the market
            self.book.restore(rows)
            self.stats.write_errors += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"[:200]

    # -- time --
    def _over(self) -> bool:
        if self.max_seconds is not None and self.mono() - self._started >= self.max_seconds:
            self.stats.stopped = self.stats.stopped or "max_seconds"
            return True
        if self.clock().time() >= self.close_at:
            self.stats.stopped = self.stats.stopped or "market_closed"
            return True
        return False

    def _wait_for_open(self) -> None:
        while self.max_seconds is None and self.clock().time() < self.open_at:
            self.sleep(30.0)

    # -- the loop --
    def run(self) -> int:
        """0 = recorded until the end of the day (or `max_seconds`); 2 = the feed refused us; 1 = gave up on the connection."""
        today = self.clock().date()
        if self.max_seconds is None:                 # a smoke test (max_seconds) may run on any day
            if today.weekday() >= 5:
                self.stats.stopped = "weekend"
                return 0
            if today in non_trading_days():
                self.stats.stopped = "exchange holiday"
                return 0
            if today.year not in CALENDAR_YEARS:
                self.stats.note = f"no holiday calendar loaded for {today.year}: weekday rule only"
        self._wait_for_open()
        attempt, takeovers = 0, 0
        try:
            while not self._over():
                opened = None
                try:
                    with self._open() as ws:
                        self.stats.connections += 1
                        opened = self._last_msg = self.mono()
                        while not self._over():
                            try:
                                raw = ws.recv(timeout=min(self.flush_seconds, 30.0))
                            except TimeoutError:
                                if self.mono() - self._last_msg > self.stall_seconds:
                                    raise ConnectionError(f"no message for {self.stall_seconds:.0f} s") from None
                                self.flush()
                                continue
                            self.handle(raw)
                            self.flush()
                except Exception as exc:  # noqa: BLE001 - every way a connection can end is handled here
                    code = close_code(exc)
                    self.stats.last_error = f"{type(exc).__name__}: {exc}"[:200]
                    self.flush(force=True)
                    healthy = opened is not None and self.mono() - opened >= HEALTHY_SECONDS
                    if healthy:
                        attempt = 0           # it had been up a while: this drop is news, not a flapping feed, so start the waits again
                    if opened is not None and self._dropped_at is None:
                        self._dropped_at = self.mono()                       # from here on we are blind until the next message
                    if code in FATAL_CLOSE:
                        self.stats.stopped = f"refused {code}: {FATAL_CLOSE[code]}"
                        return 2
                    if code == 4408:
                        takeovers += 1
                        if takeovers >= TAKEOVER_TRIES:
                            self.stats.stopped = "another client keeps taking the connection (4408)"
                            return 1
                        self.sleep(TAKEOVER_WAIT)
                        continue
                    if self._over():
                        break
                    if healthy:
                        continue              # a drop out of a healthy connection: ask again at once, every second is ~78 trades
                    self.sleep(BACKOFFS[min(attempt, len(BACKOFFS) - 1)])
                    attempt += 1
            return 0
        finally:
            self.flush(force=True)

    def _open(self):
        if self.connect_ws is not None:
            return self.connect_ws()
        from websockets.sync.client import connect as ws_connect

        if not CONFIG.feed_base_url or not CONFIG.feed_api_key:
            raise RuntimeError("VG_FEED_BASE_URL / VG_FEED_API_KEY not set - see .env")
        return ws_connect(ws_url(), additional_headers={"X-API-Key": CONFIG.feed_api_key}, open_timeout=15, close_timeout=5)

    def summary(self) -> str:
        c, s = self.book.counts, self.stats
        out = (f"trades {c.accepted:,} (duplicates {c.duplicates:,}, rejected {c.rejected:,}); connections {s.connections}; "
               f"blind {s.blind_seconds:.1f} s over {s.gaps} drops, {s.recovered} trades won back from snapshots; "
               f"rows written {s.rows_written:,} in {s.flushes} flushes; size-count rows {s.lot_rows_written:,}"
               + (f" ({s.lot_write_errors} failed writes)" if s.lot_write_errors else "")
               + f"; stopped: {s.stopped or 'end of run'}")
        return out + (f"; write errors {s.write_errors} (last: {s.last_error})" if s.write_errors else "") + \
            (f"; last connection error: {s.last_error}" if s.last_error and not s.write_errors else "") + \
            (f"; note: {s.note}" if s.note else "")


def record(*, max_seconds: float | None = None, flush_seconds: float = FLUSH_SECONDS, dry_run: bool = False,
           close_at: time = CLOSE_AT, open_at: time = OPEN_AT) -> int:
    """The `vg record-trades` command. Returns the exit code; prints one summary line and writes one `run_log` row (not on a dry run)."""
    rec = Recorder(writer=(lambda rows: 0) if dry_run else write_rows, flush_seconds=flush_seconds,
                   max_seconds=max_seconds, close_at=close_at, open_at=open_at)
    code = 2
    try:
        code = rec.run()
    except KeyboardInterrupt:
        rec.stats.stopped = "interrupted"
        code = 130
    finally:
        line = rec.summary()
        print(f"{rec.today} {line}")
        if not dry_run:
            log_run(rec.today, "ok" if code == 0 else "error", rec.book.counts.accepted, line)
    return code
