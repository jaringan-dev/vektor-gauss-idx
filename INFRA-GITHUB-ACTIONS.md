# GitHub Actions — CI + EOD orchestration

Replaces the fragile Windows Task Scheduler cron ([HANDOFF.md](HANDOFF.md) gotcha:
a `nohup`'d job dies when the last WSL session exits) with GitHub's scheduler:
run history, logs, a manual **Run workflow** button, and failure email.

This is [RENCANA-FASE-2.md](RENCANA-FASE-2.md) §3.2, done early — it's a Fase 1
reliability fix, not gated.

## Workflows

| File | Runner | Trigger | Does |
|---|---|---|---|
| `.github/workflows/ci.yml` | `ubuntu-latest` (hosted) | push to `main`, PR | `ruff check` + `pytest` (offline, no DB) |
| `.github/workflows/eod.yml` | **self-hosted** `[self-hosted, vektor-gauss]` | cron `15 12 * * 1-5` (19:15 WIB) + manual | `git pull` the deployment, then `scripts/eod.sh` |

`scripts/eod.sh` is the versioned EOD sequence (was only on the WSL box as
`/home/vg/vg-eod.sh`). It self-heals a rolling `VG_EOD_WINDOW_DAYS`-day window,
does the monthly KSEI pull on day ≤5, the weekly eval on Mondays, then the digest.

**Why self-hosted for EOD:** IDX's Cloudflare blocks every cloud/datacenter ASN
(AWS, Oracle, and by extension GitHub-hosted runners on Azure IPs) with HTTP 1020.
Only the residential-IP box reaches `www.idx.co.id`. KSEI is not blocked, but the
box also holds Postgres, so the whole sequence runs there.

## Status — cutover REVERTED (2026-09-04, session 4)

**The self-hosted runner does not work as the EOD driver on this box. Rolled back to
Task Scheduler.** `Enable-ScheduledTask -TaskName VektorGauss-EOD` — it is the EOD
driver again (proven: last good run 2026-09-03 19:15, result 0).

Why it failed: the **WSL2 VM does not stay up between `wsl.exe` sessions** on this
machine. A persistent systemd *system* service (the runner) is not enough to hold the
VM alive — the VM shuts down a short while after the last session ends, killing Postgres
and the runner process with it. Observed while testing: `postgresql` cycling
active→inactive every ~20 s (`received fast shutdown request`), the runner stuck in an
`EstablishVssConnection` reconnect loop, and one hard VM crash (`CreateVm 0x800705b4`,
`WaitForBootProcess: /sbin/init failed within 10000ms`, journal corruption). Two
`workflow_dispatch` tests of `eod.yml` both failed identically: `Set up job` ✓,
`Update deployment` (git pull) ✓, then **`Run EOD sequence` cancelled ~14 s in**
("Cancellation/Shutdown message received … UserCancelled" = the runner↔GitHub broker
connection dropped); GitHub marks the job failed after its 10-min reconcile window.
`eod.sh` reached `collect_price` for 2 days before the drop — **no DB damage** (Postgres
data is on ext4, survives VM shutdown).

Task Scheduler works precisely because its action `wsl.exe -d Ubuntu-24.04 -- bash …`
*starts* the VM and **holds the session open for the entire run** — VM-down-between-runs
is a non-issue for it.

Contributing factors: host has only **15.7 GB RAM, ~3.2 GB free**; no
`%UserProfile%\.wslconfig` (WSL defaults; VM allotted ~7.6 GB → oversubscribed under
collect load); a broken `wsl-pro-service` (Ubuntu Pro) error-looping every 2 s. WSL
2.7.3.0, kernel 6.6.114.1, Win11 26200.9278, NAT networking.

### To make GitHub Actions viable here (not done — user's call)

Pick one, then re-disable Task Scheduler:

1. **Keep-alive session** — a Windows Task Scheduler task *at logon/boot* that holds a
   permanent WSL session so the VM never idles out:
   `wsl.exe -d Ubuntu-24.04 -u root -- sh -c "exec sleep infinity"` (run hidden, restart
   on failure). Simplest fix; the runner + Postgres then stay up.
2. **`%UserProfile%\.wslconfig`**:
   ```ini
   [wsl2]
   vmIdleTimeout=-1
   memory=6GB
   swap=8GB
   networkingMode=mirrored
   dnsTunneling=true
   ```
   then `wsl --shutdown` once. (`vmIdleTimeout=-1` alone may not be enough without a
   held session — combine with #1.)
3. **Free Windows RAM** so the VM isn't memory-starved during the nightly collect.

Also: `sudo systemctl disable --now wsl-pro-service`. And once fixed, consider whether
`eod.yml`'s `schedule:` should stay (right now its cron still fires at 19:15 and
harmlessly fails against the offline runner; `flock` guards any double-run).

`ci.yml` on `ubuntu-latest` (hosted) is unaffected — it still runs on push/PR.

---

## Original cutover notes (2026-09-04) — superseded by the section above

- Runner `vg-wsl-bogor` (labels `self-hosted, vektor-gauss`) registered and online;
  systemd service `actions.runner.jaringan-bot-vektor-gauss.vg-wsl-bogor` (enabled
  + running, starts with the distro).
- `scripts/eod.sh` validated end-to-end on the box (collect → compute →
  backfill-outcomes → digest `sent (1 message)`).
- **Task Scheduler `VektorGauss-EOD` is DISABLED.** `eod.yml` (cron 19:15 WIB
  Mon–Fri) is now the only EOD driver. First live proof = the next scheduled run —
  check GitHub → Actions.
- Rollback if `eod.yml` misbehaves: `powershell.exe -Command "Enable-ScheduledTask
  -TaskName VektorGauss-EOD"` (the rolling 5-day window self-heals the gap).

## One-time setup (on the WSL box)

1. **Register the runner** — GitHub repo → Settings → Actions → Runners → *New
   self-hosted runner* → Linux x64. Run the shown commands as user `vg`:
   ```bash
   mkdir -p ~/actions-runner && cd ~/actions-runner
   curl -o r.tar.gz -L <url-from-github> && tar xzf r.tar.gz
   ./config.sh --url https://github.com/jaringan-bot/vektor-gauss \
               --token <token-from-github> --labels vektor-gauss --unattended
   sudo ./svc.sh install vg && sudo ./svc.sh start      # runs under systemd, survives logout
   ```
   The `vektor-gauss` label is what `eod.yml` targets. `systemd=true` is already
   set in `/etc/wsl.conf`, so the service starts with the distro.

2. **`.env` stays put** at `/home/vg/vektor-gauss/.env` — `scripts/eod.sh` sources
   `.venv` and the app reads `.env` via python-dotenv. No GitHub secrets needed
   for EOD (the runner is on the trusted box). Fix the one quoting issue while
   you're there: the `VG_HTTP_USER_AGENT` value has spaces — dotenv is fine with
   it, but `. .env` in a shell is not; nothing sources it that way, so optional.

3. **Keep Task Scheduler running in parallel** for ~1 week. Both are idempotent
   (`flock` + upserts), so a double run is a no-op. Once a few `eod.yml` runs land
   a digest in Telegram, disable the `VektorGauss-EOD` task:
   ```powershell
   Disable-ScheduledTask -TaskName VektorGauss-EOD
   ```

## Notes

- `eod.yml` does `git -C $VG_HOME pull --ff-only` first, so pushing to `main`
  (after CI passes) deploys automatically. If you'd rather pin the deployment,
  drop that step and `git pull` by hand.
- `concurrency: vg-eod` + `scripts/eod.sh`'s `flock` both guard against overlap.
- The runner needs `git`, `bash`, `flock`, `curl`, and the repo's `.venv`
  (`uv pip install -e ".[dev]"`) — all already present from the Fase 1 setup.
- Migrating to an Indonesian ISP VPS later: move the runner (`./config.sh remove`
  then re-register on the VPS) — see [RENCANA-FASE-2.md](RENCANA-FASE-2.md) §3.5.
