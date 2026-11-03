#!/usr/bin/env bash
# Run the dashboard in the FOREGROUND (so a Windows scheduled task that launches it through wsl.exe also keeps the WSL VM
# alive; a detached background job is killed when the VM idles). Host/port/password come from .env:
#   VG_WEB_HOST (default 127.0.0.1), VG_WEB_PORT (default 8787), VG_WEB_PASSWORD (required for any non-loopback host).
set -u
VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
[ -f .venv/bin/activate ] && . .venv/bin/activate
exec python -m vg serve --host "${VG_WEB_HOST:-127.0.0.1}" --port "${VG_WEB_PORT:-8787}"
