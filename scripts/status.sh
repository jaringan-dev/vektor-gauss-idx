#!/usr/bin/env bash
# Daily status report to Telegram (morning). Run by a Windows scheduled task, independent of the EOD script, so it can also tell you
# when the EOD did NOT run. Optionally pings an external dead-man's-switch so you hear about it when the whole PC is off:
#   VG_STATUS_HEALTHCHECK_URL  healthchecks.io ping URL for this task (optional; see README "Uptime monitoring")
set -u
VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
vg status --telegram --kind "${1:-morning}"
rc=$?
if [ -n "${VG_STATUS_HEALTHCHECK_URL:-}" ]; then
  curl -fsS -m 10 --retry 2 "$VG_STATUS_HEALTHCHECK_URL" >/dev/null 2>&1 || true
fi
exit $rc
