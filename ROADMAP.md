# Vektor Gauss roadmap

**As of 3 Nov 2026 (late evening).** One page that replaces reading `ARSITEKTUR-DATA-v1.md` §6, `CHECKLIST-UI-UX.md` and
`RENCANA-ROMBAK-UI-v3.md` side by side. Item codes (K#, F#, G#, I#) point into those documents. `CHECKLIST-UI-UX.md` is out of date:
several of its open items (glossary A5, stock/pattern pages F3/F4, clickable tickers F10, mobile D1/D2, authentication I3) were
finished after it was written.

## The one question everything serves

After about 3.5 years of free IDX data, **no pattern has been shown to beat trading costs.** The new statistics page (`/research`)
sharpens that: several patterns have a small but real edge *before* costs (about +0.2% to +0.6% per 5 days against stocks of the same
size and liquidity), and every one of them is smaller than the modelled round-trip cost (about 1.2% for liquid stocks). The
stop/target replay did not change it either. So the roadmap has three jobs, in this order:
1. find out honestly whether an edge exists (more data, better statistics);
2. make the tool useful and understandable even if the answer is "no edge": risk sizing, flow reading, honest reporting;
3. keep the data safe and complete.

Nothing commercial (public report card, paid tiers) is built until job 1 has an answer.

## Done

| Area | What |
|---|---|
| Data foundation | Immutable raw archive, sell-side ladders, split/dividend adjustment, EOD quality gate, bad-price repair (K1–K5); 105-day sell-side refill (K6); weekly backups |
| Research | BROKER2 patterns; data audit; stop/target replay (1.16M stock-days); **K10–K13 analysis** (dependence-aware intervals, false-discovery control, size/liquidity-matched edge, walk-forward folds and regimes, per-signal cost model) at `/research` |
| Dashboard | English UI, glossary, stock and pattern pages, charts, mobile, `/risk-test`, `/research` |
| Access | Password gate, safe startup, `PHONE-ACCESS.md` (your Windows/phone steps are untested) |
| Monitoring | `vg status`: Telegram report after every EOD and every backfill night (silent when fine, with a sound when not) plus a 08:00 morning report; same checks on the `/health` page |
| Housekeeping | Dashboard risk plan now reads the same `.env` settings as the Telegram digest; Sunday EOD refreshes `/risk-test` and `/research`; the BBCA ±50% days were checked (real, see below) |

**BBCA outlier days.** Foreign-broker net flow of ±40–56% of traded value on some BBCA days is real, not a data fault: BBCA is
foreign-dominated, the independent IDX-domicile measure has a similar median size (13% of traded value) and correlates 0.78 with the
broker-based figure. It does mean a fixed "5% of traded value" threshold fires constantly on large caps, which the size-matched
comparison in `/research` partly corrects for.

## Running now

| Item | State |
|---|---|
| K7 deep broker history (2023-01 to 2026-06) | Runner built (`vg broker-deep`, `scripts/deep_backfill.sh`): newest-first, resumable, 15,000 calls per night, computes the filled days. About **165,000 calls left, roughly 11 nights**. The first chunk starts after the 00:00 quota reset. **To run it every night unattended, register a scheduled task** (see below) |
| Forward-test gate | Time-based: the first pattern can be judged once its span reaches 60 days, around mid-November |
| G3 / G11 proofs | G3: 5 EOD days with ≥150 broker names (2 of 5 so far); G11: release `backup-2026-11-08` after Sunday |
| Phone access | Your steps in `PHONE-ACCESS.md` |

## Next (after K7 has filled)

| Item | Why |
|---|---|
| Re-run `/research` on 3 years of broker data | Today the broker patterns have only ~94 days; the intervals are wide. This is the real test of whether they are worth anything |
| K9 sector map and KSEI coverage | Lets the matched comparison also neutralise sector |
| Beta control in `/research` | The matched edge removes size and liquidity but not beta |
| Health home page (F2) and collector run log (G1) | A single "is everything healthy?" screen |
| Screener speed (B2) | Show the top 100 first |

## Later

K8 security master (delistings, historical sectors; depends on a free source), K14 fundamentals, K15 macro and short-sell data,
sector rotation (F6), dividend calendar (F7), insiders per stock (F8), Broker Stalker (F9), per-broker inventory chart (needs K7 and a
two-sided broker measure), tablet and screen-reader testing (C5, D4).

## Gated: do not start until a pattern passes (or you decide the product is a reading/risk tool)

Public report card (I1), landing page, sign-up, user watchlists, paid tier (I2). If no pattern ever passes, the honest product is a
transaction-reading and risk-sizing tool for you, not something to sell as "signals".

## Worth your attention

- The Telegram digest still prints **"aksi BUY / ADD / REDUCE/SELL"** lines (`VG_DIGEST_TRADE_CALLS=true`). They contradict the "no buy/sell
  imperatives" rule and nothing measured supports them. Consider setting `VG_DIGEST_TRADE_CALLS=false`.

## Decisions waiting for you

1. **Product direction if nothing passes:** personal reading and risk tool, or keep hunting for an edge once K7 data is in? (I assumed the second, in the background.)
2. **K7 depth after 2023:** extend to 2020 (another ~100,000 calls) once the 2023 data has been analysed?
3. **Fundamentals (K14):** worth it, or stay focused on flow and price?
4. **Phone access:** are you doing the Tailscale setup?

## Register the nightly K7 task (one time, PowerShell)

```powershell
$a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 -u vg -- bash -lc ~/vektor-gauss/scripts/deep_backfill.sh"
$t = New-ScheduledTaskTrigger -Daily -At 12:10AM
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName "VektorGauss-DeepBackfill" -Action $a -Trigger $t -Settings $s
```

Disable it (`Disable-ScheduledTask -TaskName VektorGauss-DeepBackfill`) when `vg broker-deep --plan` reports nothing left.

## Register the 08:00 morning status task (one time, PowerShell)

It runs independently of the EOD script, so it also tells you when an EOD did not run.

```powershell
$a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 -- bash /home/vg/vektor-gauss/scripts/status.sh morning"
$t = New-ScheduledTaskTrigger -Daily -At 8:00AM
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName "VektorGauss-Status" -Action $a -Trigger $t -Settings $s
```

**If the whole PC is off, nothing can be sent.** For that, create a free check at healthchecks.io (period 1 day, grace 3 hours), put its ping URL in
`.env` as `VG_STATUS_HEALTHCHECK_URL=...`, and connect the check to your Telegram or email there. A missing morning ping then reaches you another way.

## Running-trade recorder (`vg record-trades`) - register the weekday task (one time, PowerShell)

The feed's running-trade WebSocket keeps no history and allows ONE connection per account, so exactly one recorder must run per day.
It waits for the open (08:50), records until 16:05 WIB, writes one row per stock per minute to `trade_flow_1m`, and logs one `run_log`
row (phase `trade_record`). It costs no REST quota. The PC and WSL must be up for the whole session; the run holds its own WSL session.

```powershell
$a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 -u vg -- bash /home/vg/vektor-gauss/scripts/record_trades.sh"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:55AM
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 9)
Register-ScheduledTask -TaskName "VektorGauss-TradeRecorder" -Action $a -Trigger $t -Settings $s
```

Smoke test any time the market is open (writes real rows): `vg record-trades --max-seconds 120`; add `--dry-run` to count without writing.
What is NOT proven yet: that the feed's buy/sell label is the aggressive side (compare a recorded day with `done-details`), and that
big single prints (one ENRG print was Rp149B) are regular-board trades rather than crossings.
