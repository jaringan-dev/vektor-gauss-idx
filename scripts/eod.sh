#!/usr/bin/env bash
# Vektor Gauss daily EOD sequence.
#
# Rolling window self-heals a few missed days (all upserts are idempotent).
# Run by: GitHub Actions (self-hosted runner) / cron / by hand.
#
#   VG_HOME             repo checkout with .venv + .env   (default: parent of this script)
#   VG_EOD_WINDOW_DAYS  rolling backfill window            (default: 5)
#   VG_LOCK            flock path to guard overlap         (default: /tmp/vg-eod.lock)
#
# Writes to stdout (so it shows in the Actions log). Redirect yourself for cron.
set -u

VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .venv/bin/activate ] && . .venv/bin/activate

exec 9>"${VG_LOCK:-/tmp/vg-eod.lock}"
flock -n 9 || { echo "$(date -Is) another run holds the lock; skip"; exit 0; }

WINDOW="${VG_EOD_WINDOW_DAYS:-5}"
TODAY=$(date +%F)
BACK=$(date -d "$TODAY -${WINDOW} days" +%F)

echo "===================== EOD $(date -Is) ====================="
echo "-- window $BACK .. $TODAY --"
vg collect-price       --backfill "$BACK" "$TODAY"
vg collect-index       --backfill "$BACK" "$TODAY"
vg collect-disclosure  --backfill "$BACK" "$TODAY"
vg detect-ca
vg compute             --backfill "$BACK" "$TODAY"
vg backfill-outcomes

if [ "$(date +%-d)" -le 5 ]; then
  echo "-- monthly: ownership + corp actions --"
  vg collect-ownership --latest
  vg collect-corpactions --year "$(date +%Y)"
  vg detect-ca
fi

if [ "$(date +%u)" -eq 1 ]; then
  echo "-- Monday: weekly eval --"
  vg digest --weekly || true
fi

vg digest || true
echo "----- done $(date -Is) -----"
