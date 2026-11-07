#!/usr/bin/env bash
# Vektor Gauss daily EOD sequence.
#
# Rolling window self-heals a few missed days (all upserts are idempotent).
# Run by: GitHub Actions (self-hosted runner) / cron / by hand.
#
#   VG_HOME             repo checkout with .venv + .env   (default: parent of this script)
#   VG_EOD_WINDOW_DAYS  rolling backfill window            (default: 5)
#   VG_LOCK            flock path to guard overlap         (default: /tmp/vg-eod.lock)
#   VG_HEALTHCHECK_URL  healthchecks.io ping URL (optional) — "is the whole machine/
#                       cron still alive" dead-man's-switch. Empty/unset = skipped.
#                       See README.md "Uptime monitoring" for setup.
#
# Writes to stdout (so it shows in the Actions log). Redirect yourself for cron.
set -u

VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
[ -f .env ] && { set -a; . ./.env; set +a; }

exec 9>"${VG_LOCK:-/tmp/vg-eod.lock}"
flock -n 9 || { echo "$(date -Is) another run holds the lock; skip"; exit 0; }

WINDOW="${VG_EOD_WINDOW_DAYS:-5}"
TODAY=$(date +%F)
BACK=$(date -d "$TODAY -${WINDOW} days" +%F)

echo "===================== EOD $(date -Is) ====================="
echo "-- window $BACK .. $TODAY --"
# Apply db/schema.sql first. It is idempotent (CREATE/ALTER ... IF NOT EXISTS) and is how a deploy that adds a column
# (e.g. the signal_components broker-scoring columns) reaches the database; without it `compute` would fail on INSERT.
vg init-db || echo "!! init-db failed - compute may fail on a missing column"
vg collect-price       --backfill "$BACK" "$TODAY"
vg collect-index       --backfill "$BACK" "$TODAY"
# Cloudflare-free fallback: gap-fill watchlist+holdings OHLCV+foreign flow from the
# VG feed (Tier 3) so a 403 on the IDX price feed above never starves the pipeline.
# Never overwrites Tier-0 rows; also confirms trading days so `compute` can still run.
vg collect-price-history --universe || true
vg collect-disclosure  --backfill "$BACK" "$TODAY"
vg parse-disclosure    # Phase B: catch up on anything unparsed (cheap — usually 0-15/day)
vg collect-halts       --backfill "$BACK" "$TODAY"
# Real free float from the issuers' monthly registry reports (LBRE), for FLOAT2-ASING-PADAT. Before compute, so a report announced
# today can be used tonight. Announcements already stored are skipped; busy days (5th-12th of a month) ~200 PDFs. Best-effort.
vg collect-free-float --recent 10 || true
vg detect-ca
# Quality gate (ARSITEKTUR-DATA-v1 K4): if today's inputs are broken (price gap, OHLC violations) compute
# would turn them into wrong signals that eval later scores. A BLOCK finding skips compute and alerts.
GATE_OK=1
vg quality-gate || GATE_OK=0
if [ "$GATE_OK" -eq 1 ]; then
vg compute             --backfill "$BACK" "$TODAY"   # pass 1 — produces the radar candidates
else
  echo "!! quality gate BLOCKED — skipping compute (both passes); fix the inputs, then re-run"
