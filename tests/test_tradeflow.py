"""The running-trade recorder (src/vg/tradeflow.py): parse, bucket, drop repeats, write additively, survive a dropped connection.
No network and no database: the WebSocket and the clock are scripted."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from vg import tradeflow
from vg.tradeflow import WIB, MinuteBook, Recorder, parse_trade

ROOT = Path(__file__).resolve().parent.parent
FRI = date(2026, 11, 6)


def trade(t="BBCA", c="buy", a="09:15:32", p=10250, l=50, v=51_250_000, tn="202611060001048293", pc=1.48):
    return {"t": t, "c": c, "a": a, "p": p, "l": l, "v": v, "pc": pc, "tn": tn}


def msg(**kw):
    return json.dumps({"type": "trade", "data": trade(**kw)})


# ---- one trade --------------------------------------------------------------------------------------------------------------------

def test_a_good_trade_is_parsed_and_its_session_comes_from_the_sequence_number():
    t = parse_trade(trade(), date(2026, 1, 1))
    assert (t.ticker, t.side, t.minute, t.price, t.lots, t.value) == ("BBCA", "buy", 9 * 60 + 15, 10250.0, 50.0, 51_250_000.0)
    assert t.session == date(2026, 11, 6)                       # tn starts with YYYYMMDD: that wins over the clock's date
    assert parse_trade(trade(tn="1048293"), FRI).session == FRI     # a number without a date prefix: today
    assert parse_trade(trade(tn="20261399123"), FRI).session == FRI     # an impossible date in the prefix: today
    assert parse_trade(trade(t="bbca"), FRI).ticker == "BBCA"


@pytest.mark.parametrize("bad", [
    None, "x", [], {},
    trade(c="hold"), trade(c=None), trade(t="BBC"), trade(t="BBCAA"), trade(t="BB-A"), trade(t=None),
    trade(a="9:15:32"), trade(a="09:60:00"), trade(a="24:00:00"), trade(a=None),
    trade(p=0), trade(p=-1), trade(p=True), trade(p="10250"), trade(p=float("nan")), trade(p=float("inf")),
    trade(l=0), trade(l=-5), trade(l=None), trade(v=-1), trade(v=None), trade(tn=""), trade(tn=None), trade(tn=123),
])
def test_anything_that_is_not_a_usable_trade_is_rejected_not_guessed(bad):
    assert parse_trade(bad, FRI) is None


def test_a_zero_value_trade_is_kept():
    assert parse_trade(trade(v=0), FRI) is not None                # a price-only print is still a trade


# ---- the minute buckets -----------------------------------------------------------------------------------------------------------

def test_trades_of_one_minute_add_up_per_side_and_keep_the_biggest_and_the_price_range():
    book = MinuteBook(FRI)
    assert book.add(trade(c="buy", l=10, v=100, p=100, tn="20261106000000000001"))
    assert book.add(trade(c="buy", l=30, v=330, p=110, a="09:15:59", tn="20261106000000000002"))
    assert book.add(trade(c="sell", l=5, v=480, p=96, tn="20261106000000000003"))
    (key, b), = book.buckets.items()
    assert key == (date(2026, 11, 6), "BBCA", 555)
    assert (b.n_buy, b.n_sell, b.buy_lot, b.sell_lot, b.buy_val, b.sell_val) == (2, 1, 40.0, 5.0, 430.0, 480.0)
    assert (b.max_val, b.px_high, b.px_low) == (480.0, 110.0, 96.0)


def test_another_minute_or_stock_is_another_bucket():
    book = MinuteBook(FRI)
    for i, kw in enumerate([{}, {"a": "09:16:00"}, {"t": "BBRI"}, {"c": "sell"}]):
        book.add(trade(tn=f"2026110600000000{i:04d}", **kw))
    assert len(book.buckets) == 3 and book.buckets[(FRI, "BBCA", 555)].n_sell == 1


def test_a_repeated_trade_number_is_counted_once():
    book = MinuteBook(FRI)
    assert book.add(trade()) is True and book.add(trade()) is False
    assert (book.counts.accepted, book.counts.duplicates, book.counts.rejected) == (1, 1, 0)
    assert book.buckets[(FRI, "BBCA", 555)].n_buy == 1
    assert book.add({"nonsense": 1}) is False and book.counts.rejected == 1


def test_only_the_most_recent_numbers_are_remembered():
    book = MinuteBook(FRI, seen_keep=3)
    for i in range(5):
        book.add(trade(tn=f"20261106{i:010d}"))
    assert len(book._seen) == 3
    assert book.add(trade(tn="20261106" + "0000000004")) is False       # recent: still recognised
    assert book.add(trade(tn="20261106" + "0000000000")) is True         # evicted long ago: the known, documented limit


def test_draining_empties_the_book_and_restoring_merges_with_what_arrived_meanwhile():
    book = MinuteBook(FRI)
    book.add(trade(l=10, v=100, p=100, tn="20261106000000000001"))
    rows = book.drain()
    assert book.buckets == {} and len(rows) == 1 and rows[0][:3] == (date(2026, 11, 6), "BBCA", 555)
    book.add(trade(l=7, v=700, p=120, tn="20261106000000000002"))          # arrives while the write is failing
    book.restore(rows)
    b = book.buckets[(date(2026, 11, 6), "BBCA", 555)]
    assert (b.n_buy, b.buy_lot, b.buy_val, b.max_val, b.px_high, b.px_low) == (2, 17.0, 800.0, 700.0, 120.0, 100.0)


# ---- size counts for the iceberg proxy --------------------------------------------------------------------------------------------

def test_big_fills_are_counted_per_side_price_and_size_and_small_ones_are_not():
    book = MinuteBook(date(2026, 11, 6))
    book.add(trade(l=500, a="09:15:00", tn="20261106001"))
    book.add(trade(l=500, a="10:02:00", tn="20261106002"))                 # same side, price, size: one key, n=2, minutes span
    book.add(trade(l=500, c="sell", a="09:20:00", tn="20261106003"))       # other side: another key
    book.add(trade(l=10, a="09:16:00", tn="20261106004"))                  # retail size: minute bucket only
    book.add(trade(l=500, a="09:30:00", tn="20261106002"))                 # repeated trade number: not counted twice
    rows = sorted(book.drain_lots())
    assert rows == [(date(2026, 11, 6), "BBCA", "B", 10250.0, 500.0, 2, 555, 602),
                    (date(2026, 11, 6), "BBCA", "S", 10250.0, 500.0, 1, 560, 560)]
    assert book.lots == {}
    book.add(trade(l=500, a="11:00:00", tn="20261106005"))
    book.restore_lots(rows)                                                 # a failed write merges back with what came meanwhile
    k = (date(2026, 11, 6), "BBCA", "B", 10250.0, 500.0)
    assert book.lots[k] == [3, 555, 660]


def test_the_size_upsert_matches_its_table_and_adds():
    ddl = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE IF NOT EXISTS trade_lots_daily \((.*?)\n\);", ddl, re.DOTALL).group(1)
    cols = re.findall(r"^\s{4}([a-z_0-9]+)\s", table, re.MULTILINE)
    insert_cols = [c.strip() for c in re.search(r"INSERT INTO trade_lots_daily \((.*?)\) VALUES", tradeflow._UPSERT_LOTS).group(1).split(",")]
    assert insert_cols == cols[:len(insert_cols)] and tradeflow._UPSERT_LOTS.count("%s") == len(insert_cols) == 8
    assert "PRIMARY KEY (session_date, ticker, side, price, lots)" in table
    assert "n = trade_lots_daily.n + EXCLUDED.n" in tradeflow._UPSERT_LOTS


def test_an_injected_writer_never_writes_size_counts_to_the_real_table():
    rec = Recorder(writer=lambda rows: len(rows), clock=lambda: datetime(2026, 11, 6, 9, 0, tzinfo=WIB))
    assert rec.lot_writer is not tradeflow.write_lot_rows
    assert Recorder(clock=lambda: datetime(2026, 11, 6, 9, 0, tzinfo=WIB)).lot_writer is tradeflow.write_lot_rows


def test_a_failed_size_write_keeps_the_counts_and_the_minute_rows_still_go_out():
    wrote = []

    def bad_lots(rows):
        raise RuntimeError("disk full")

    rec = Recorder(writer=lambda rows: wrote.append(rows) or len(rows), lot_writer=bad_lots,
                   clock=lambda: datetime(2026, 11, 6, 9, 0, tzinfo=WIB))
    rec.handle(msg(l=500, tn="20261106001"))
    rec.flush(force=True)
    assert len(wrote) == 1 and rec.stats.lot_write_errors == 1 and len(rec.book.lots) == 1


# ---- the database part ------------------------------------------------------------------------------------------------------------

def test_the_upsert_matches_the_table_and_adds_instead_of_replacing():
    ddl = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE IF NOT EXISTS trade_flow_1m \((.*?)\n\);", ddl, re.DOTALL).group(1)
    cols = re.findall(r"^\s{4}([a-z_0-9]+)\s", table, re.MULTILINE)
    insert_cols = [c.strip() for c in re.search(r"INSERT INTO trade_flow_1m \((.*?)\) VALUES", tradeflow._UPSERT).group(1).split(",")]
    assert insert_cols == cols[:len(insert_cols)] and "PRIMARY KEY (session_date, ticker, minute)" in table
    assert tradeflow._UPSERT.count("%s") == len(insert_cols) == 12
    for c in ("n_buy", "n_sell", "buy_lot", "sell_lot", "buy_val", "sell_val"):
        assert f"{c} = trade_flow_1m.{c} + EXCLUDED.{c}" in tradeflow._UPSERT
    assert "GREATEST(trade_flow_1m.max_val" in tradeflow._UPSERT and "LEAST(trade_flow_1m.px_low" in tradeflow._UPSERT


def test_writing_rows_is_one_executemany_and_nothing_for_an_empty_list(monkeypatch):
    log = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def executemany(self, sql, rows):
            log.append(("many", sql, list(rows)))

    class Conn(Cur):
        def cursor(self):
            return Cur()

    monkeypatch.setattr(tradeflow, "connect", lambda: Conn())
    assert tradeflow.write_rows([]) == 0 and log == []
    rows = [(FRI, "BBCA", 555, 1, 0, 10.0, 0.0, 100.0, 0.0, 100.0, 10.0, 10.0)]
    assert tradeflow.write_rows(rows) == 1 and log[0][2] == rows and log[0][1] == tradeflow._UPSERT


# ---- a scripted world: clock, socket, writer --------------------------------------------------------------------------------------

class World:
    """A fake clock that only moves when the code sleeps or a script step says so, plus the sockets to connect to."""

    def __init__(self, start=time(10, 0), day=FRI):
        self.t = datetime.combine(day, start, tzinfo=WIB)
        self.offset = 0.0
        self.sleeps, self.sockets, self.written = [], [], []

    def clock(self):
        return self.t + timedelta(seconds=self.offset)

    def mono(self):
        return self.offset

    def sleep(self, s):
        self.sleeps.append(s)
        self.offset += s

    def writer(self, rows):
        self.written.extend(rows)
        return len(rows)

    def connect(self):
        if not self.sockets:
            raise ConnectionError("no more scripted sockets")
        return self.sockets.pop(0)

    def recorder(self, **kw):
        kw.setdefault("close_at", time(16, 5))
        return Recorder(connect_ws=self.connect, writer=self.writer, clock=self.clock, sleep=self.sleep, mono=self.mono, **kw)


class Socket:
    """Steps: a str = a frame; an Exception = raised by recv; ("tick", s) = time passes; "timeout" = recv times out."""

    def __init__(self, world, *steps):
        self.world, self.steps = world, list(steps)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def recv(self, timeout=None):
        while True:
            if not self.steps:
                raise ConnectionError("script ended")
            s = self.steps.pop(0)
            if isinstance(s, tuple):
                self.world.offset += s[1]
                continue
            if s == "timeout":
                self.world.offset += timeout or 1
                raise TimeoutError
            if isinstance(s, Exception):
                raise s
            return s


def n(i):
    return f"2026110600000000{i:04d}"


def test_the_snapshot_and_the_top_five_are_not_counted_but_trades_are():
    w = World()
    rec = w.recorder()
    rec.handle(json.dumps({"type": "snapshot", "market": {"status": "open"}, "recent": [trade(tn=n(1)), trade(tn=n(2))], "top": []}))
    rec.handle(json.dumps({"type": "top5", "top": [], "boards": {}}))
    rec.handle(json.dumps({"type": "filter_applied"}))
    rec.handle("not json at all")
    rec.handle(msg(tn=n(3)))
    c = rec.book.counts
    assert (c.accepted, c.snapshots, c.rejected) == (1, 1, 1)


def test_flushing_waits_for_its_interval_and_a_forced_flush_does_not():
    w = World()
    rec = w.recorder(flush_seconds=10)
    rec.handle(msg(tn=n(1)))
    rec.flush()
    assert w.written == []                                       # 0 s since the last flush
    w.offset = 10
    rec.flush()
    assert len(w.written) == 1 and rec.book.buckets == {}
    rec.handle(msg(tn=n(2)))
    rec.flush(force=True)
    assert len(w.written) == 2


def test_a_failed_write_keeps_the_trades_and_the_next_write_has_all_of_them():
    w = World()
    calls = {"n": 0}

    def flaky(rows):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is restarting")
        return w.writer(rows)

    rec = Recorder(connect_ws=w.connect, writer=flaky, clock=w.clock, sleep=w.sleep, mono=w.mono, flush_seconds=0)
    rec.handle(msg(l=10, v=100, tn=n(1)))
    rec.flush(force=True)
    assert rec.stats.write_errors == 1 and "database is restarting" in rec.stats.last_error and w.written == []
    rec.handle(msg(l=5, v=50, tn=n(2)))
    rec.flush(force=True)
    (row,) = w.written
    assert (row[3], row[5], row[7]) == (2, 15.0, 150.0)          # both trades, once: n_buy, buy_lot, buy_val


# ---- the connection loop ----------------------------------------------------------------------------------------------------------

def test_a_normal_day_records_until_the_market_closes():
    w = World(start=time(16, 4, 50))
    w.sockets = [Socket(w, msg(tn=n(1)), msg(tn=n(2), c="sell"), ("tick", 30))]
    rec = w.recorder(flush_seconds=5)
    assert rec.run() == 0
    assert rec.stats.stopped == "market_closed" and rec.stats.connections == 1
    (row,) = w.written
    assert (row[3], row[4]) == (1, 1)                            # one buy and one sell in the minute


def test_a_dropped_connection_is_reopened_after_a_growing_wait_and_nothing_is_counted_twice():
    w = World()
    w.sockets = [Socket(w, msg(tn=n(1)), ConnectionError("reset")),
                 Socket(w, msg(tn=n(1)), msg(tn=n(2)), ConnectionError("reset again")),      # the first trade repeats: dropped
                 Socket(w, msg(tn=n(3)), ("tick", 30000))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 0
    assert w.sleeps == [2.0, 5.0] and rec.stats.connections == 3
    assert (rec.book.counts.accepted, rec.book.counts.duplicates) == (3, 1)
    assert sum(r[3] for r in w.written) == 3


def test_the_reconnect_wait_grows_to_a_cap():
    w = World()
    w.sockets = [Socket(w, ConnectionError("x")) for _ in range(6)] + [Socket(w, ("tick", 30000))]
    assert w.recorder().run() == 0
    assert w.sleeps == [2.0, 5.0, 10.0, 30.0, 30.0, 30.0]


def test_a_connection_that_was_healthy_for_a_while_is_reopened_at_once_and_starts_the_waits_again():
    w = World()
    w.sockets = [Socket(w, ConnectionError("x")), Socket(w, ConnectionError("x")),            # two quick drops: 2 s, then 5 s
                 Socket(w, msg(tn=n(1)), ("tick", 120), ConnectionError("dropped after two minutes")),    # healthy: NO wait, waits start over
                 Socket(w, ConnectionError("x")), Socket(w, ("tick", 30000))]
    assert w.recorder().run() == 0
    assert w.sleeps == [2.0, 5.0, 2.0]


SNAP = json.dumps({"type": "snapshot", "market": {"status": "open"}, "top": [],
                   "recent": [trade(tn=n(2)), trade(tn=n(3)), trade(tn=n(4))]})


def test_after_a_healthy_connection_drops_the_reconnect_snapshot_wins_back_what_was_missed():
    w = World()
    w.sockets = [Socket(w, msg(tn=n(1)), msg(tn=n(2)), ("tick", 120), ConnectionError("no close frame received or sent")),
                 Socket(w, SNAP, msg(tn=n(5)), ("tick", 30000))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 0 and w.sleeps == []                     # asked again at once
    c = rec.book.counts
    assert (c.accepted, c.duplicates, rec.stats.recovered) == (5, 0, 2)       # n3 and n4 were missed; n2 was already counted, silently
    assert (rec.stats.gaps, rec.stats.blind_seconds) == (1, 0.0)
    assert "blind 0.0 s over 1 drops, 2 trades won back" in rec.summary()
    assert sum(r[3] for r in w.written) == 5


def test_the_snapshot_of_the_first_connection_is_never_counted():
    w = World()                                                  # a restart: the snapshot repeats what the last run already stored
    w.sockets = [Socket(w, SNAP, msg(tn=n(9)), ("tick", 30000))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 0
    assert rec.book.counts.accepted == 1 and rec.stats.recovered == 0 and rec.book.counts.snapshots == 1


def test_the_blind_time_runs_from_the_drop_to_the_first_message_even_when_a_retry_fails():
    w = World()
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 120), ConnectionError("dropped")),         # healthy, then dropped
                 Socket(w, ConnectionError("refused again")),                                  # reopened at once, dies before any message
                 Socket(w, SNAP, ("tick", 30000))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 0 and w.sleeps == [2.0]
    assert rec.stats.gaps == 1 and rec.stats.blind_seconds == 2.0                 # one outage of 2 s, not two of ~0 s


def test_a_snapshot_with_malformed_items_counts_the_good_ones_and_rejects_the_rest():
    w = World()
    snap = json.dumps({"type": "snapshot", "top": [], "recent": [trade(tn=n(3)), {"junk": 1}, trade(tn=n(4))]})
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 120), ConnectionError("x")), Socket(w, snap, ("tick", 30000))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 0
    assert (rec.book.counts.accepted, rec.stats.recovered, rec.book.counts.rejected) == (3, 2, 1)


@pytest.mark.parametrize("code, why", [(4401, "refused"), (4403, "paid user")])
def test_a_refused_key_stops_at_once_and_still_saves_what_was_recorded(code, why):
    pytest.importorskip("websockets")
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    w = World()
    w.sockets = [Socket(w, msg(tn=n(1)), ConnectionClosed(Close(code, "no"), None)), Socket(w, msg(tn=n(2)))]
    rec = w.recorder(flush_seconds=1000)
    assert rec.run() == 2
    assert why in rec.stats.stopped and w.sleeps == [] and len(w.sockets) == 1       # no retry
    assert len(w.written) == 1                                                       # the trade before the refusal was flushed


def test_a_taken_over_connection_waits_a_minute_and_gives_up_after_five():
    pytest.importorskip("websockets")
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    w = World()
    w.sockets = [Socket(w, ConnectionClosed(Close(4408, "superseded"), None)) for _ in range(6)]
    rec = w.recorder()
    assert rec.run() == 1
    assert w.sleeps == [tradeflow.TAKEOVER_WAIT] * 4 and "4408" in rec.stats.stopped


def test_a_connection_that_goes_silent_is_replaced():
    w = World()
    w.sockets = [Socket(w, msg(tn=n(1)), *(["timeout"] * 30)), Socket(w, msg(tn=n(2)), ("tick", 30000))]
    rec = w.recorder(flush_seconds=10, stall_seconds=90)
    assert rec.run() == 0 and rec.stats.connections == 2
    assert w.sleeps == []                                         # it had been up for 90+ s, so it counts as healthy: replaced at once, no wait
    assert rec.stats.gaps == 1 and rec.stats.blind_seconds == 0.0
    assert rec.book.counts.accepted == 2 and sum(r[3] for r in w.written) == 2


def test_a_weekend_connects_to_nothing():
    w = World(day=date(2026, 11, 7))                             # a Saturday
    w.sockets = [Socket(w, msg())]
    rec = w.recorder()
    assert rec.run() == 0 and rec.stats.stopped == "weekend" and len(w.sockets) == 1 and rec.stats.connections == 0


def test_before_the_open_it_waits_and_then_records():
    w = World(start=time(8, 40))
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 40000))]
    rec = w.recorder(open_at=time(8, 50))
    assert rec.run() == 0 and w.sleeps[0] == 30.0 and rec.stats.connections == 1 and sum(r[3] for r in w.written) == 1


def test_a_smoke_test_stops_after_its_time_even_at_the_weekend():
    w = World(day=date(2026, 11, 7))
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 100))]
    rec = w.recorder(max_seconds=60)
    assert rec.run() == 0 and rec.stats.stopped == "max_seconds" and rec.stats.connections == 1


# ---- the exchange calendar --------------------------------------------------------------------------------------------------------

def test_the_holiday_list_is_the_22_announced_weekdays_of_2026():
    days = tradeflow.NON_TRADING_DAYS
    assert len(days) == 22 and all(d.year == 2026 and d.weekday() < 5 for d in days)       # a weekend needs no entry
    assert {date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 31)} <= days             # Christmas leave, Christmas, the exchange's own day
    assert {date(2026, 1, 1), date(2026, 3, 19), date(2026, 8, 17), date(2026, 8, 25)} <= days
    assert 2026 in tradeflow.CALENDAR_YEARS


def test_an_exchange_holiday_connects_to_nothing(monkeypatch):
    monkeypatch.delenv("VG_EXTRA_NON_TRADING_DAYS", raising=False)
    w = World(day=date(2026, 12, 25))                            # a Friday, but Christmas
    w.sockets = [Socket(w, msg())]
    rec = w.recorder()
    assert rec.run() == 0 and rec.stats.stopped == "exchange holiday" and rec.stats.connections == 0 and len(w.sockets) == 1


def test_the_trading_days_around_a_holiday_do_record(monkeypatch):
    monkeypatch.delenv("VG_EXTRA_NON_TRADING_DAYS", raising=False)
    for day in (date(2026, 12, 23), date(2026, 12, 28), date(2026, 11, 9)):
        w = World(day=day)
        w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 30000))]
        rec = w.recorder()
        assert rec.run() == 0 and rec.stats.connections == 1 and rec.stats.stopped == "market_closed", day


def test_a_holiday_the_government_adds_later_can_be_put_in_the_environment(monkeypatch):
    monkeypatch.setenv("VG_EXTRA_NON_TRADING_DAYS", "2026-11-09, not-a-date, ,2026-11-10")
    assert {date(2026, 11, 9), date(2026, 11, 10)} <= tradeflow.non_trading_days() and tradeflow.NON_TRADING_DAYS <= tradeflow.non_trading_days()
    w = World(day=date(2026, 11, 9))                             # a Monday
    w.sockets = [Socket(w, msg())]
    rec = w.recorder()
    assert rec.run() == 0 and rec.stats.stopped == "exchange holiday" and rec.stats.connections == 0


def test_a_year_without_a_calendar_records_on_weekdays_and_says_so(monkeypatch):
    monkeypatch.delenv("VG_EXTRA_NON_TRADING_DAYS", raising=False)
    w = World(day=date(2027, 1, 5))                              # a Tuesday in a year the list does not cover
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 30000))]
    rec = w.recorder()
    assert rec.run() == 0 and rec.stats.connections == 1
    assert "no holiday calendar loaded for 2027" in rec.stats.note and "note: no holiday calendar" in rec.summary()


def test_a_smoke_test_runs_even_on_a_holiday(monkeypatch):
    monkeypatch.delenv("VG_EXTRA_NON_TRADING_DAYS", raising=False)
    w = World(day=date(2026, 12, 25))
    w.sockets = [Socket(w, msg(tn=n(1)), ("tick", 100))]
    rec = w.recorder(max_seconds=60)
    assert rec.run() == 0 and rec.stats.connections == 1 and rec.stats.stopped == "max_seconds"


# ---- helpers and the command ------------------------------------------------------------------------------------------------------

def test_the_socket_address_is_the_feed_address_with_a_secure_scheme():
    assert tradeflow.ws_url("https://stock.arjum.com") == "wss://stock.arjum.com/ws/running-trade"
    assert tradeflow.ws_url("http://localhost:9000/") == "ws://localhost:9000/ws/running-trade"
    with pytest.raises(ValueError):
        tradeflow.ws_url("ftp://x")


def test_the_close_code_is_read_from_the_server_frame_and_is_none_for_other_errors():
    pytest.importorskip("websockets")
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    assert tradeflow.close_code(ConnectionClosed(Close(4403, "x"), None)) == 4403
    assert tradeflow.close_code(ConnectionClosed(None, Close(1006, ""))) == 1006
    assert tradeflow.close_code(ConnectionError("x")) is None


def test_the_command_logs_one_run_and_a_dry_run_logs_nothing(monkeypatch, capsys):
    logged = []

    class Fake:
        def __init__(self, **kw):
            self.today, self.kw = FRI, kw
            self.book = MinuteBook(FRI)
            self.book.counts.accepted = 42
            self.stats = tradeflow.Stats(stopped="market_closed")

        def run(self):
            return 0

        def summary(self):
            return "trades 42"

    monkeypatch.setattr(tradeflow, "Recorder", Fake)
    monkeypatch.setattr(tradeflow, "log_run", lambda *a: logged.append(a))
    assert tradeflow.record() == 0
    assert logged == [(FRI, "ok", 42, "trades 42")] and "trades 42" in capsys.readouterr().out
    logged.clear()
    assert tradeflow.record(dry_run=True) == 0 and logged == []


def test_the_cli_knows_the_command(monkeypatch):
    from vg import __main__ as cli

    seen = {}
    monkeypatch.setattr(tradeflow, "record", lambda **k: seen.update(k) or 0)
    assert cli.main(["record-trades", "--max-seconds", "5", "--dry-run", "--flush-seconds", "2"]) == 0
    assert seen == {"max_seconds": 5.0, "flush_seconds": 2.0, "dry_run": True}
