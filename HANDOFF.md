# Handoff — status & next steps

_State as of 2026-09-03 (session 2). Phase 1 is **running**._

## Where things stand

**Phase 1 is live on the user's home PC.** IDX's Cloudflare blocks every cloud
ASN (AWS Singapore + Jakarta, Oracle Cloud — all HTTP 1020), so the collector
runs from an Indonesian residential connection instead of a VPS.

Runtime: **WSL2 Ubuntu 24.04** on the Windows 11 PC.
- `/home/vg/vektor-gauss` — repo + `.venv` (system Python 3.12), `pip install -e ".[dev]"`.
- **PostgreSQL 16** native (apt), db `vektorgauss`, role `vg` / `vg`.
- `.env`: DB url, UA, `VG_REQUEST_DELAY_SECONDS=5`, Telegram bot `@vektorgaussbot`
  token + chat id `8795612441`. Watchlist `BBRI,BBCA,ADRO,ANTM,TLKM`, holdings
  empty, `VG_ACCOUNT_BALANCE=0` (→ no lot-size line yet).
- **IDX transport** = `curl_cffi` impersonating Chrome (commit `324e251`): plain
  httpx always draws the "Just a moment" challenge, even from the home IP.
- **Cron** = Windows Task Scheduler `VektorGauss-EOD`, daily **19:15 WIB**,
  `StartWhenAvailable` (catches runs missed while the PC was off). Runs
  `/home/vg/vg-eod.sh` = rolling 5-day collect+compute (self-heals gaps) +
  monthly (day ≤5) ownership & corp actions + Monday `digest --weekly` + digest.
- **Backfill** (`/home/vg/backfill.sh`): price+index 2023-01-02 → now, ownership
  2021-01 → 2026-08, corp actions 2021–2026, then detect-ca + compute +
  backfill-outcomes. Re-runnable (idempotent) if interrupted.

Methodology unchanged (`RENCANA-FASE-1.md`, `PRINSIP-DATA.md`): 100% Tier-0 data,
every signal is weight-0 "probation", `vg eval` decides edge, **no composite
score**. Digest trade calls gated by `VG_DIGEST_TRADE_CALLS` (rework/​disable if
ever sold — OJK).

## What's left

1. ~~Let the backfill finish, then read `vg eval`.~~ Done — see
   **First `vg eval` read** below. Real test is still the forward run.
2. **Tune `.env`**: real watchlist, `VG_HOLDINGS` (drives HOLD/REDUCE vs BUY),
   `VG_ACCOUNT_BALANCE` + `VG_RISK_PCT` for lot sizing in the digest. Also set
   **`VG_FORWARD_SINCE`** to today's date once the cron is steady — it splits
   `vg eval` / the weekly digest into an in-sample and a forward-only block, the
   actual instrument for the gate. (`VG_RADAR_TAGS` defaults to the two live
   candidates now; override only to change them.)
3. **Confirm the first cron run** (19:15 WIB) lands a digest in Telegram. Then
   migrate the cron to GitHub Actions — see `INFRA-GITHUB-ACTIONS.md` (register
   the self-hosted runner, run both in parallel a week, disable Task Scheduler).
4. **Leave it running ~2 months.** Paper-trade the surviving candidates
   (`FLOAT-ASING-PADAT`, `TIKET-BESAR-AKUM`; `TIKET-KECIL-MARKUP` as an avoid) on
   the watchlist in parallel, keep a manual spreadsheet. Read `vg digest
   --weekly` / `vg eval`. Watch whether the forward edge survives out-of-sample.

## First `vg eval` read (2026-09-03, in-sample)

Full 2023-02 → 2026-09 history recomputed after Phase 1.25, so **there is no
genuine forward window yet** — every number here is in-sample. `edge` = tag's H5
return minus the equal-weight universe that day; `hit`/`uHit` = % positive for
tag vs universe; `L` = subset on names trading > Rp5B that day.

**The flagship tag still has no edge.** `AKUM-ASING`: edgeH5 −0.07%, edgeH10
−0.12%, hit 43% vs uHit 40%, liquid subset only +0.15%. Same verdict as the 3.5y
research pass — free foreign-flow accumulation is not tradeable.

**Phase 1.25 earned its keep — 2 of its 3 new tags are the best in the table:**

| tag | n | edgeH1 | edgeH5 | edgeH10 | hit/uHit | edgeH5 L |
|---|---|---|---|---|---|---|
| `FLOAT-ASING-PADAT` | 3,552 | +0.27% | **+0.63%** | +0.41% | **46 / 39** | **+0.92%** |
| `TIKET-BESAR-AKUM` | 36,984 | +0.13% | +0.36% | +0.68% | 42 / 39 | +0.40% |
| `TIKET-KECIL-MARKUP` | 16,687 | **−0.34%** | −0.21% | −0.01% | 41 / 41 | +0.20% |

