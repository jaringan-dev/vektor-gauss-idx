#!/usr/bin/env bash
# RENCANA-FASE-1.md step 1: run this FROM THE VPS before building anything.
# It verifies the IDX Tier-0 endpoint is reachable (not Cloudflare-blocked)
# and that the KSEI ownership archive downloads.
set -euo pipefail

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
IDX='https://www.idx.co.id/primary/TradingSummary/GetStockSummary?length=5&start=0'
KSEI='https://web.ksei.co.id/archive_download/holding_composition'

echo "== IDX Stock Summary =="
code=$(curl -s -o /tmp/vg_idx.json -w '%{http_code}' -A "$UA" \
  -H 'Accept: application/json' \
  -H 'Referer: https://www.idx.co.id/en/market-data/trading-summary/stock-summary' \
  "$IDX" || true)
echo "HTTP $code"
if [ "$code" = "200" ] && head -c1 /tmp/vg_idx.json | grep -q '{'; then
  echo "OK — JSON returned:"
  head -c 300 /tmp/vg_idx.json; echo
else
  echo "BLOCKED or unexpected. First 300 bytes:"
  head -c 300 /tmp/vg_idx.json; echo
  echo ">> Try an Indonesian VPS, or install curl-cffi (pyproject extra 'cffi')."
fi

echo
echo "== KSEI ownership archive page =="
kcode=$(curl -s -o /tmp/vg_ksei.html -w '%{http_code}' -A "$UA" "$KSEI" || true)
echo "HTTP $kcode  ($(wc -c < /tmp/vg_ksei.html) bytes)"

echo
echo "== KSEI BalanceposEfek zip (last month-end) =="
# last day of previous month
lm=$(date -d "$(date +%Y-%m-01) -1 day" +%Y%m%d 2>/dev/null || date -v-1d -v1d +%Y%m%d)
zcode=$(curl -s -o /tmp/vg_ksei.zip -w '%{http_code}' -A "$UA" \
  -H 'Referer: https://web.ksei.co.id/archive_download/holding_composition' \
  "https://web.ksei.co.id/Download/BalanceposEfek${lm}.zip" || true)
echo "BalanceposEfek${lm}.zip -> HTTP $zcode  ($(wc -c < /tmp/vg_ksei.zip) bytes)"
head -c2 /tmp/vg_ksei.zip | grep -q 'PK' && echo "OK — valid zip" || echo "not a zip (try -1/-2 days, or a real month-end)"
