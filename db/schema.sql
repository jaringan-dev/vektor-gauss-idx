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

-- Phase 1.5 — the broker-summary feed (arjum_idxedge) carries per-side transaction
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
