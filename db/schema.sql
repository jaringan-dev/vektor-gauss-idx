-- Vektor Gauss — canonical schema (Phase 1)
-- Idempotent: safe to run repeatedly (`vg init-db`).
-- Source tiers (see PRINSIP-DATA.md): 0 = IDX/KSEI/OJK direct, 1 = licensed
-- redistributor, 2 = commercial aggregator (needs cross-validation), 3 = scraped
-- retail platform (never in production).

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trading_calendar (
    session_date  DATE PRIMARY KEY,
    confirmed_by  TEXT NOT NULL,            -- which source's data confirmed this was a trading day
    confirmed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tickers (
    ticker      TEXT PRIMARY KEY,           -- IDX code, e.g. 'BBRI'
    name        TEXT,
    sector      TEXT,                       -- filled later from another source
    is_idx250   BOOLEAN,                    -- filled later
    first_seen  DATE,
    last_seen   DATE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Time series — prices & flow (Phase 1: IDX Stock Summary, Tier 0)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS price_bars (
    session_date        DATE NOT NULL,
    ticker              TEXT NOT NULL,
    prev                NUMERIC,
    open                NUMERIC,
    first_trade         NUMERIC,
    high                NUMERIC,
    low                 NUMERIC,
    close               NUMERIC,
    change              NUMERIC,
    volume              NUMERIC,            -- shares
    value               NUMERIC,            -- IDR, all boards
    frequency           NUMERIC,
    -- Foreign flow as reported by IDX (shares). NOTE: whether this is all-boards
    -- or regular-only is UNVERIFIED — see PRINSIP-DATA.md A / README.
    foreign_buy_vol     NUMERIC,
    foreign_sell_vol    NUMERIC,
    -- Negotiated / cash board, broken out by IDX. Compute flow on (all - nonreg).
    nonreg_volume       NUMERIC,
    nonreg_value        NUMERIC,
    nonreg_frequency    NUMERIC,
    listed_shares       NUMERIC,
    tradable_shares     NUMERIC,
    weight_for_index    NUMERIC,
    index_individual    NUMERIC,
    bid                 NUMERIC,
    bid_volume          NUMERIC,
    offer               NUMERIC,
    offer_volume        NUMERIC,
    remarks             TEXT,               -- IDX special-notation string
    delisting_date      DATE,
    source              TEXT NOT NULL,
    source_tier         SMALLINT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_price_bars_ticker_date ON price_bars (ticker, session_date DESC);

-- IDX Index Summary (Tier 0). 'COMPOSITE' = IHSG. ~45 indices/day.
CREATE TABLE IF NOT EXISTS index_bars (
    session_date  DATE NOT NULL,
    index_code    TEXT NOT NULL,
    prev          NUMERIC,
    high          NUMERIC,
    low           NUMERIC,
    close         NUMERIC,
    change        NUMERIC,
    num_stock     NUMERIC,
    volume        NUMERIC,
    value         NUMERIC,
    frequency     NUMERIC,
    market_capital NUMERIC,
    source        TEXT NOT NULL,
    source_tier   SMALLINT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, index_code)
);
CREATE INDEX IF NOT EXISTS idx_index_bars_code_date ON index_bars (index_code, session_date DESC);

-- ---------------------------------------------------------------------------
-- Ownership (KSEI BalanceposEfek month-end file, Tier 0) — monthly.
-- Share counts. local_institution = local total - local individual.
-- detail JSONB holds the full 9-category L/F breakdown + price + sec_num +
-- scripless_pct. holders_count is NOT in this file (NULL until sourced elsewhere).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ownership_monthly (
    month_end           DATE NOT NULL,
    ticker              TEXT NOT NULL,
    local_individual    NUMERIC,
    local_institution   NUMERIC,
    foreign_total       NUMERIC,
    detail              JSONB,              -- {price, sec_num, local:{...}, foreign:{...}, scripless_pct}
    holders_count       NUMERIC,
    source              TEXT NOT NULL,
    source_tier         SMALLINT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (month_end, ticker)
);
CREATE INDEX IF NOT EXISTS idx_ownership_ticker ON ownership_monthly (ticker, month_end DESC);

-- Corporate actions. Two sources feed this:
--  * ksei_ca_schedule  — KSEI schedule_ca_keseluruhan_<year>.xls (HTML): cash/stock
--    dividends, rights distributions, mergers. type = normalized 'Type of CA'.
--  * derived_from_price_bars — share_count_change events inferred from a jump in
--    price_bars.listed_shares (splits/bonus/rights that KSEI's schedule omits).
-- ex_date: the date the price/share series goes ex (recording_date for KSEI rows;
-- the session_date of the listed_shares jump for derived rows).
CREATE TABLE IF NOT EXISTS corp_actions (
    ticker           TEXT NOT NULL,
    ex_date          DATE NOT NULL,
    type             TEXT NOT NULL,     -- cash_dividend, stock_dividend, rights, merger, share_count_change, ...
    ratio            NUMERIC,           -- share adjustment factor (new/old) where applicable
    cum_date         DATE,
    distribution_date DATE,
    exercise_ratio   TEXT,              -- raw KSEI ratio strings (e.g. "5 : 1")
    proceed_ratio    TEXT,
    issuer           TEXT,
    notes            TEXT,
    source           TEXT NOT NULL,
    source_tier      SMALLINT NOT NULL,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ex_date, type, source)
);
CREATE INDEX IF NOT EXISTS idx_corp_actions_ticker ON corp_actions (ticker, ex_date DESC);

-- Ownership-disclosure filings (Tier 0): >5% substantial shareholders and
-- director/commissioner ("insider") ownership changes, POJK 4/2024, filed via
-- KSEI AKSes and published on the IDX announcement feed
-- (/primary/ListedCompany/GetAnnouncement, keyword "LAPORAN KEPEMILIKAN").
-- Phase A stores filing metadata + archives the KSEI PDF form. Phase B parses
-- the PDFs into disclosure_holdings.
CREATE TABLE IF NOT EXISTS disclosure_filings (
    no_pengumuman     TEXT PRIMARY KEY,          -- IDX NoPengumuman, unique per filing
    ticker            TEXT NOT NULL,
    filed_at          TIMESTAMPTZ NOT NULL,      -- TglPengumuman (WIB)
    filed_date        DATE NOT NULL,             -- filed_at::date — the engine bucket key
    title             TEXT,                      -- JudulPengumuman
    subject           TEXT,                      -- PerihalPengumuman (raw)
    party             TEXT,                      -- reporting party parsed from subject (heuristic)
    jenis             TEXT,                      -- JenisPengumuman
    pdf_url           TEXT,                      -- first attachment FullSavePath
    pdf_artifact_path TEXT,                      -- local archived copy (raw_artifacts.path)
    raw               JSONB,                     -- full pengumuman node + attachments[]
    source            TEXT NOT NULL,
    source_tier       SMALLINT NOT NULL,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_disclosure_ticker_date ON disclosure_filings (ticker, filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_disclosure_filed_date  ON disclosure_filings (filed_date DESC);

-- ---------------------------------------------------------------------------
-- Disclosure PDF parse (Phase B, src/vg/sources/disclosure_pdf.py). One row
-- per transaction line in the filing's table; row_seq=0 with all
-- transaction_*/price/etc NULL when the per-row table didn't structurally
-- parse (rare — the header fields above it are regexed independently and
-- still land here, since shares_before/after alone is enough for the
-- INSIDER-*/SUBSTANTIAL-* direction tags in engine.py).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS disclosure_holdings (
    no_pengumuman         TEXT NOT NULL REFERENCES disclosure_filings(no_pengumuman),
    row_seq               SMALLINT NOT NULL,
    ticker                TEXT NOT NULL,
    filed_date            DATE NOT NULL,          -- denormalized from disclosure_filings
    reporter_name         TEXT,
    is_insider            BOOLEAN,                -- Anggota Direksi/Dewan Komisaris
    position              TEXT,                   -- Jabatan
    is_controller         BOOLEAN,                -- Keterangan Pengendali
    shares_before         NUMERIC,
    shares_after          NUMERIC,
    voting_rights_before  NUMERIC,                -- fraction, e.g. 0.000346 == 0.0346%
    voting_rights_after   NUMERIC,
    transaction_type      TEXT,                   -- raw Indonesian label, e.g. "Pembelian"
    direction             TEXT,                   -- 'buy' | 'sell' | 'other'
    ownership_status      TEXT,                   -- Langsung / Tidak Langsung
    direct_investor       TEXT,
    shares_qty            NUMERIC,                -- this row's transaction volume
    share_class           TEXT,
    price                 NUMERIC,
    transaction_date      DATE,
    purpose               TEXT,
    source                TEXT NOT NULL,
    source_tier           SMALLINT NOT NULL,
    parsed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (no_pengumuman, row_seq)
);
CREATE INDEX IF NOT EXISTS idx_disclosure_holdings_ticker_date ON disclosure_holdings (ticker, filed_date DESC);

-- ---------------------------------------------------------------------------
-- Paper-trading simulation (src/vg/paper.py) — HANDOFF.md's "keep a manual
-- spreadsheet" ask for FLOAT-ASING-PADAT / TIKET-BESAR-AKUM, automated.
-- One row per (ticker, tag, signal_date): entry/SL/target computed the same
-- way as digest.py's trade line, then walked forward against price_bars
-- until stop or target is hit or MAX_HOLD_DAYS elapses. A different lens
-- than signal_log's fixed-horizon returns -- "would this SL/target trade
-- plan have actually worked", trade by trade.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS paper_trades (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL,
    tag           TEXT NOT NULL,
    signal_date   DATE NOT NULL,
    entry         NUMERIC NOT NULL,          -- close on signal_date
    atr14         NUMERIC NOT NULL,
    stop_loss     NUMERIC NOT NULL,
    target        NUMERIC NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',   -- 'open' | 'win' | 'loss' | 'timeout'
    resolved_date DATE,
    exit_price    NUMERIC,                   -- stop/target price on win/loss; last close on timeout
    r_multiple    NUMERIC,                   -- (exit-entry)/(entry-stop_loss), signed
    days_held     INT,                       -- trading days from signal_date to resolved_date
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, tag, signal_date)
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_tag     ON paper_trades (tag, signal_date DESC);

-- ---------------------------------------------------------------------------
-- Trading-halt announcements (src/vg/sources/trading_halt.py). Metadata
-- only, no PDF — the announcement title is enough. Lets the digest say WHY a
-- watchlist ticker didn't trade instead of a bare "tidak diperdagangkan".
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS trading_halts (
    no_pengumuman TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    filed_at      TIMESTAMPTZ NOT NULL,
    filed_date    DATE NOT NULL,
    title         TEXT,
    is_halt       BOOLEAN NOT NULL,      -- true = the halt notice itself; false = a related follow-up
    source        TEXT NOT NULL,
    source_tier   SMALLINT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trading_halts_ticker_date ON trading_halts (ticker, filed_date DESC);

-- ---------------------------------------------------------------------------
-- Broker summary (Phase 1.5+, Tier 1 preferred). Defined now, left empty.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS broker_flows (
    session_date  DATE NOT NULL,
    ticker        TEXT NOT NULL,
    broker_code   TEXT NOT NULL,
    side_flag     TEXT,                     -- 'D' | 'F' | NULL
    buy_val       NUMERIC,
    sell_val      NUMERIC,
    buy_vol       NUMERIC,
    sell_vol      NUMERIC,
    buy_avg       NUMERIC,
    sell_avg      NUMERIC,
    board         TEXT,                     -- 'RG' | 'NG' | 'TN' | 'ALL'
    source        TEXT NOT NULL,
    source_tier   SMALLINT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, broker_code, board)
);

-- Per-broker daily net + running inventory (cum_net_val) over a trailing window,
-- from the VG feed's broker-accumulation endpoint (~80 td, top brokers per ticker,
-- Tier 3). HISTORICAL (unlike broker_flows which is latest-day only). Powers the
-- Transaction / Inventory / Compare-Inventory charts and Broker-Stalker views.
CREATE TABLE IF NOT EXISTS broker_inventory (
    session_date  DATE NOT NULL,
    ticker        TEXT NOT NULL,
    broker_code   TEXT NOT NULL,
    broker_name   TEXT,
    net_val       NUMERIC,               -- that day's net value (IDR)
    net_vol       NUMERIC,               -- that day's net volume (shares)
    cum_net_val   NUMERIC,               -- running inventory = cumulative net value
    buy_avg       NUMERIC,               -- avg buy price that day
    sell_avg      NUMERIC,               -- avg sell price that day
    source        TEXT NOT NULL,
    source_tier   SMALLINT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, broker_code)
);
CREATE INDEX IF NOT EXISTS idx_broker_inventory_ticker ON broker_inventory (ticker, broker_code, session_date);

-- Done-by-price aggregate (Tier 3). One done-details fetch (paginated, expensive)
-- is aggregated LOCALLY into price-level buckets so it is paid for once and never
-- re-fetched. Per (day, ticker, board, price): volume/value/trades + aggressor
-- split (action BUY/SELL = HAKA/HAKI) + foreign split (buyer/seller_type F).
-- This is the compact data layer for the Done Detail Visualization.
CREATE TABLE IF NOT EXISTS done_by_price (
    session_date        DATE NOT NULL,
    ticker              TEXT NOT NULL,
    board               TEXT NOT NULL,          -- RG | NG | TN
    price_num           BIGINT NOT NULL,
    qty                 NUMERIC,                -- total lots done at this price
    value               NUMERIC,                -- IDR
    trades              INT,                    -- number of done ticks
    buy_aggressor_qty   NUMERIC,                -- action=BUY (offer lifted, HAKA)
    sell_aggressor_qty  NUMERIC,                -- action=SELL (bid hit, HAKI)
    foreign_buy_qty     NUMERIC,                -- buyer_type=F
    foreign_sell_qty    NUMERIC,                -- seller_type=F
    source              TEXT NOT NULL,
    source_tier         SMALLINT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, board, price_num)
);
CREATE INDEX IF NOT EXISTS idx_done_by_price_ticker ON done_by_price (ticker, session_date);

