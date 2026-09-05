#!/usr/bin/env bash
# Vektor Gauss backup — protects the ONE thing the forward-test gate depends on:
# the live Postgres DB (price/KSEI/disclosure history + signal_log outcomes) and
# the raw artifact archive. Both currently live on a single home-PC disk with no
# other copy anywhere (see RENCANA-MIGRASI-VPS.md).
#
# Mechanism: pg_dump (custom format) + tar the artifact dir, upload both as
# assets on a dated GitHub Release in the same private repo (gh CLI, already
# authenticated for the git-remote bridge — no new credentials, no new service).
# Releases support files up to 2GB each and don't bloat `git clone` size like
# committing the dump would. Old backups beyond VG_BACKUP_KEEP are pruned.
#
# Run by: scripts/eod.sh (weekly, see the Sunday block) / cron / by hand.
#
#   VG_HOME          repo checkout with .venv + .env     (default: parent of this script)
#   VG_BACKUP_REPO    "owner/repo" for `gh release`       (default: jaringan-bot/vektor-gauss)
#   VG_BACKUP_KEEP    how many past backups to retain     (default: 8, ~2 months weekly)
#   VG_BACKUP_DIR     scratch dir for the dump/tar files  (default: /tmp/vg-backup)
set -u

VG_HOME="${VG_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$VG_HOME" || { echo "VG_HOME not found: $VG_HOME"; exit 1; }

# .env holds VG_DATABASE_URL + Telegram secrets — simple KEY=VALUE, safe to source.
[ -f .env ] && { set -a; . ./.env; set +a; }

REPO="${VG_BACKUP_REPO:-jaringan-bot/vektor-gauss}"
KEEP="${VG_BACKUP_KEEP:-8}"
WORKDIR="${VG_BACKUP_DIR:-/tmp/vg-backup}"
STAMP=$(date +%F)
TAG="backup-${STAMP}"
DB_URL="${VG_DATABASE_URL:-postgresql://vg:vg@localhost:5432/vektorgauss}"
ARTIFACT_DIR="${VG_ARTIFACT_DIR:-$VG_HOME/data/artifacts}"

mkdir -p "$WORKDIR"
DUMP_FILE="$WORKDIR/vg_${STAMP}.dump"
ARTIFACT_TAR="$WORKDIR/vg-artifacts_${STAMP}.tar.gz"

notify_fail() {
  # Best-effort Telegram ping — a silent backup failure is worse than a noisy one.
  [ -n "${VG_TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${VG_TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -s -m 10 "https://api.telegram.org/bot${VG_TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${VG_TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=⚠️ Vektor Gauss backup ${STAMP} GAGAL: $1" \
    >/dev/null 2>&1 || true
}

echo "===================== BACKUP $(date -Is) ====================="

echo "-- pg_dump --"
if ! pg_dump -Fc "$DB_URL" -f "$DUMP_FILE"; then
  echo "pg_dump failed"; notify_fail "pg_dump gagal"; exit 1
fi
echo "   $(du -h "$DUMP_FILE" | cut -f1)  $DUMP_FILE"

echo "-- tar artifacts ($ARTIFACT_DIR) --"
if [ -d "$ARTIFACT_DIR" ]; then
  if ! tar -czf "$ARTIFACT_TAR" -C "$(dirname "$ARTIFACT_DIR")" "$(basename "$ARTIFACT_DIR")"; then
    echo "tar failed"; notify_fail "tar artifacts gagal"; exit 1
  fi
  echo "   $(du -h "$ARTIFACT_TAR" | cut -f1)  $ARTIFACT_TAR"
else
  echo "   (skip — $ARTIFACT_DIR not found)"
  ARTIFACT_TAR=""
fi

echo "-- gh release ($REPO, tag $TAG) --"
ASSETS=("$DUMP_FILE")
[ -n "$ARTIFACT_TAR" ] && ASSETS+=("$ARTIFACT_TAR")
if ! gh release create "$TAG" "${ASSETS[@]}" \
    -R "$REPO" -t "Backup $STAMP" -n "Automated: pg_dump + data/artifacts snapshot." \
    --prerelease; then
  echo "gh release create failed"; notify_fail "upload ke GitHub Releases gagal"; exit 1
fi

echo "-- prune old backups beyond VG_BACKUP_KEEP=$KEEP --"
OLD_TAGS=$(gh release list -R "$REPO" --json tagName,createdAt \
  --jq '[.[] | select(.tagName | startswith("backup-"))] | sort_by(.createdAt) | reverse | .[].tagName' \
  | tail -n +"$((KEEP + 1))")
for t in $OLD_TAGS; do
  echo "   deleting $t"
  gh release delete "$t" -R "$REPO" --yes --cleanup-tag || true
done

rm -f "$DUMP_FILE" "$ARTIFACT_TAR"
echo "----- backup done $(date -Is) -----"