fi
# Broker summary (vektorgauss, Tier 3) for watchlist + holdings + EVERY liquid name
# (> Rp5B traded) + today's radar extras, then recompute so the broker signals + the RADAR
# broker line land. Two-pass: needs pass-1 `compute` above (radar names). Idempotent: names
# already stored are skipped. ~190 calls/day at VG_FEED_DELAY_SECONDS (default 1s). It used
# to be `--radar` (a 60-name cap) which let daily coverage silently sag to ~60 (checklist G3).
# Best-effort — a Tier-3 outage must never abort EOD.
vg collect-broker-summary --full || true
# The COMPLETE broker tables (every broker that traded, ~960 calls) for the stalker / inventory pages. A separate table: nothing computed
# from broker_flows reads it, so the BROKER-* signals are untouched. After the line above so that day is already published. Best-effort.
vg collect-broker-full --sessions 1 || true
# Done-details (HAKA/HAKI) for the WATCHLIST (+ VG_LQ45_TICKERS if set) only - never the whole market (~130 calls per
# stock-day). Time-boxed by VG_DONE_BUDGET_MINUTES (default 15) so it cannot hold up pass 2 and the digest.
# Feeds the Aggressor Ratio in pass 2. Best-effort Tier 3; a ticker already stored for the day costs 0 calls.
T_DONE=$SECONDS
vg collect-done-details --watchlist-only || true
echo "-- order-flow harvest took $((SECONDS - T_DONE))s (budget ${VG_DONE_BUDGET_MINUTES:-15} min) --"
[ "$GATE_OK" -eq 1 ] && { vg compute --backfill "$BACK" "$TODAY" || true; }   # pass 2 — broker signals land
# Broker inventory (~80 td cum net per broker) for watchlist+holdings — historical
# feed data for the Transaction/Inventory/Compare charts. Best-effort, Tier 3.
vg collect-broker-inventory --universe || true
# Rebuild the split/bonus/dividend factors from today's corp_actions (~1 min) so adjusted prices and returns never run on
# factors that miss a newly announced action (audit 2026-11-07: they were last rebuilt by hand, days behind). Best-effort.
vg compute-adj || true
vg backfill-outcomes
# Outcome v2 (audit 2026-11-07): VWAP(t+1) entry, IDX-reference-price + dividend adjusted -> signal_log.ret2_*, market_breadth2.
# Writes only v2 columns (~2 min: builds a price grid). The forward-test gate reads these (engine.eval_tags). Best-effort.
vg backfill-outcomes2 || true
# Data-quality flags (signals whose input was known broken at the time), rebuilt whole (~1 min). Shown beside the edge.
vg flag-signals || true
vg paper-update        # open/resolve FLOAT-ASING-PADAT / TIKET-BESAR-AKUM paper trades

# KSEI's monthly ownership file appears a few days after month-end, on a day we cannot predict. Ask every day until last month's
# file is stored; once it is, this is a no-op (no download). Best-effort and idempotent.
vg collect-ownership --pending || true
if [ "$(date +%-d)" -le 5 ]; then
  echo "-- monthly: corp actions --"
  vg collect-corpactions --year "$(date +%Y)"
  vg detect-ca
fi

if [ "$(date +%u)" -eq 1 ]; then
  echo "-- Monday: weekly eval --"
  vg digest --weekly || true
fi

if [ "$(date +%u)" -eq 7 ]; then
  echo "-- Sunday: backup (pg_dump + artifacts -> GitHub Releases) --"
  bash "$VG_HOME/scripts/backup.sh" || true
  echo "-- Sunday: refresh the stop/target test shown at /risk-test (~3 min, best-effort) --"
  vg risk-eval --save > /dev/null || true
  echo "-- Sunday: refresh the edge analysis shown at /research (~3 min, best-effort) --"
  vg research --save > /dev/null || true
fi

vg digest || true

# Tell the owner how the run went (silent when everything is fine, with a sound when something is not).
vg status --telegram --kind evening || true

# One-off checklist for the first healthy EOD after the broker-scoring release. It sends ONCE, only after a successful
# compute with the new columns present (`vg remind-verify --force` resends). Last on purpose and always `|| true`:
# it cannot change what `vg digest` or the status report above did.
vg remind-verify || true

if [ -n "${VG_HEALTHCHECK_URL:-}" ]; then
  # Dead-man's-switch: tells an OUTSIDE service the machine/cron is still alive.
  # Catches "PC/WSL/internet died entirely" (Task Scheduler never even fires this
  # script), which nothing running ON this machine can ever detect about itself.
  # Deliberately unconditional (reached even after upstream `|| true` failures) —
  # this checks "did the run happen at all", not "did every step succeed"; the
  # latter belongs to the superadmin dashboard's per-collector view (RENCANA-UI-UX.md).
  curl -fsS -m 10 --retry 2 "$VG_HEALTHCHECK_URL" >/dev/null 2>&1 || true
fi

echo "----- done $(date -Is) -----"