CREATE TABLE IF NOT EXISTS broker_affiliation_groups (
    group_id     TEXT NOT NULL,
    broker_code  TEXT NOT NULL,
    group_name   TEXT,
    version      INT NOT NULL DEFAULT 1,
    valid_from   DATE NOT NULL,
    PRIMARY KEY (group_id, broker_code, version)
);

-- ---------------------------------------------------------------------------
-- Engine output (Phase 1 §5–6). NFF is in IDR (split-robust). All windows are
-- trading days. price_chg_5d / rs are computed on split-adjusted closes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signal_components (
    session_date            DATE NOT NULL,
    ticker                  TEXT NOT NULL,
    close                   NUMERIC,
    atr14                   NUMERIC,     -- 14-day ATR, for mechanical stop-loss / target
    price_chg_5d            NUMERIC,
    nff_d0                  NUMERIC,
    nff_w1                  NUMERIC,
    nff_w2                  NUMERIC,
    nff_w3                  NUMERIC,
    nff_w4                  NUMERIC,
    foreign_consistency_10d SMALLINT,
    vol_surge               NUMERIC,
    rs_ihsg_5d              NUMERIC,
    rs_ihsg_10d             NUMERIC,
    rs_ihsg_20d             NUMERIC,
    nonreg_ratio            NUMERIC,
    ksei_foreign_trend_3m   TEXT,        -- up | down | flat | nodata (sign of ksei_foreign_d3m)
    ksei_retail_trend_3m    TEXT,        -- (sign of ksei_indiv_d3m)
    ksei_foreign_d3m        NUMERIC,     -- 3-month change in %foreign of the scripless base (fraction)
    ksei_inst_d3m           NUMERIC,     -- 3-month change in %local-institution
    ksei_indiv_d3m          NUMERIC,     -- 3-month change in %local-individual (retail)
    quadrant                TEXT,        -- AKUMULASI | MARKUP | DISTRIBUSI | MARKDOWN | NETRAL
    data_flags              TEXT[] NOT NULL DEFAULT '{}',  -- CROSSING-TINGGI, KSEI-DIVERGEN, SHARE_COUNT_CHANGE, RECON_FAIL, ...
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker)
);

