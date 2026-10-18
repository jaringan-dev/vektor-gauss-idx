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
# Every step always runs (no `set -e`): one failing collector must not starve the
# rest. Failures are collected and listed at the end instead:
#   run  — core step; any failure makes the script exit 1.
#   soft — best-effort step (Tier-3 feed, digest); a failure is only a warning.
#
# Writes to stdout (so it shows in the Actions log). Redirect yourself for cron.
set -u

VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
[ -f .env ] && { set -a; . ./.env; set +a; }

exec 9>"${VG_LOCK:-/tmp/vg-eod.lock}"
flock -n 9 || { echo "$(date -Is) another run holds the lock; skip"; exit 0; }

FAILED=()
WARNED=()
run()  { "$@" || FAILED+=("$*"); }
soft() { "$@" || WARNED+=("$*"); }

WINDOW="${VG_EOD_WINDOW_DAYS:-5}"
TODAY=$(date +%F)
BACK=$(date -d "$TODAY -${WINDOW} days" +%F)

echo "===================== EOD $(date -Is) ====================="
echo "-- window $BACK .. $TODAY --"
run  vg collect-price       --backfill "$BACK" "$TODAY"
run  vg collect-index       --backfill "$BACK" "$TODAY"
# Cloudflare-free fallback: gap-fill watchlist+holdings OHLCV+foreign flow from the
# VG feed (Tier 3) so a 403 on the IDX price feed above never starves the pipeline.
# Never overwrites Tier-0 rows; also confirms trading days so `compute` can still run.
soft vg collect-price-history --universe
run  vg collect-disclosure  --backfill "$BACK" "$TODAY"
run  vg parse-disclosure    # Phase B: catch up on anything unparsed (cheap — usually 0-15/day)
run  vg collect-halts       --backfill "$BACK" "$TODAY"
run  vg detect-ca
run  vg compute             --backfill "$BACK" "$TODAY"   # pass 1 — produces the radar candidates
# Broker summary (vektorgauss, Tier 3, forward-only) for watchlist + holdings +
# today's radar-tagged names, then recompute so the broker signals + the RADAR
# broker line land. Two-pass: needs pass-1 `compute` above.
soft vg collect-broker-summary --radar
soft vg compute             --backfill "$BACK" "$TODAY"   # pass 2 — broker signals land
# Broker inventory (~80 td cum net per broker) for watchlist+holdings — historical
# feed data for the Transaction/Inventory/Compare charts. Tier 3.
soft vg collect-broker-inventory --universe
run  vg backfill-outcomes
run  vg paper-update        # open/resolve FLOAT-ASING-PADAT / TIKET-BESAR-AKUM paper trades

if [ "$(date +%-d)" -le 5 ]; then
  echo "-- monthly: ownership + corp actions --"
  run vg collect-ownership --latest
  run vg collect-corpactions --year "$(date +%Y)"
  run vg detect-ca
fi

if [ "$(date +%u)" -eq 1 ]; then
  echo "-- Monday: weekly eval --"
  soft vg digest --weekly
fi

if [ "$(date +%u)" -eq 7 ]; then
  echo "-- Sunday: backup (pg_dump + artifacts -> GitHub Releases) --"
  soft bash "$VG_HOME/scripts/backup.sh"
fi

soft vg digest

if [ -n "${VG_HEALTHCHECK_URL:-}" ]; then
  # Dead-man's-switch: tells an OUTSIDE service the machine/cron is still alive.
  # Catches "PC/WSL/internet died entirely" (Task Scheduler never even fires this
  # script), which nothing running ON this machine can ever detect about itself.
  # Deliberately unconditional (reached even when steps above failed) — this
  # checks "did the run happen at all"; per-step failures are the summary below.
  curl -fsS -m 10 --retry 2 "$VG_HEALTHCHECK_URL" >/dev/null 2>&1 || true
fi

if [ ${#WARNED[@]} -gt 0 ]; then
  echo "WARN — best-effort steps failed (${#WARNED[@]}):"
  printf '  %s\n' "${WARNED[@]}"
fi
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED — core steps failed (${#FAILED[@]}):"
  printf '  %s\n' "${FAILED[@]}"
fi

echo "----- done $(date -Is) -----"
[ ${#FAILED[@]} -eq 0 ]
