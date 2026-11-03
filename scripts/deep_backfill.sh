#!/usr/bin/env bash
# K7 nightly chunk: fill broker history (2023-01 .. 2026-06) newest -> oldest, ~15,000 feed calls per run (the API allows ~21k a day; EOD needs ~1k), then compute the filled
# days. Resumable and idempotent: run it every night (just after the 00:00 quota reset) until `vg broker-deep --plan` says nothing
# is left. It uses its own budget, so the 19:15 EOD (~1,000 calls) is never starved. Logs to ~/deep_backfill.log.
#   VG_DEEP_BUDGET (default 15000)  VG_DEEP_START (default 2023-01-02)  VG_DEEP_END (default 2026-06-02)
set -u
VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
LOG="${VG_DEEP_LOG:-$HOME/deep_backfill.log}"
{
  echo "===== deep backfill $(date -Is) ====="
  vg broker-deep --start "${VG_DEEP_START:-2023-01-02}" --end "${VG_DEEP_END:-2026-06-02}" --budget "${VG_DEEP_BUDGET:-15000}" --compute
  echo "rc=$?  $(date -Is)"
  vg broker-deep --start "${VG_DEEP_START:-2023-01-02}" --end "${VG_DEEP_END:-2026-06-02}" --plan
  vg status --telegram --kind backfill   # progress after every night; loud only when something is wrong
} >> "$LOG" 2>&1