CREATE TABLE IF NOT EXISTS signal_log (
    session_date    DATE NOT NULL,
    ticker          TEXT NOT NULL,
    tag             TEXT NOT NULL,
    layer           TEXT NOT NULL DEFAULT 'probation',   -- 'probation' | 'weighted'
    close           NUMERIC,
    ret_h1          NUMERIC,
    ret_h3          NUMERIC,
    ret_h5          NUMERIC,
    ret_h10         NUMERIC,
    ret_vs_ihsg_h5  NUMERIC,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, tag)
);

-- Equal-weight universe forward returns per trading day — the benchmark a tag's
-- edge is measured against (IHSG is large-cap-weighted, not the tradeable
-- universe). Filled by `vg backfill-outcomes`. `*_liquid` = restricted to names
-- that traded > LIQUID_VALUE_IDR that day.
CREATE TABLE IF NOT EXISTS market_breadth (
    session_date    DATE PRIMARY KEY,
    n_all           INT,
    ret_h1_all      NUMERIC,
    ret_h3_all      NUMERIC,
    ret_h5_all      NUMERIC,
    ret_h10_all     NUMERIC,
    hit_h5_all      NUMERIC,
    n_liquid        INT,
    ret_h5_liquid   NUMERIC,
    hit_h5_liquid   NUMERIC,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outcome v2 (audit 2026-11-07, src/vg/outcomes2.py): entry at the VWAP of t+1 (the signal only exists after the close),
-- total-return basis from IDX's own reference-price resets (splits, bonus, rights) + cash-dividend factors. ret2c_h5 = the
-- same basis entered at the signal-day close, for comparison with v1. ret2_at = when the row was resolved (also when it got
-- no entry because nobody traded on t+1). v1 columns above are never touched by v2.
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2_h1  NUMERIC;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2_h3  NUMERIC;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2_h5  NUMERIC;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2_h10 NUMERIC;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2c_h5 NUMERIC;
ALTER TABLE signal_log ADD COLUMN IF NOT EXISTS ret2_at  TIMESTAMPTZ;

-- The v2 benchmark: equal-weight universe (every stock that traded on the signal day), SAME formula as signal_log.ret2_*.
CREATE TABLE IF NOT EXISTS market_breadth2 (
    session_date    DATE PRIMARY KEY,
    n_all           INT,
    ret2_h1_all     NUMERIC,
    ret2_h3_all     NUMERIC,
    ret2_h5_all     NUMERIC,
    ret2_h10_all    NUMERIC,
    hit2_h5_all     NUMERIC,
    ret2c_h5_all    NUMERIC,
    n_liquid        INT,
    ret2_h5_liquid  NUMERIC,
    hit2_h5_liquid  NUMERIC,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outcome v2 for EVERY stock-day that traded (not only signalled ones), same formula as signal_log.ret2_*. Rebuilt whole by
-- `vg backfill-outcomes2`; market_breadth2 is its daily average and research.py's size x liquidity buckets read it.
CREATE TABLE IF NOT EXISTS ret2_daily (
    session_date    DATE NOT NULL,
    ticker          TEXT NOT NULL,
    value           NUMERIC,
    r1              NUMERIC,
    r3              NUMERIC,
    r5              NUMERIC,
    r10             NUMERIC,
    rc5             NUMERIC,
    PRIMARY KEY (session_date, ticker)
);

-- Every tag as FIRST computed (audit 2026-11-07, B13). `compute` re-runs the last ~5 days every night and rewrites the
-- unresolved signal_log rows with data that arrived later (e.g. a KSEI month published days after the session), so
-- signal_log cannot tell what was known at the time. This table only ever gains rows (INSERT ... ON CONFLICT DO NOTHING,
-- never updated or deleted except its ret2_* outcome columns): captured_at is when the tag first appeared. The forward
-- test counts a tag only if it was captured before 08:00 WIB of the next calendar day (before the next open), from
-- engine.FREEZE_SINCE on. Outcome columns are filled by `vg backfill-outcomes2` exactly like signal_log.ret2_*.
CREATE TABLE IF NOT EXISTS signal_snapshot (
    session_date    DATE NOT NULL,
    ticker          TEXT NOT NULL,
    tag             TEXT NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ret2_h1         NUMERIC,
    ret2_h3         NUMERIC,
    ret2_h5         NUMERIC,
    ret2_h10        NUMERIC,
    ret2c_h5        NUMERIC,
    ret2_at         TIMESTAMPTZ,
    PRIMARY KEY (session_date, ticker, tag)
);

-- Real free float per issuer per month, from the monthly registry report (LBRE) each company files with IDX (src/vg/freefloat.py,
-- `vg collect-free-float`). free_float_shares excludes controller, affiliates, treasury, lock-up etc. (listing rule I-A).
-- first_announced_at = when the month was FIRST published (a later KOREKSI replaces the figures but not this time), so a
-- session only uses a month once it was public. consistent = the stated % matches shares / listed within rounding.
CREATE TABLE IF NOT EXISTS free_float_monthly (
    ticker                  TEXT NOT NULL,
    month_end               DATE NOT NULL,
    free_float_shares       NUMERIC NOT NULL,
    listed_shares           NUMERIC NOT NULL,
    free_float_pct          NUMERIC NOT NULL,
    prev_free_float_shares  NUMERIC,
    prev_listed_shares      NUMERIC,
    holders_sid             INT,
    consistent              BOOLEAN NOT NULL,
    is_correction           BOOLEAN NOT NULL DEFAULT false,
    no_pengumuman           TEXT NOT NULL,
    announced_at            TIMESTAMP NOT NULL,          -- IDX announcement time (WIB, as IDX gives it)
    first_announced_at      TIMESTAMP NOT NULL,
    pdf_url                 TEXT,
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, month_end)
);

-- Data-quality flags on signals whose INPUT was known to be broken at the time (src/vg/flags.py, `vg flag-signals`; rebuilt
-- whole). signal_log is never changed: evaluations show the edge with and without the flagged rows.
CREATE TABLE IF NOT EXISTS signal_flags (
    session_date    DATE NOT NULL,
    ticker          TEXT NOT NULL,
    tag             TEXT NOT NULL,
    flags           TEXT[] NOT NULL,
    PRIMARY KEY (session_date, ticker, tag)
);

-- ---------------------------------------------------------------------------
-- Operational
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_artifacts (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    logical_date DATE,                      -- the market date the artifact is about
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INT NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, logical_date, sha256)
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id        BIGSERIAL PRIMARY KEY,
    phase         TEXT NOT NULL,            -- 'collect_price', 'collect_index', ...
    batch_date    DATE,
    status        TEXT NOT NULL,            -- 'ok' | 'no_data' | 'date_mismatch' | 'blocked' | 'error'
    rows_affected INT NOT NULL DEFAULT 0,
    error         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_run_log_phase_date ON run_log (phase, batch_date DESC);

-- ---------------------------------------------------------------------------
-- Additive migrations — `CREATE TABLE IF NOT EXISTS` above does not alter an
-- existing table, so new columns on old databases go here. All idempotent.
-- ---------------------------------------------------------------------------

ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS ksei_foreign_d3m NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS ksei_inst_d3m    NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS ksei_indiv_d3m   NUMERIC;

-- Phase 1.25 — signals from price_bars fields the engine didn't use before.
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS avg_ticket        NUMERIC;  -- value / frequency (IDR per trade)
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS avg_ticket_z      NUMERIC;  -- z vs own trailing ~20d
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bid_offer_imb     NUMERIC;  -- (bidvol - offervol) / (bidvol + offervol) at close
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS float_ratio       NUMERIC;  -- tradable_shares / listed_shares
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS foreign_float_5d  NUMERIC;  -- 5d foreign net shares / tradable_shares

-- Ownership-disclosure filing counts (disclosure_filings), trailing trading-day windows.
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS disclosure_5d     SMALLINT;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS disclosure_20d    SMALLINT;

-- Phase 1.5 — the broker-summary feed (vektorgauss) carries per-side transaction
-- FREQUENCY, which the original broker_flows shape omitted. freq enables avg-ticket
-- (buy_val/buy_freq) — the "positioning vs churn" signal. net_* stored as reported.
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS buy_freq  NUMERIC;
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS sell_freq NUMERIC;
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS net_val   NUMERIC;
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS net_vol   NUMERIC;

-- Broker-house foreign flow signals (engine.py). Foreign-BROKER net (IDR) is a
-- different measure than the domicile-based nff_* columns above — cross-checked,
-- never summed. bkr_cr3 = top-3 net buyers / all net buyers (0..1).
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_foreign_net_d0 NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_foreign_net_5d NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_cr3            NUMERIC;

-- Heuristic participant classifier (INFERENCE — see RETAIL_BROKERS). Foreign-inst
-- vs domestic-retail net (IDR), top-buyer concentration, and who the dominant
-- net-buyer ("bandar candidate") broker is.
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_foreign_inst_net NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_dom_retail_net   NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_cr1              NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_top_buyer        TEXT;

-- ---------------------------------------------------------------------------
-- ARSITEKTUR-DATA-v1 Phase 0 (K1/K2). Idempotent, like everything above.
-- ---------------------------------------------------------------------------
-- Lineage: which raw bytes + which parser produced a row (find a bad parser version, re-parse the raw).
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS raw_sha        TEXT;
ALTER TABLE broker_flows ADD COLUMN IF NOT EXISTS parser_version TEXT;

-- The feed's ranked GROSS ladder (top-25 buyers and top-25 sellers). `broker_flows` only ever held the
-- top-20 NET buyers, so the sell side — and therefore any distribution signal — was invisible.
CREATE TABLE IF NOT EXISTS broker_levels (
    session_date   DATE NOT NULL,
    ticker         TEXT NOT NULL,
    side           CHAR(1) NOT NULL CHECK (side IN ('B', 'S')),   -- gross Buyer | gross Seller
    rank           SMALLINT NOT NULL,                              -- 1 = largest on that side
    broker_code    TEXT NOT NULL,
    broker_name    TEXT,
    val            NUMERIC,                                        -- IDR gross on that side
    vol            NUMERIC,
    freq           NUMERIC,
    avg_price      NUMERIC,
    source         TEXT NOT NULL,
    source_tier    SMALLINT NOT NULL,
    raw_sha        TEXT,
    parser_version TEXT,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, side, rank)
);
CREATE INDEX IF NOT EXISTS idx_broker_levels_ticker ON broker_levels (ticker, session_date);

