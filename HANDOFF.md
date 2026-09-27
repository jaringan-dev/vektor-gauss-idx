# Handoff — status & next steps

_State as of 2026-09-27 (session 6). Phase 1 is **running**, forward-test clock is on._

## Status at a glance (read this first)

- **NEW (session 6):** a free per-ticker broker/price/done feed ("`vektorgauss`",
  Tier 3) is wired — broker summary, broker inventory, price-history fallback,
  done-by-price, and a heuristic participant classifier (FOREIGN-INST / DOM-RETAIL
  / BANDAR-CANDIDATE). All probation, forward-tested. See **Session 6 update** below.
  Not pushed — GitHub account suspended; canonical branch is the WSL clone's
  `claude/project-ini-progress-blwa3y`.

- **Gate ("Decision point" below) is not reached yet.** Forward-test clock started
  `VG_FORWARD_SINCE=2026-09-04`. Re-read the forward block of `vg eval` /
  `vg digest --weekly` **on or after ~2026-11-04** (~2 months) before deciding
  anything about paid data or Phase 2. Don't spend money before then.
- **Candidates actually being watched:** `FLOAT-ASING-PADAT`, `TIKET-BESAR-AKUM`
  (long side), `TIKET-KECIL-MARKUP` (avoid filter). `AKUM-ASING` and the raw
  quadrant tags (MARKUP/AKUMULASI/DISTRIBUSI/MARKDOWN) are confirmed **no edge**
  in-sample (3.5y research pass + Phase 1 recompute) — they still fire in the
  digest (legacy/context), but they are not what decides the gate.
- **New candidate as of 2026-09-05: `INSIDER-AKUM`** (Phase B, disclosure PDF
  direction). In-sample n=151, edgeH5 +1.63%, edgeH10 +4.91%, hit 61%/54% —
  the strongest shape in the whole `vg eval` table. n is thin only because
  disclosure collection itself starts 2026-07-01 (not the 3.5y price history);
  it grows daily now that `vg parse-disclosure` runs in `eod.sh`. Still
  probation — same gate, same rules, don't act on it yet. `INSIDER-DIST`/
  `SUBSTANTIAL-AKUM`/`SUBSTANTIAL-DIST` are live too but weaker/mixed in-sample.
- **EOD driver:** **both** Windows Task Scheduler `VektorGauss-EOD` (19:15 WIB) and
  GitHub Actions `eod.yml` (cron 19:15 WIB) are active in parallel as of
  2026-09-05 — the WSL2 VM-doesn't-survive-between-sessions issue that killed the
  first GitHub Actions attempt (2026-09-04) is now fixed via a Windows Startup-folder
  keep-alive script (Task Scheduler creation was blocked by system policy on this
  machine). First `workflow_dispatch` test completed successfully end-to-end.
  **Not yet cut over** — watch a few real scheduled `eod.yml` runs before disabling
  Task Scheduler. Expect duplicate Telegram digests on days both fire until then.
  See `INFRA-GITHUB-ACTIONS.md`.
