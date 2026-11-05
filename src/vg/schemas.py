"""Pydantic v2 row schemas: the validation gate between a source's `fetch()` and an `INSERT ... ON CONFLICT`.

One model = one database row of one existing table (`broker_flows`, `price_bars`, `broker_inventory`,
`done_by_price`). The table definitions in `db/schema.sql` are untouched; these models only describe what a row must
look like before it is allowed to reach Postgres.

What is validated, and what deliberately is not
-----------------------------------------------
* Validated (format and impossibility): ticker / broker-code shape, enum columns (`board`, `side_flag`), the
  tier range, volumes / values / prices that cannot be negative, and **NaN / Infinity in any NUMERIC** (Postgres
  accepts 'NaN' in a NUMERIC column, and one such row silently poisons every later SUM / AVG).
* NOT validated (data semantics): that high >= low, that buy + sell aggressor qty <= qty, that net = buy - sell.
  The feeds have real quirks there (see `collect._sane_open`); rejecting them would drop real rows. The one
  exception is the existing, documented `open` repair, reproduced in `PriceBar`.

Naming: `BrokerFlowRow`, `PriceBar`, `BrokerInventoryRow`, `DoneByPriceRow` also exist as the *source-side*
dataclasses in `vg.sources.base` (what `fetch()` returns). These are their database-side counterparts; import this
module as `schemas` where both are in scope. Each model's `from_source()` converts one into the other, and
`as_row()` yields the exact tuple `collect.py` builds today (a test pins that equivalence).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

if TYPE_CHECKING:  # pragma: no cover - typing only; schemas must stay importable without the source modules
    from .sources import base as _src

__all__ = [
    "BrokerFlowRow",
    "BrokerInventoryRow",
    "DoneByPriceRow",
    "PriceBar",
    "RowModel",
]


def _upper(v):
    return v.strip().upper() if isinstance(v, str) else v


# IDX codes are 4 letters, 5 for warrants / rights / special boards ("GOTOM", "MYRXP" exist in price_bars).
# The pattern is intentionally a little wider than what is stored today.
Ticker = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z][A-Z0-9]{1,7}$")]
BrokerCode = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z]{2}$")]

# NUMERIC columns. `NonNeg` for quantities that cannot be negative; `Signed` for net / change.
NonNeg = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Signed = Annotated[Decimal, Field(allow_inf_nan=False)]

Tier = Annotated[int, Field(ge=0, le=3)]  # 0 official ... 3 scraped retail platform (RENCANA-FASE-1)
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RowModel(BaseModel):
    """Common behaviour: column order, conflict key, and tuple export for `upsert_many`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    TABLE: ClassVar[str]
    COLUMNS: ClassVar[tuple[str, ...]]
    CONFLICT: ClassVar[tuple[str, ...]]

    @classmethod
    def update_columns(cls) -> tuple[str, ...]:
        """Columns rewritten on conflict: everything except the conflict key."""
        return tuple(c for c in cls.COLUMNS if c not in cls.CONFLICT)

    def as_row(self) -> tuple:
        """Values in `COLUMNS` order, ready for `upsert_many`."""
        return tuple(getattr(self, c) for c in self.COLUMNS)

    def key(self) -> tuple:
        """The conflict key of this row (for in-batch de-duplication and for error reports)."""
        return tuple(getattr(self, c) for c in self.CONFLICT)


# ---------------------------------------------------------------------------------------------------------------
# broker_flows
# ---------------------------------------------------------------------------------------------------------------