-- Certain-bounds foreign broker net (see engine._foreign_net_bounds). NULL = ladder not available.
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_foreign_net_lo NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bkr_foreign_net_hi NUMERIC;

-- Broker scoring (src/vg/engine/scoring.py), PROBATION / weight 0. All nullable, additive: existing columns and the
-- primary key are untouched. NULL = the inputs for that measure did not exist that day (no ladder, no done rows, ...).
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bpi3             NUMERIC;   -- Bandar Power Index, top-3: cr3_buy - cr3_sell, in [-1, 1]
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bpi5             NUMERIC;   -- ... top-5
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS bpi_overlap3     SMALLINT;  -- brokers in BOTH the top-3 buyers and top-3 sellers (0-3)
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS aggr_haka_share  NUMERIC;   -- HAKA / (HAKA + HAKI), regular board, in [0, 1]
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS aggr_ratio       NUMERIC;   -- HAKA / HAKI (NULL when HAKI = 0)
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS aggr_coverage    NUMERIC;   -- (HAKA + HAKI) / qty: share of volume that carries an aggressor flag
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS retail_net_lo    NUMERIC;   -- certain bounds on the retail-broker cohort's net (IDR)
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS retail_net_hi    NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS finst_net_lo     NUMERIC;   -- ... and on foreign institutions (foreign houses that are not retail)
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS finst_net_hi     NUMERIC;
ALTER TABLE signal_components ADD COLUMN IF NOT EXISTS retail_div_state TEXT;      -- retail_buying_institutions_selling | ... | indeterminate

