# Vektor Gauss

Personal capital-flow / bandarmology research tool for the Indonesian Stock Exchange (IDX).
**Phase 1** = your own alert tool, EOD, official sources only.

> Not investment advice. A tool for reading public market data.

## Documents (read in this order)

| Doc | What |
|---|---|
| [RENCANA-FASE-1.md](RENCANA-FASE-1.md) | Phase 1 plan — scope, methodology (2-tier + forward-test), schema, Telegram |
| [PRINSIP-DATA.md](PRINSIP-DATA.md) | **Binding.** Source tiers + 9 anti-manipulation checks |
| [RENCANA-FASE-2.md](RENCANA-FASE-2.md) | Phase 2 design (held) — product concept, data inventory, GitHub/Cloudflare infra, web architecture |
| [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) | Critique of the original Gemini blueprint |
| [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md) | IDX + KSEI data source inventory |
| [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md) · [IDXALPHA-TEARDOWN.md](IDXALPHA-TEARDOWN.md) · [SECTORS-API.md](SECTORS-API.md) · [PERTANYAAN-IDX-KSEI.md](PERTANYAAN-IDX-KSEI.md) | Landscape research |

## What's built so far

- `db/schema.sql` — full canonical schema (12 tables; several unused in Phase 1).
- **IDX Stock Summary collector** (`sources/idx.py`, Tier 0): OHLCV + foreign flow
  (volume) + regular/negotiated split, per ticker per day → `price_bars`.
- **IDX Index Summary collector** (`sources/idx.py`, Tier 0): ~45 indices/day,
  `COMPOSITE` = IHSG → `index_bars`.
- **KSEI monthly ownership collector** (`sources/ksei.py`, Tier 0): `BalanceposEfek`
  month-end zip → `ownership_monthly` (9 investor categories × local/foreign +
  scripless %).
- **Corporate actions** (`sources/ksei.py` + `collect.py`, Tier 0):
  KSEI `schedule_ca_keseluruhan` (dividends / rights / stock dividends / mergers)
  **plus** a `derived_from_price_bars` detector for splits/bonus (KSEI omits splits)
  → `corp_actions`.
- **Engine v0** (`src/vg/engine.py`) — RENCANA-FASE-1 §5-6, PRINSIP-DATA §C.
  Per trading day, per ticker: NFF (net foreign flow, **in IDR**, split-robust)
  D0 + rolling W1-W4 on trading days, foreign-buy consistency, vol surge, RS vs
  IHSG (split-adjusted), negotiated-board ratio, KSEI monthly trend cross-check →
  `signal_components`. Fires **probation tags** (`AKUM-ASING`, `DISTRIBUSI-ASING`,
  `MARKUP`, `MARKDOWN`, `CROSSING-TINGGI`, `KSEI-KONFIRM`/`KSEI-DIVERGEN`) into
  `signal_log` — **weight 0**. `backfill-outcomes` fills their H+1/3/5/10 returns;
  `eval` reports whether any tag actually precedes a move. No composite score.
- **Telegram digest** (`telegram.py` + `digest.py`): `vg digest` sends an EOD
  digest to your own chat — watchlist lines (price, NFF D0/W1, foreign
  consistency, quadrant, tag, ⚠ data flags), **trade calls** (action + ATR
  stop-loss + R-multiple target + lot size, gated by `VG_DIGEST_TRADE_CALLS`),
  a RADAR list of new `AKUM-ASING` names, freshness warnings. `--weekly` sends
  the `eval` table.