- `FLOAT-ASING-PADAT` — best edge, only tag with a real hit-rate gap (46 vs 39),
  naturally concentrated in liquid names (2,651 / 3,552). But n ≈ 1k/yr → thin.
- `TIKET-BESAR-AKUM` — the avg-ticket (value/frequency) institutional-footprint
  proxy. Edge rises monotonically H1→H10 on a large sample. Modest but the most
  consistent shape.
- `TIKET-KECIL-MARKUP` — works as a **negative** signal (−0.34% at H1). "Retail
  crowd, fragile" thesis holds in-sample. Avoid filter, not a buy.

**Theses contradicted by the data:** `DISTRIBUSI-ASING` is meant to be bearish
but shows +0.40% edgeH5 / +0.90% edgeH10 — it fires on "price up 5d" and is
really just momentum, same as `MARKUP`. Treat both as price-momentum proxies, not
foreign-behaviour signals. `CROSSING-TINGGI`: +0.28% avg edge but hit-rate 38%
(lowest) — a few tail outliers carry it, fragile.

**Bottom line — verdict unchanged, not yet tradeable:**
- Every "working" tag lands at or below the 150–190 bp round-trip cost band.
  `FLOAT-ASING-PADAT` liquid (+0.92% H5) clears one-way, not round-trip;
  `TIKET-BESAR-AKUM` (+0.40%) is well under.
- Every hit-rate is < 50% (38–46%) — positive mean, negative median, carried by
  tail winners. Hard to size discretionarily.
- But Phase 1.25 produced two long-side forward-test candidates that beat the
  original `AKUM-ASING`: **`FLOAT-ASING-PADAT`** and **`TIKET-BESAR-AKUM`**, plus
  **`TIKET-KECIL-MARKUP`** as a negative. Those three are what to watch once real
  forward observations accumulate.

## Decision point (after ~2 months of forward `vg eval`)

- A tag shows consistent edge vs IHSG after ~150–190 bp round-trip costs, n≥50 →
  promote it: `layer='weighted'`, start acting on it. (No weighting code yet —
  small addition when the data justifies it.)
- Nothing shows edge → the free-data methodology isn't enough. Either stop, or go
  **Phase 1.5**: buy per-ticker broker summary (Index Alpha Rp200k/mo, or Sectors
  Insider $59/mo which also bundles KSEI ownership + corp actions + `cohort`).
  Then CR3, institution-vs-retail, NeoBDM-style participant tags become possible
  — as new probation signals, forward-tested the same way.

This decision point is the **gate** for everything in `RENCANA-FASE-2.md` (product
concept, GitHub/Cloudflare infra, web architecture). None of it is built until a
tag clears here. `FLOAT-ASING-PADAT` / `TIKET-BESAR-AKUM` are the current candidates
(see "First `vg eval` read" above).

## Migrating to an Indonesian ISP VPS (when ready)

The stack was built Postgres-from-the-start for this. On the VPS: install the
toolchain, `git clone`, `pip install -e .`, `pg_dump` from WSL → `pg_restore`,
copy `.env`, move the cron to system `cron`. Nothing in the code changes. A
residential-class ID ISP (Biznet Gio, IDCloudHost, Dewaweb) is required — a
datacenter IP will hit the same Cloudflare 1020 wall.

## Operating it (home PC)

```bash
# check today's run
wsl -d Ubuntu-24.04 -- tail -40 /home/vg/vektor-gauss/eod.log
# re-run the backfill if it was interrupted (idempotent)
wsl -d Ubuntu-24.04 -- bash /home/vg/backfill.sh
# manual digest preview
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/vektor-gauss && . .venv/bin/activate && vg digest --dry-run'
# forward-test table
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/vektor-gauss && . .venv/bin/activate && vg eval'
```

## Gotchas for a future coding session

- **`pytest` before touching anything** — `cd ~/vektor-gauss && . .venv/bin/activate && pytest` (49 tests, offline).
- IDX only works from the home residential IP. From WSL, plain `curl`/`httpx` is
  challenged; the code path (`curl_cffi`) works. Don't "fix" it back to httpx.
- IDX foreign buy/sell is **volume (shares)**, and it's **UNVERIFIED** whether
  all-boards or regular-only (`PRINSIP-DATA.md` §A).
- KSEI month-end file date shifts for holidays — `KseiOwnershipSource` tries the
  last weekday then walks back 9 days.
- KSEI's `schedule_ca_keseluruhan` has **no stock splits** — those come from
  `detect-ca` (listed_shares jumps). `mconv_*.xls` = bond conversions, not splits.
- Engine thresholds live in `src/vg/engine.py` module constants. Don't hand-tune
  them to look good on history — let `vg eval` drive them.
- The WSL distro must be running for the cron to fire; a `nohup`'d job dies when
  the last WSL session exits — long jobs need an attached `wsl.exe` or a systemd
  unit. The Task Scheduler action holds a session for its own run.