-- K3: price adjustment from corporate actions (src/vg/adjust.py). price_bars stays RAW.
CREATE TABLE IF NOT EXISTS adj_factor (
    ticker     TEXT NOT NULL,
    ex_date    DATE NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('split', 'bonus', 'dividend')),
    factor     NUMERIC NOT NULL CHECK (factor > 0),     -- multiply bars BEFORE ex_date by this
    detail     TEXT,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ex_date, kind)
);
-- Only bars that precede at least one event have a row; every other bar implicitly has factor 1.
CREATE TABLE IF NOT EXISTS price_adj (
    session_date  DATE NOT NULL,
    ticker        TEXT NOT NULL,
    split_factor  NUMERIC NOT NULL,   -- splits + bonus issues only (price continuity)
    total_factor  NUMERIC NOT NULL,   -- + cash dividends (total-return basis)
    PRIMARY KEY (session_date, ticker)
);
CREATE OR REPLACE VIEW price_bars_adj AS
SELECT p.session_date, p.ticker, p.close,
       COALESCE(a.split_factor, 1) AS split_factor,
       COALESCE(a.total_factor, 1) AS total_factor,
       p.close * COALESCE(a.split_factor, 1) AS close_adj,
       p.close * COALESCE(a.total_factor, 1) AS close_tr