- **`VG_HOLDINGS`/`VG_ACCOUNT_BALANCE` blank/0 in `.env` is CORRECT, not a gap** —
  confirmed with the user 2026-09-05: no live execution yet, this tool is still
  paper-trading/forward-testing (see HANDOFF #4). Every watchlist ticker digests
  as "not held" (BUY/WATCH/AVOID) and the BUY/ADD line has no lot-size
  suggestion, which is the right behaviour with zero real capital at risk. Only
  fill these in once the user actually starts executing — don't "fix" this
  pre-emptively.
- **Data-vendor research is done and filed, but paid data is explicitly on hold**
  until the gate above — see `INVENTARIS-SUMBER-DATA.md` §6bis for the official
  IDX price catalogue and vendor findings (Invezgo, Wiguna Investment, NeoBDM).

## Session 6 update (2026-09-27) — VG data feed + participant classifier

**A free per-ticker broker/price feed was found and wired.** Vendor is a solo dev's
"IDX EDGE PRO" (`stock.arjum.com`, freemium, daily quota reset 00:00 WIB). In code
and DB it is called **`vektorgauss` / `vektorgauss_history` / `vektorgauss_inventory`
/ `vektorgauss_done`**, env `VG_FEED_BASE_URL` / `VG_FEED_API_KEY` — deliberately NO
third-party name anywhere in code/DB/output (endpoint + key live only in `.env`).
It is **Tier 3** (free/scraped, undisclosed sourcing) → cross-check only, never
production; high bus-factor. Backup if it dies: price/sector/ownership/seasonality
are IDX/KSEI-direct (independent); broker/inventory/done are VG-unique → fall back
to Sectors.app ($59/mo) or IDX Equity & Derivative Log Data (Rp3jt/mo). `raw_artifacts`
archives every response, so collected data survives feed death — **harvest while free**.

New collectors (all Tier 3, `.env` key required):
- `vg collect-broker-summary --latest|--radar|--ticker` → `broker_flows` (per-broker
  buy/sell/freq/net, `side_flag` F/D from the static `FOREIGN_BROKERS` map).
- `vg collect-broker-inventory --universe|--ticker` → `broker_inventory` (~80 td
  per-broker daily net + running `cum_net_val`; the historical layer).
- `vg collect-price-history --universe|--ticker` → gap-fills `price_bars` from the
  Cloudflare-free `/api/history` (~120 td); source_tier-guarded so it NEVER
  overwrites Tier-0 IDX rows, only fills gaps + confirms trading_calendar. This is
  the fallback for the IDX 403/Cloudflare block (proven: recovered 2026-09-25).
- `vg collect-done-details --ticker [--date] [--max-pages]` → `done_by_price`
  (tick order-flow aggregated LOCALLY into price levels + aggressor BUY/SELL +
  foreign F/D split). Expensive (~130 pages/liquid stock/day) → on-demand, NOT EOD.

Engine signals (all probation, weight 0, forward-tested):
- `BROKER-ASING-AKUM`/`-DIST`, `BROKER-DOMISILI-KONFIRM`/`-DIVERGEN` (broker-house
  foreign net vs account-domicile nff — different measures, cross-checked not summed).
- **Participant classifier (INFERENCE):** `FOREIGN-INST`, `DOM-RETAIL`,
  `BANDAR-CANDIDATE` from `broker_flows`+`broker_inventory` via static
  `RETAIL_BROKERS` map + avg-ticket + net-buyer concentration (`bkr_cr1`,
  `bkr_top_buyer` = who the dominant broker is). Honest limits: Foreign/Domestic
  solid; Institution/Retail weak proxy (no daily label); Market Maker heuristic
  only. Bandar ≠ foreign (a retail broker can concentrate a gorengan).

Digest: watchlist + RADAR now show "Broker asing net … · CR3 …"; RADAR tags now
include `BROKER-ASING-AKUM` and `BANDAR-CANDIDATE`. EOD `eod.sh` runs the two-pass
(compute → `collect-broker-summary --radar` → compute) + `collect-broker-inventory
--universe` + `collect-price-history --universe`, all best-effort. Call-minimization:
**skip_existing** (default) on the harvest collectors makes idempotent re-runs cost
0 API calls; `VG_HARVEST_TICKERS` widens the harvest universe without code changes.

10 planned website features — data coverage: 9/10 have a source now (Sector Activity,
Rotation, Broker Stalker, Transaction Chart, Seasonality, Balance Position, Broker
Summary, Inventory, Compare Inventory). Done Detail = `done_by_price` (on-demand).
Sector was NOT a gap — `collect-profiles` already fills `tickers.sector` (Indonesian
IDX-IC); a static English sector map was built then reverted to avoid clobbering it.

**REPO STATE / GOTCHA:** work is on branch `claude/project-ini-progress-blwa3y`
(WSL runtime clone) — cherry-picked from a Windows/OneDrive worktree branch.
**Not pushed: the GitHub account (jaringan-bot) is SUSPENDED** (push/pull 403).
The two clones sync via `/mnt/c` only (fetch + cherry-pick); the WSL clone is the
canonical one (also holds collect-margin/collect-profiles/probe-idx not in the
worktree base). Push `git push -u origin claude/project-ini-progress-blwa3y` once
the account is restored. Runtime is still **LOCAL (WSL2 home PC), not VPS** — IDX
Cloudflare still blocks datacenter IPs, so only the VG feed (Cloudflare-free) could
move to a VPS, not the IDX-direct sources. Rotate `VG_FEED_API_KEY` (it appeared in
a chat transcript). pytest 174/174.

## Session 5 update (2026-09-05)

- **Telegram digest reformatted** — watchlist/RADAR were a dense wall of
  abbreviated text (`NFF D0`/`W1`, quadrant duplicated with the primary tag,
  "tgt" instead of "target"). Redesigned for scannability: blank line between
  every ticker, emoji state marker per quadrant, spelled-out flow labels
  ("Asing bersih — hari ini X · 1 minggu terakhir Y"), sorted monospace
  tag-count footer instead of a `TAG:n · TAG:n` run-on line. Verified against
  real 2026-09-04 data via `vg digest --dry-run` and a live sample sent to
  Telegram. Committed `6c21bba`.
- **Official IDX Data Service price catalogue found** — public PDF, no login:
  `idx.co.id/media/2qalvu4z/20240923_idx-data-catalogue-pricelist-2024.pdf`.
  Resolves the old "fee table empty, contact IDX" gap: IDX sells **no
  ready-made "broker summary per emiten"** product (only raw order/trade data
  with broker codes to self-aggregate, or a Domestic/Foreign-only aggregate —
  confirms `IDX Equity EoD Recapitulation` is NOT broker-level). Also corrects
  a standing wrong assumption: broker codes are **not anonymized in licensed
  feeds** (Log Data, Real Time Order, ITCH all carry `Brokers Code`) —
  anonymization only applies to the free public feed. Vendor checks (Invezgo —
  2yr API lookback cap + explicit no-redistribution clause; Wiguna Investment —
  login-gated, founder works at an IDX-member broker; NeoBDM — now blocks
  ClaudeBot in robots.txt) also logged. Full detail in
  `INVENTARIS-SUMBER-DATA.md` §6bis, `PERTANYAAN-IDX-KSEI.md`. **Decision:
  none of this is acted on until the gate above.** Committed `4688b13`.
- `.env` audited per HANDOFF item #2 below — still open, see "Status at a
  glance".
- **Phase B shipped: disclosure PDF parsing** (`src/vg/sources/disclosure_pdf.py`
  + `disclosure_holdings` table + `vg parse-disclosure`, wired into `eod.sh`
  after `collect-disclosure`). Word-position clustering over `pdfplumber`
  (its `extract_table()` doesn't survive this form's 2-line-wrapped cells —
  verified empirically), not naive text regex. Backfilled all 663 archived
  filings (2026-07-01→09-05): **0 parse errors**, 1446 transaction rows, only
  24 filings (3.6%) fell back to header-only (no per-row breakdown, but
  shares-before/after — and therefore direction — still captured). New tags
  `INSIDER-AKUM/DIST`, `SUBSTANTIAL-AKUM/DIST` in `engine.py` — direction
  from the header's shares_before/after (reliable), not the fragile
  transaction-type column. Recomputed `signal_components`/`signal_log` over
  the same window; see "Status at a glance" for the first read. Real PDF
  fixtures + regression tests in `tests/test_disclosure_pdf_parse.py`.

## Session 4 update (2026-09-04)

- **Disclosure Phase A backfill DONE** — `vg collect-disclosure --backfill 2026-07-01
  2026-09-04`: 659 filings, 171 tickers, every PDF archived. Recompute over the window
  done. `vg eval` now shows `LAPOR-KEPEMILIKAN-RAMAI`: n 188, edgeH5 **−1.17%** (−2.29%
  liquid), hit 48% vs uHit 54% — the crude filing-surge tag reads **negative** in-sample
  (a burst of >5%/insider filings tends to precede underperformance). Probation weight-0;
  forward-test will judge it. 212 signal rows / 42 dates. Other tags unchanged.
- **Dashboard artifact regenerated** (`a13ea232`) with the above.
- **GitHub Actions EOD cutover FAILED — rolled back to Task Scheduler.** The self-hosted
  runner (`vg-wsl-bogor`) does not hold up on this box: the WSL2 VM shuts down shortly
  after each `wsl.exe` session ends, so Postgres + the runner process keep dying and the
  runner↔GitHub link flaps. Two `workflow_dispatch` tests of `eod.yml` both failed the
  same way (job picked up, `git pull` ✓, "Run EOD sequence" cancelled ~14 s in when the
  broker connection dropped). No DB damage. `Enable-ScheduledTask -TaskName
  VektorGauss-EOD` run — Task Scheduler is the EOD driver again (its action holds a WSL
  session for the whole run, which is why it always worked). Full diagnosis + the options
  to make Actions work here (keep-alive session / `.wslconfig` / free RAM; host has only
  15.7 GB) are in the `vektor-gauss-vps` memory and `INFRA-GITHUB-ACTIONS.md`.
- **Not committed.** `HANDOFF.md`, `INFRA-GITHUB-ACTIONS.md`, and the memory files are the
  only changes; ask the user before committing.

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
2. ~~Tune `.env`: real watchlist, `VG_HOLDINGS`, `VG_ACCOUNT_BALANCE` +
   `VG_RISK_PCT` for lot sizing.~~ Watchlist done. `VG_HOLDINGS`/`VG_ACCOUNT_BALANCE`
   deliberately blank/0 — no live execution yet (confirmed 2026-09-05, see
   "Status at a glance"); fill in only once actually trading. Also set
   **`VG_FORWARD_SINCE`** to today's date once the cron is steady — it splits
   `vg eval` / the weekly digest into an in-sample and a forward-only block, the
   actual instrument for the gate. (`VG_RADAR_TAGS` defaults to the two live
   candidates now; override only to change them.)
3. ~~Confirm the first cron run; migrate to GitHub Actions.~~ Done (2026-09-04) —
   self-hosted runner `vg-wsl-bogor` live, `eod.yml` (19:15 WIB Mon–Fri) is the
   EOD driver, Task Scheduler disabled. `INFRA-GITHUB-ACTIONS.md`. Still: eyeball
   the first live scheduled run on GitHub → Actions.
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
