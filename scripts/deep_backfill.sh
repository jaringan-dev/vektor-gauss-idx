#!/usr/bin/env bash
# K7 nightly chunk: fill broker history (2023-01 .. 2026-06) newest -> oldest, VG_DEEP_BUDGET feed calls per run (default 15,000; the API allows what the plan says per day, EOD needs ~2k), then compute the filled
# days. Resumable and idempotent: run it every night (just after the 00:00 quota reset) until `vg broker-deep --plan` says nothing
# is left. It uses its own budget, so the 19:15 EOD (~1,000 calls) is never starved. Logs to ~/deep_backfill.log.
#   VG_DEEP_BUDGET (default 15000)  VG_DEEP_WORKERS (default 1; parallel feed calls, max 8)  VG_DEEP_START (default 2023-01-02)  VG_DEEP_END (default 2026-06-02)
set -u
VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
LOG="${VG_DEEP_LOG:-$HOME/deep_backfill.log}"
# Several feed calls at once: the daily quota is per call, so this only makes the budget run out sooner (hours instead of most of a day).
export VG_FEED_WORKERS="${VG_DEEP_WORKERS:-${VG_FEED_WORKERS:-1}}"
{
  echo "===== deep backfill $(date -Is) ====="
  vg broker-deep --start "${VG_DEEP_START:-2023-01-02}" --end "${VG_DEEP_END:-2026-06-02}" --budget "${VG_DEEP_BUDGET:-15000}" --compute
  echo "rc=$?  $(date -Is)"
  vg broker-deep --start "${VG_DEEP_START:-2023-01-02}" --end "${VG_DEEP_END:-2026-06-02}" --plan
  vg status --telegram --kind backfill   # progress after every night; loud only when something is wrong
} >> "$LOG" 2>&1