FROM price_bars p LEFT JOIN price_adj a USING (session_date, ticker);

-- K5: why a price row was touched after ingest (src/vg/repair.py). NULL = untouched.
ALTER TABLE price_bars ADD COLUMN IF NOT EXISTS quality_note TEXT;

-- Slow, rarely-changing analyses computed offline (e.g. `vg risk-eval --save`) and read by the dashboard, so a page view
-- never triggers a multi-minute computation. One row per analysis name; the payload is self-describing JSON.
CREATE TABLE IF NOT EXISTS analysis_snapshot (
    name         TEXT PRIMARY KEY,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB NOT NULL
);

-- Running-trade stream (`vg record-trades`, src/vg/tradeflow.py): every match order the exchange reported, summed per stock per
-- minute. The feed keeps no history, so this table only has the days the recorder was running. `minute` is minutes since 00:00
-- exchange time (09:15 = 555). buy/sell is the action the feed labels each trade with (documented as the aggressive side by the tick
-- rule - not yet verified here against done-details). Values are rupiah, lots are 1 lot = 100 shares.
CREATE TABLE IF NOT EXISTS trade_flow_1m (
    session_date DATE NOT NULL,
    ticker       TEXT NOT NULL,
    minute       SMALLINT NOT NULL,
    n_buy        INTEGER NOT NULL DEFAULT 0,
    n_sell       INTEGER NOT NULL DEFAULT 0,
    buy_lot      NUMERIC NOT NULL DEFAULT 0,
    sell_lot     NUMERIC NOT NULL DEFAULT 0,
    buy_val      NUMERIC NOT NULL DEFAULT 0,
    sell_val     NUMERIC NOT NULL DEFAULT 0,
    max_val      NUMERIC NOT NULL DEFAULT 0,      -- the biggest single trade in the minute
    px_high      NUMERIC NOT NULL,
    px_low       NUMERIC NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, ticker, minute)
);
CREATE INDEX IF NOT EXISTS idx_trade_flow_1m_day ON trade_flow_1m (session_date, minute);

