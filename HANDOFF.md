# Handoff — status & next steps

_State as of commit `88552a7` (2026-09-03)._

## Where things stand

**Phase 1 code is complete.** 7 commits on `main`. ~49 offline tests exist but
have **never been run** (no Python on the dev machine — run `pytest` on the VPS
first). No VPS, no database, no data yet.

Decisions locked in (see `RENCANA-FASE-1.md`, `PRINSIP-DATA.md`):
- Personal tool, not sold. 100% Tier-0 data (IDX Stock/Index Summary + KSEI).
  No broker summary → no CR3 / Non-Retail / Sultan / Zombie in Phase 1.
- 2-tier methodology: every signal is weight-0 "probation"; the forward test
  (`vg eval`) decides if any tag has real edge. **No composite score.**
- Digest carries trade calls (action + ATR stop-loss + R-target + lot size),
  gated by `VG_DIGEST_TRADE_CALLS`. **Must be reworked if ever sold (OJK).**

## What YOU do next (not code — one-time setup)

1. **VPS.** ~$5/mo, 2 GB RAM. **Prefer an Indonesian IP** (Biznet Gio,
   IDCloudHost, Dewaweb) — datacenter IPs elsewhere may get Cloudflare-blocked
   by `www.idx.co.id`.
2. `bash scripts/check_idx_access.sh` — confirms IDX + KSEI are reachable from
   that host. If IDX returns 403 / HTML → try another provider/region, or wire
   the `curl_cffi` fallback into `IdxStockSummarySource._get`.
3. Install:
   ```bash
   cp .env.example .env          # fill VG_TELEGRAM_*, VG_WATCHLIST, VG_HOLDINGS, sizing vars
   docker compose up -d db
   python -m venv .venv && . .venv/bin/activate
   pip install -e ".[dev]"
   pytest                        # first real test run — fix anything that fails
   vg init-db
   ```
4. **Telegram bot:** create via @BotFather, get your numeric chat id from
   @userinfobot, put both in `.env`.
5. **Backfill** (polite, ~4 s/day — a 2-year price backfill takes ~40 min):
   ```bash
   vg collect-price     --backfill 2024-01-02 <today>
   vg collect-index     --backfill 2024-01-02 <today>
   vg collect-ownership --backfill 2021-01 <this-month>
   vg collect-corpactions --backfill 2021 <this-year>
   vg detect-ca
   vg compute           --backfill 2024-02-01 <today>   # needs ~25 prior price days
   vg backfill-outcomes
   vg digest --dry-run                                  # eyeball it
   ```
6. **Cron** (VPS timezone = WIB, or offset): the daily EOD block + monthly +
   weekly lines are in `README.md` → "Daily EOD sequence".
7. **Leave it running ~2 months.** Paper-trade `AKUM-ASING` in parallel, keep a
   manual spreadsheet. Read `vg digest --weekly` / `vg eval`.

## Decision point (after ~2 months of `vg eval`)

- A tag shows consistent edge vs IHSG after ~150–190 bp round-trip costs, n≥50 →
  promote it: set its `layer='weighted'` and start acting on it. (No weighting
  code yet — it's a small addition when the data justifies it.)
- Nothing shows edge → the free-data methodology isn't enough. Either stop, or
  go **Phase 1.5**: buy per-ticker broker summary (Index Alpha Rp200k/mo, or
  Sectors Insider $59/mo which also bundles KSEI ownership + corp actions +
  `cohort` classification). Then CR3, institution-vs-retail, and NeoBDM-style
  participant tags become possible — as new probation signals, forward-tested
  the same way.

## Backlog (Phase 2 — only if going commercial)

- Web dashboard (Next.js). Includes a **position-sizing calculator** like
  `neobdm.tech/calculator/` — the maths is already done in `src/vg/calc.py`,
  just needs a UI.
- Licensed data feed (Tier 1 redistributor) to replace any scraped source.
- Multi-user, auth, QRIS payments, PT + UU PDP compliance, disclaimer.
- Rework or remove digest trade calls (OJK — "not investment advice").

## Gotchas for a future coding session

- **Test before touching:** `pytest` on the VPS. The Windows dev box has no
  Python, so past edits were reviewed by hand only.
- IDX foreign buy/sell is **volume (shares)**, and it's **UNVERIFIED** whether
  it's all-boards or regular-only (`PRINSIP-DATA.md` §A). Flag before trusting.
- KSEI month-end file date shifts for holidays — `KseiOwnershipSource` tries the
  last weekday then walks back 9 days.
- KSEI's `schedule_ca_keseluruhan` has **no stock splits** — those come from
  `detect-ca` (listed_shares jumps). `mconv_*.xls` = bond conversions, not splits.
- Engine thresholds live in `src/vg/engine.py` module constants. Don't hand-tune
  them to look good on history — that's the exact mistake the Gemini blueprint
  made (`ANALISIS-VALIDASI.md` C4). Let `vg eval` drive them.
