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
vg collect-price       --backfill "$BACK" "$TODAY"
vg collect-index       --backfill "$BACK" "$TODAY"
vg collect-disclosure  --backfill "$BACK" "$TODAY"
vg parse-disclosure    # Phase B: catch up on anything unparsed (cheap — usually 0-15/day)
vg collect-halts       --backfill "$BACK" "$TODAY"
vg detect-ca
vg compute             --backfill "$BACK" "$TODAY"
vg backfill-outcomes
vg paper-update        # open/resolve FLOAT-ASING-PADAT / TIKET-BESAR-AKUM paper trades

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

if [ "$(date +%u)" -eq 7 ]; then
  echo "-- Sunday: backup (pg_dump + artifacts -> GitHub Releases) --"
  bash "$VG_HOME/scripts/backup.sh" || true
fi

vg digest || true

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