-- The user's own watchlist, kept from the dashboard (/today). It starts EMPTY and is only ever filled by the user: nothing is added from
-- configuration. VG_WATCHLIST in .env is a different thing (which stocks the nightly collection pulls tick data for).
CREATE TABLE IF NOT EXISTS user_watchlist (
    ticker    TEXT PRIMARY KEY,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- COMPLETE per-broker table (feed all_data=true): every broker that traded the stock that day, so buy value = sell value for the day.
-- `broker_flows` keeps only the 20 biggest NET buyers (no sellers) and feeds the BROKER-* signals; this table feeds the broker stalker and
-- inventory pages only, and nothing computed from `broker_flows` reads it. Collected by `vg collect-broker-full`.
CREATE TABLE IF NOT EXISTS broker_flows_full (
    session_date  DATE NOT NULL,
    ticker        TEXT NOT NULL,
    broker_code   TEXT NOT NULL,
    side_flag     TEXT,                     -- 'D' | 'F' from the static foreign-broker list
    buy_val       NUMERIC,
    sell_val      NUMERIC,
    buy_vol       NUMERIC,
    sell_vol      NUMERIC,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, session_date, broker_code)
);
CREATE INDEX IF NOT EXISTS broker_flows_full_broker ON broker_flows_full (broker_code, session_date);
CREATE INDEX IF NOT EXISTS broker_flows_full_day ON broker_flows_full (session_date);

