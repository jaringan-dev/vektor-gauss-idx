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

## Status

Runner **registered and online**: `vg-wsl-bogor` (labels `self-hosted, vektor-gauss`),
systemd service `actions.runner.jaringan-bot-vektor-gauss.vg-wsl-bogor` (enabled +
running, starts with the distro). Remaining: trigger `eod.yml` once to confirm it
lands a digest, run a week alongside Task Scheduler, then disable the task.

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