- **`vg calc`** (`src/vg/calc.py`) — position-sizing calculator (the logic behind
  NeoBDM's calculator; the Phase 2 web version wraps this): entry(ies) + stop-loss
  → lots for a fixed % account risk, R:R, position value.
- `src/vg/collect.py` + `vg` CLI — fetch → raw artifact (+sha256) → upsert → `run_log`.

Phase 1 code is complete. Remaining: run it on a VPS for ~2 months, read `vg eval`.

## Setup (on the VPS)

```bash
# 0. FIRST — verify IDX/KSEI are reachable from this host (Cloudflare check)
bash scripts/check_idx_access.sh

# 1. Postgres
cp .env.example .env            # adjust if needed
docker compose up -d db

# 2. Python
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# 3. Schema (also auto-applied on first `docker compose up`)
vg init-db

# 4. Collect
vg collect-price --latest
vg collect-price --date 2026-09-02
vg collect-price --backfill 2024-01-02 2026-09-02       # ~4s/day, polite

vg collect-ownership --latest                            # this + last month
vg collect-ownership --month 2026-08
vg collect-ownership --backfill 2021-01 2026-08          # KSEI files exist ~2018+

vg collect-index --latest                                # IHSG + ~44 others
vg collect-index --backfill 2024-01-02 2026-09-02

vg collect-corpactions --year 2026                       # KSEI CA schedule
vg collect-corpactions --backfill 2021 2026
vg detect-ca                                             # splits from listed_shares jumps (needs price_bars first)

vg compute --latest                                      # engine v0 -> signal_components + probation tags
vg compute --backfill 2024-01-02 2026-09-02
vg backfill-outcomes                                     # forward returns on resolved tags + refresh market_breadth
vg eval                                                  # per-tag edge vs the equal-weight universe (date-matched) — the Phase 1 payoff

vg digest --dry-run                                      # preview the Telegram digest
vg digest                                                # send it (needs VG_TELEGRAM_* in .env)
vg digest --weekly                                       # the eval table

vg calc --entry 3390 --sl 3180 --target-r 2.5 --balance 100000000 --risk 1.0

vg season COMPOSITE --years 5      # day-of-week + monthly seasonality (thin n — read `n`)
vg ownership BBRI --months 12      # KSEI monthly %foreign / %local-inst / %local-indiv + scripless %
```

### Daily EOD sequence (cron ~19:00 WIB)

```
vg collect-price --latest
vg collect-index --latest
vg detect-ca                # cheap; catches splits since yesterday
vg compute --latest
vg backfill-outcomes
vg digest
# (monthly, early in the month) vg collect-ownership --latest ; vg collect-corpactions --year <this year>
# (weekly, e.g. Mon)            vg digest --weekly
```

`collect-ownership` downloads `web.ksei.co.id/Download/BalanceposEfek<YYYYMMDD>.zip`
for the last trading day of the month (tries the last weekday, walks back up to
9 days for Eid / year-end holidays). The file's own `Date` column is the
authoritative `month_end`. `holders_count` is NOT in this file (stays NULL —
sourced later from Sectors `shareholders-composition` or elsewhere).

Run tests (offline, no DB): `pytest`

## Cloudflare note

`www.idx.co.id` sits behind Cloudflare. Tested 2026-09-03 from AWS (Singapore +
Jakarta) and Oracle Cloud (Singapore): **every datacenter/cloud ASN is
blocked** — HTTP 1020 "you have been blocked", which no client or headless
browser clears. KSEI is unaffected.

What works: run the collector from an **Indonesian residential connection**. Even
there, a plain `httpx` request to the `/primary/` JSON endpoint draws a "Just a
moment..." challenge — so `idx.py` uses **`curl_cffi` impersonating Chrome**
(matching TLS/JA3), fetches the trading-summary HTML page first to bank the
`__cf_bm` cookie, then calls the JSON endpoint on the same session, with retry.
`curl-cffi` is a core dependency; one warmed session is reused across a backfill.

Keep the rate low (`VG_REQUEST_DELAY_SECONDS`, default 4; you fetch once/day).
Do **not** add proxy rotation / challenge-solving. If a source blocks you, change
source — see [PRINSIP-DATA.md](PRINSIP-DATA.md).

## Endpoint reference (verified 2026-09-03)

```
GET https://www.idx.co.id/primary/TradingSummary/GetStockSummary?length=9999&start=0&date=YYYYMMDD
```
- Omit `date` → latest published day.
- `{"data": [{ "StockCode", "Close", "Value", "ForeignBuy", "ForeignSell",
   "NonRegularValue", "ListedShares", "TradebleShares", "Remarks", ... }]}` (~963 rows).
- **Unverified:** whether `ForeignBuy`/`ForeignSell` are all-boards or regular-only
  (flag in `PRINSIP-DATA.md` §A). Confirm before using foreign flow as a signal.
