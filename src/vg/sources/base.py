"""Source contracts. See PRINSIP-DATA.md for the tier system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

FetchStatus = Literal["ok", "no_data", "date_mismatch", "blocked", "error"]

Num = Optional[Decimal]


@dataclass(slots=True)
class PriceBar:
    """Normalized daily bar for one ticker. Numeric fields are Decimal|None."""

    ticker: str
    name: str | None
    prev: Num = None
    open: Num = None
    first_trade: Num = None
    high: Num = None
    low: Num = None
    close: Num = None
    change: Num = None
    volume: Num = None
    value: Num = None
    frequency: Num = None
    foreign_buy_vol: Num = None
    foreign_sell_vol: Num = None
    nonreg_volume: Num = None
    nonreg_value: Num = None
    nonreg_frequency: Num = None
    listed_shares: Num = None
    tradable_shares: Num = None
    weight_for_index: Num = None
    index_individual: Num = None
    bid: Num = None
    bid_volume: Num = None
    offer: Num = None
    offer_volume: Num = None
    remarks: str | None = None
    delisting_date: date | None = None


@dataclass(slots=True)
class PriceFetchResult:
    status: FetchStatus
    session_date: date
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    bars: list[PriceBar] = field(default_factory=list)
    detail: str = ""


class PriceSource(ABC):
    """Per-ticker daily OHLCV + foreign flow for a single trading day."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, session_date: date) -> PriceFetchResult:
        ...


@dataclass(slots=True)
class IndexBar:
    index_code: str
    prev: Num = None
    high: Num = None
    low: Num = None
    close: Num = None
    change: Num = None
    num_stock: Num = None
    volume: Num = None
    value: Num = None
    frequency: Num = None
    market_capital: Num = None


@dataclass(slots=True)
class IndexFetchResult:
    status: FetchStatus
    session_date: date
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    bars: list[IndexBar] = field(default_factory=list)
    detail: str = ""


class IndexSource(ABC):
    """Daily index values (IHSG + others) for a single trading day."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, session_date: date | None) -> IndexFetchResult:
        ...


# Investor-category codes used by KSEI (both Local and Foreign):
#   IS insurance · CP corporate · PF pension fund · IB financial institution/bank
#   ID individual · MF mutual fund · SC securities company · FD foundation · OT other
KSEI_CATEGORIES = ("IS", "CP", "PF", "IB", "ID", "MF", "SC", "FD", "OT")


@dataclass(slots=True)
class OwnershipSnapshot:
    """One EQUITY row from a KSEI month-end balance-position file (share counts)."""

    ticker: str
    price: Num = None          # month-end close, as recorded by KSEI
    sec_num: Num = None        # total registered shares
    local: dict[str, Num] = field(default_factory=dict)    # keys: KSEI_CATEGORIES + "TOTAL"
    foreign: dict[str, Num] = field(default_factory=dict)


@dataclass(slots=True)
class OwnershipFetchResult:
    status: FetchStatus
    month_end: date | None
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    raw_ext: str = "zip"
    rows: list[OwnershipSnapshot] = field(default_factory=list)
    detail: str = ""


class OwnershipSource(ABC):
    """Month-end ownership composition by investor category (local/foreign)."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, month: date) -> OwnershipFetchResult:
        ...


@dataclass(slots=True)
class CorpAction:
    ticker: str
    ca_type: str                       # normalized: cash_dividend, rights, stock_split, ...
    raw_type: str                      # original 'Type of CA' string
    ex_date: date | None               # recording_date, or cum_date if that's missing
    cum_date: date | None = None
    distribution_date: date | None = None
    exercise_ratio: str | None = None
    proceed_ratio: str | None = None
    issuer: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class CaFetchResult:
    status: FetchStatus
    year: int
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    raw_ext: str = "xls"
    rows: list[CorpAction] = field(default_factory=list)
    detail: str = ""


class CorpActionSource(ABC):
    """A year's corporate-action schedule."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, year: int) -> CaFetchResult:
        ...


@dataclass(slots=True)
class DisclosureFiling:
    """One ownership-disclosure filing (>5% substantial holder or director/commissioner
    change) — POJK 4/2024, filed via KSEI AKSes, published on the IDX announcement feed."""

    ticker: str
    no_pengumuman: str                 # IDX NoPengumuman — unique per filing
    filed_at: datetime                 # TglPengumuman (naive WIB)
    filed_date: date                   # filed_at.date() — the engine bucket key
    title: str | None = None           # JudulPengumuman
    subject: str | None = None         # PerihalPengumuman (raw)
    party: str | None = None           # reporting party parsed from subject (heuristic)
    jenis: str | None = None           # JenisPengumuman ("STOCK")
    attachments: list[dict] = field(default_factory=list)   # [{original_filename, full_save_path}]
    raw: dict = field(default_factory=dict)                 # full pengumuman node + attachments


@dataclass(slots=True)
class DisclosureFetchResult:
    status: FetchStatus
    session_date: date
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    raw_ext: str = "json"
    rows: list[DisclosureFiling] = field(default_factory=list)
    detail: str = ""


class DisclosureSource(ABC):
    """Ownership-disclosure filings (>5% substantial + insider) for one filing day."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, session_date: date) -> DisclosureFetchResult:
        ...


@dataclass(slots=True)
class BrokerFlowRow:
    """One broker's buy/sell on one ticker for one trading day (all boards).

    The feed gives value/volume/frequency per side; `buy_avg`/`sell_avg` (IDR per
    share) and `side_flag` (D/F origin) are derived — the feed carries neither.
    `side_flag` comes from a static broker->origin map, NOT from the data itself.
    """

    broker_code: str
    broker_name: str | None = None
    buy_val: Num = None
    buy_vol: Num = None
    buy_freq: Num = None
    sell_val: Num = None
    sell_vol: Num = None
    sell_freq: Num = None
    net_val: Num = None
    net_vol: Num = None
    buy_avg: Num = None            # buy_val / buy_vol
    sell_avg: Num = None           # sell_val / sell_vol
    side_flag: str | None = None   # 'D' | 'F' | None — from the static origin map
    board: str = "ALL"             # this feed does not split RG/NG/TN


@dataclass(slots=True)
class BrokerSummaryFetchResult:
    status: FetchStatus
    session_date: date
    ticker: str
    source: str
    source_tier: int
    raw_bytes: bytes | None = None
    raw_ext: str = "json"
    rows: list[BrokerFlowRow] = field(default_factory=list)
    detail: str = ""


class BrokerSummarySource(ABC):
    """Per-ticker per-broker buy/sell for one trading day.

    Phase 1.5 feed. The free stock.arjum.com implementation is forward-only
    (no historical date param) and Tier 3 (scraped retail platform) — treat as a
    cross-check, never as a production source. See RENCANA-FASE-1.md."""

    name: str
    tier: int

    @abstractmethod
    def fetch(self, session_date: date, ticker: str) -> BrokerSummaryFetchResult:
        ...
