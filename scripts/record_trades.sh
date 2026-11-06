#!/usr/bin/env bash
# Running-trade recorder: one run per trading day, from just before the open to just after the close (08:50 - 16:05 WIB).
# The feed keeps no history and allows ONE connection per account, so this must be the only client of the key: start it once a day
# (Task Scheduler, weekdays 08:55; see ROADMAP) and let it end by itself. Safe to start again: a duplicate trade number is dropped,
# and a second copy would take the connection over (close 4408), so do not run two. Logs to ~/record_trades.log.
set -u
VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
LOG="${VG_TRADES_LOG:-$HOME/record_trades.log}"
{
  echo "===== record-trades $(date -Is) ====="
  vg record-trades "$@"
  echo "rc=$?  $(date -Is)"
} >> "$LOG" 2>&1
