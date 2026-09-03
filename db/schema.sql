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

CREATE TABLE IF NOT EXISTS index_bars (
    session_date  DATE NOT NULL,
    index_code    TEXT NOT NULL,            -- 'COMPOSITE' (IHSG), 'LQ45', sector codes...
    close         NUMERIC,
    source        TEXT NOT NULL,
    source_tier   SMALLINT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, index_code)
);
-- Not populated in Phase 1 scaffold — next collector.

-- ---------------------------------------------------------------------------
-- Ownership (KSEI, Tier 0) — monthly. Not populated in Phase 1 scaffold.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ownership_monthly (
    month_end           DATE NOT NULL,
    ticker              TEXT NOT NULL,
    local_individual    NUMERIC,
    local_institution   NUMERIC,
    foreign_total       NUMERIC,
    detail              JSONB,              -- full per-investor-category breakdown
    holders_count       NUMERIC,
    source              TEXT NOT NULL,
    source_tier         SMALLINT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (month_end, ticker)
);

CREATE TABLE IF NOT EXISTS corp_actions (
    ticker      TEXT NOT NULL,
    ex_date     DATE NOT NULL,
    type        TEXT NOT NULL,              -- 'stock_split', 'reverse_split', 'bonus', 'rights', 'stock_dividend', ...
    ratio       NUMERIC,                    -- new : old adjustment factor where applicable
    notes       TEXT,
    source      TEXT NOT NULL,
    source_tier SMALLINT NOT NULL,
    PRIMARY KEY (ticker, ex_date, type)
);

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
-- Engine output (Phase 1 §5–6). Not populated in Phase 1 scaffold.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS signal_components (
    session_date            DATE NOT NULL,
    ticker                  TEXT NOT NULL,
    nff_d0                  NUMERIC,
    nff_w1                  NUMERIC,
    nff_w2                  NUMERIC,
    nff_w4                  NUMERIC,
    foreign_consistency_10d SMALLINT,
    vol_surge               NUMERIC,
    rs_ihsg_5d              NUMERIC,
    rs_ihsg_20d             NUMERIC,
    nonreg_ratio            NUMERIC,
    ksei_foreign_trend_3m   TEXT,
    ksei_inst_trend_3m      TEXT,
    quadrant                TEXT,
    data_flags              TEXT[] NOT NULL DEFAULT '{}',  -- RECON_FAIL, CROSSING-TINGGI, KSEI-DIVERGEN, SOURCE_CONFLICT, SUSPECT_WASH
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
