# Vektor Gauss

Personal capital-flow / bandarmology research tool for the Indonesian Stock Exchange (IDX).
**Phase 1** = your own alert tool, EOD, official sources only.

> Not investment advice. A tool for reading public market data.

## Documents (read in this order)

| Doc | What |
|---|---|
| [RENCANA-FASE-1.md](RENCANA-FASE-1.md) | Phase 1 plan — scope, methodology (2-tier + forward-test), schema, Telegram |
| [PRINSIP-DATA.md](PRINSIP-DATA.md) | **Binding.** Source tiers + 9 anti-manipulation checks |
| [ANALISIS-VALIDASI.md](ANALISIS-VALIDASI.md) | Critique of the original Gemini blueprint |
| [INVENTARIS-SUMBER-DATA.md](INVENTARIS-SUMBER-DATA.md) | IDX + KSEI data source inventory |
| [LANSKAP-KOMPETITOR.md](LANSKAP-KOMPETITOR.md) · [IDXALPHA-TEARDOWN.md](IDXALPHA-TEARDOWN.md) · [SECTORS-API.md](SECTORS-API.md) · [PERTANYAAN-IDX-KSEI.md](PERTANYAAN-IDX-KSEI.md) | Landscape research |

## What's built so far

- `db/schema.sql` — full canonical schema (12 tables; several unused in Phase 1).
- `src/vg/sources/idx.py` — **IDX Stock Summary collector** (Tier 0): OHLCV +
  foreign flow (volume) + regular/negotiated split, per ticker per day.
- `src/vg/sources/ksei.py` — **KSEI monthly ownership collector** (Tier 0): the
  `BalanceposEfek<YYYYMMDD>.zip` month-end file → `ownership_monthly`. Per-ticker
  share counts by investor category (9 categories × local/foreign) + scripless %.
- `src/vg/collect.py` + `vg` CLI — fetch → raw artifact (+sha256) → upsert → `run_log`.

Not yet: IHSG/index collector, corp actions, the engine, Telegram, forward-test.

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
```

`collect-ownership` downloads `web.ksei.co.id/Download/BalanceposEfek<YYYYMMDD>.zip`
for the last trading day of the month (tries the last weekday, walks back up to
9 days for Eid / year-end holidays). The file's own `Date` column is the
authoritative `month_end`. `holders_count` is NOT in this file (stays NULL —
sourced later from Sectors `shareholders-composition` or elsewhere).

Run tests (offline, no DB): `pytest`

## Cloudflare note

`www.idx.co.id` sits behind Cloudflare. From a datacenter IP the JSON endpoint
may return `403` or an HTML challenge. Mitigations (personal scale):

1. Prefer an **Indonesian VPS** (Biznet Gio, IDCloudHost, Dewaweb) — least friction.
2. Keep the rate low (default `VG_REQUEST_DELAY_SECONDS=4`; you only fetch once/day).
3. If still blocked: `pip install -e ".[cffi]"` and (TODO) wire the `curl_cffi`
   fallback into `IdxStockSummarySource._get`.

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