class BrokerFlowRow(RowModel):
    """One broker's buy/sell on one ticker for one day (`broker_flows`)."""

    TABLE: ClassVar[str] = "broker_flows"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "session_date", "ticker", "broker_code", "side_flag", "buy_val", "sell_val",
        "buy_vol", "sell_vol", "buy_avg", "sell_avg", "buy_freq", "sell_freq",
        "net_val", "net_vol", "board", "source", "source_tier", "raw_sha", "parser_version",
    )
    CONFLICT: ClassVar[tuple[str, ...]] = ("session_date", "ticker", "broker_code", "board")

    session_date: date
    ticker: Ticker
    broker_code: BrokerCode
    side_flag: Literal["D", "F"] | None = None
    buy_val: NonNeg | None = None
    sell_val: NonNeg | None = None
    buy_vol: NonNeg | None = None
    sell_vol: NonNeg | None = None
    buy_avg: NonNeg | None = None
    sell_avg: NonNeg | None = None
    buy_freq: NonNeg | None = None
    sell_freq: NonNeg | None = None
    net_val: Signed | None = None
    net_vol: Signed | None = None
    board: Literal["ALL", "RG", "NG", "TN"] = "ALL"
    source: Source
    source_tier: Tier
    raw_sha: str | None = None
    parser_version: str | None = None

    @classmethod
    def from_source(
        cls,
        r: _src.BrokerFlowRow,
        *,
        session_date: date,
        ticker: str,
        source: str,
        source_tier: int,
        raw_sha: str | None = None,
        parser_version: str | None = None,
    ) -> BrokerFlowRow:
        """Build from a `vg.sources.base.BrokerFlowRow` plus the fetch-level context it does not carry.
        (`broker_name` is not a `broker_flows` column, so it is not copied.)"""
        return cls(
            session_date=session_date, ticker=ticker, broker_code=r.broker_code, side_flag=r.side_flag,
            buy_val=r.buy_val, sell_val=r.sell_val, buy_vol=r.buy_vol, sell_vol=r.sell_vol,
            buy_avg=r.buy_avg, sell_avg=r.sell_avg, buy_freq=r.buy_freq, sell_freq=r.sell_freq,
            net_val=r.net_val, net_vol=r.net_vol, board=r.board, source=source, source_tier=source_tier,
            raw_sha=raw_sha, parser_version=parser_version,
        )


# ---------------------------------------------------------------------------------------------------------------
# price_bars
# ---------------------------------------------------------------------------------------------------------------


class PriceBar(RowModel):
    """One ticker's daily bar (`price_bars`)."""

    TABLE: ClassVar[str] = "price_bars"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "session_date", "ticker", "prev", "open", "first_trade", "high", "low", "close",
        "change", "volume", "value", "frequency", "foreign_buy_vol", "foreign_sell_vol",
        "nonreg_volume", "nonreg_value", "nonreg_frequency", "listed_shares",
        "tradable_shares", "weight_for_index", "index_individual", "bid", "bid_volume",
        "offer", "offer_volume", "remarks", "delisting_date", "source", "source_tier",
    )
    CONFLICT: ClassVar[tuple[str, ...]] = ("session_date", "ticker")

    session_date: date
    ticker: Ticker
    prev: NonNeg | None = None
    open: NonNeg | None = None
    # IDX writes the sentinel -99999999 when a ticker has no first trade (105 stored rows, 41 tickers, still
    # arriving 2026-11-02). It is real source data, so it must pass; it is stored as received, not cleaned.
    first_trade: Signed | None = None
    high: NonNeg | None = None
    low: NonNeg | None = None
    close: NonNeg | None = None
    change: Signed | None = None
    volume: NonNeg | None = None
    value: NonNeg | None = None
    frequency: NonNeg | None = None
    foreign_buy_vol: NonNeg | None = None
    foreign_sell_vol: NonNeg | None = None
    nonreg_volume: NonNeg | None = None
    nonreg_value: NonNeg | None = None
    nonreg_frequency: NonNeg | None = None
    listed_shares: NonNeg | None = None
    tradable_shares: NonNeg | None = None
    weight_for_index: Signed | None = None
    index_individual: Signed | None = None
    bid: NonNeg | None = None
    bid_volume: NonNeg | None = None
    offer: NonNeg | None = None
    offer_volume: NonNeg | None = None
    remarks: str | None = None
    delisting_date: date | None = None
    source: Source
    source_tier: Tier

    @model_validator(mode="after")
    def _null_impossible_open(self) -> PriceBar:
        """`open` outside [low, high] is a source quirk (128 traded rows on 2026-10-16): store NULL, not a lie.
        Untraded rows (low == 0) are left alone. Same rule as `collect._sane_open`."""
        o, lo, hi = self.open, self.low, self.high
        if o is not None and lo and hi is not None and not (lo <= o <= hi):
            self.open = None
        return self

    @classmethod
    def from_source(
        cls,
        bar: _src.PriceBar,
        *,
        source: str,
        source_tier: int,
        session_date: date | None = None,
    ) -> PriceBar:
        """Build from a `vg.sources.base.PriceBar`. A one-day fetch (IDX Stock Summary) passes `session_date`;
        a per-ticker history bar carries its own `bar_date`."""
        return cls(
            session_date=session_date or bar.bar_date, ticker=bar.ticker, prev=bar.prev, open=bar.open,
            first_trade=bar.first_trade, high=bar.high, low=bar.low, close=bar.close, change=bar.change,
            volume=bar.volume, value=bar.value, frequency=bar.frequency,
            foreign_buy_vol=bar.foreign_buy_vol, foreign_sell_vol=bar.foreign_sell_vol,
            nonreg_volume=bar.nonreg_volume, nonreg_value=bar.nonreg_value,
            nonreg_frequency=bar.nonreg_frequency, listed_shares=bar.listed_shares,
            tradable_shares=bar.tradable_shares, weight_for_index=bar.weight_for_index,
            index_individual=bar.index_individual, bid=bar.bid, bid_volume=bar.bid_volume,
            offer=bar.offer, offer_volume=bar.offer_volume, remarks=bar.remarks,
            delisting_date=bar.delisting_date, source=source, source_tier=source_tier,
        )


# ---------------------------------------------------------------------------------------------------------------
# broker_inventory
# ---------------------------------------------------------------------------------------------------------------


class BrokerInventoryRow(RowModel):
    """One broker's net and running inventory on one day (`broker_inventory`)."""

    TABLE: ClassVar[str] = "broker_inventory"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "session_date", "ticker", "broker_code", "broker_name", "net_val", "net_vol",
        "cum_net_val", "buy_avg", "sell_avg", "source", "source_tier",
    )
    CONFLICT: ClassVar[tuple[str, ...]] = ("session_date", "ticker", "broker_code")

    session_date: date
    ticker: Ticker
    broker_code: BrokerCode
    broker_name: str | None = None
    net_val: Signed | None = None
    net_vol: Signed | None = None
    cum_net_val: Signed | None = None     # running inventory: legitimately negative for a net seller
    buy_avg: NonNeg | None = None
    sell_avg: NonNeg | None = None
    source: Source
    source_tier: Tier

    @classmethod
    def from_source(
        cls, r: _src.BrokerInventoryRow, *, ticker: str, source: str, source_tier: int
    ) -> BrokerInventoryRow:
        return cls(
            session_date=r.bar_date, ticker=ticker, broker_code=r.broker_code, broker_name=r.broker_name,
            net_val=r.net_val, net_vol=r.net_vol, cum_net_val=r.cum_net_val, buy_avg=r.buy_avg,
            sell_avg=r.sell_avg, source=source, source_tier=source_tier,
        )


# ---------------------------------------------------------------------------------------------------------------
# done_by_price
# ---------------------------------------------------------------------------------------------------------------


class DoneByPriceRow(RowModel):
    """Done-details aggregated to one price level on one board for one day (`done_by_price`)."""

    TABLE: ClassVar[str] = "done_by_price"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "session_date", "ticker", "board", "price_num", "qty", "value", "trades",
        "buy_aggressor_qty", "sell_aggressor_qty", "foreign_buy_qty", "foreign_sell_qty",
        "source", "source_tier",
    )
    CONFLICT: ClassVar[tuple[str, ...]] = ("session_date", "ticker", "board", "price_num")

    session_date: date
    ticker: Ticker
    board: Literal["RG", "NG", "TN"]
    price_num: Annotated[int, Field(gt=0)]
    qty: NonNeg | None = None
    value: NonNeg | None = None
    trades: Annotated[int, Field(ge=0)] = 0
    buy_aggressor_qty: NonNeg | None = None
    sell_aggressor_qty: NonNeg | None = None
    foreign_buy_qty: NonNeg | None = None
    foreign_sell_qty: NonNeg | None = None
    source: Source
    source_tier: Tier

    @classmethod
    def from_source(
        cls, r: _src.DoneByPriceRow, *, session_date: date, ticker: str, source: str, source_tier: int
    ) -> DoneByPriceRow:
        return cls(
            session_date=session_date, ticker=ticker, board=r.board, price_num=r.price_num, qty=r.qty,
            value=r.value, trades=r.trades, buy_aggressor_qty=r.buy_aggressor_qty,
            sell_aggressor_qty=r.sell_aggressor_qty, foreign_buy_qty=r.foreign_buy_qty,
            foreign_sell_qty=r.foreign_sell_qty, source=source, source_tier=source_tier,
        )
