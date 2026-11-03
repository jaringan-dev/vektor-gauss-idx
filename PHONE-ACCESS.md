# Opening the dashboard from your phone

**Status:** the code side is done and tested (password gate, lockout, security headers, a refusal to start unsafely, a
foreground runner script). The Windows/phone side below is **not tested here** because it means installing apps and changing
Windows settings on your machines, which I do not do for you. Each step says what it changes.

## What is protected, and how

The dashboard shows your watchlist and holdings, so:

- `vg serve` listens on `127.0.0.1` (this PC only) by default, with no password.
- If you tell it to listen on any other address it **refuses to start** unless `VG_WEB_PASSWORD` is set and at least 12
  characters long.
- With a password set, every page needs a login (HTTP Basic). Eight wrong attempts from one device lock it out for 10 minutes.
  `/healthz` stays open for monitors.
- The password is sent with every request, so it must only travel over an **encrypted** path. That is why the recommended
  route is Tailscale: traffic between your phone and PC is encrypted end to end, nothing is opened on your router, and the
  dashboard is never on the public internet.

## Recommended route: Tailscale + WSL mirrored networking

1. **Install Tailscale** on the PC (tailscale.com/download) and on the phone, and sign in with the same account on both. Free
   for personal use. Note the PC's name in the Tailscale app (for example `nuc-11atkc4`).
2. **Make WSL reachable from the PC's network address.** Edit `C:\Users\<you>\.wslconfig` (keep any lines already there) so it
   contains:

   ```
   [wsl2]
   networkingMode=mirrored
   ```

   Then run `wsl --shutdown` and open WSL again. **This stops everything running in WSL**, including a running EOD or backup,
   so do it in the daytime, not around 19:15. Needs Windows 11 22H2 or newer (yours is newer).
3. **Allow only the Tailscale network to reach port 8787.** In an administrator PowerShell:

   ```powershell
   New-NetFirewallRule -DisplayName "VG dashboard (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 8787 -InterfaceAlias "Tailscale" -Action Allow
   New-NetFirewallHyperVRule -Name "VG-Dashboard" -DisplayName "Vektor Gauss dashboard" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8787
   ```

   The first rule keeps Wi-Fi neighbours out; the second lets traffic into WSL when mirrored networking is on. Check them with
   `Get-NetFirewallRule -DisplayName "VG dashboard*"` and `Get-NetFirewallHyperVRule -Name VG-Dashboard`.
4. **Set a password** in `~/vektor-gauss/.env` (inside WSL). Generate one and paste it in yourself:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(18))"
   ```

   ```
   VG_WEB_USER=vg
   VG_WEB_PASSWORD=<the generated password>
   VG_WEB_HOST=0.0.0.0
   ```

5. **Start it and try it.** In WSL: `bash ~/vektor-gauss/scripts/serve.sh`. On the phone (Tailscale on), open
   `http://<pc-name>:8787`, enter user `vg` and the password. Your browser will say "Not secure" because the address is plain
   HTTP; the traffic is still encrypted by Tailscale. Save the page to the home screen if you like.
6. **Keep it running.** A background job in WSL dies when the WSL VM idles, so run it the way the EOD task does, as a Windows
   scheduled task that holds a WSL session open (this also keeps the VM alive). In PowerShell:

   ```powershell
   $a = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu-24.04 -u vg -- bash -lc ~/vektor-gauss/scripts/serve.sh"
   $t = New-ScheduledTaskTrigger -AtLogOn
   $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
   Register-ScheduledTask -TaskName "VektorGauss-Dashboard" -Action $a -Trigger $t -Settings $s
   ```

   Stop the older detached dashboard first (it also uses port 8787).

## Other routes

- **Cloudflare Tunnel + Cloudflare Access:** a real HTTPS address with a login in front, reachable from anywhere. Needs a domain
  and a Cloudflare account, so more setup than Tailscale. Keep the app password as well.
- **Same Wi-Fi only, without Tailscale:** works with steps 2 to 5 minus Tailscale, but the password then crosses your Wi-Fi in
  plain text. Not recommended.
- **Never** forward a port on your router to this dashboard.

## If it does not work

1. In WSL: `curl http://127.0.0.1:8787/healthz` should print `{"ok":true}`. If not, the server is not running.
2. On the PC (not WSL): `curl.exe http://127.0.0.1:8787/healthz`. If this fails but step 1 works, mirrored networking is not active.
3. From the phone, check Tailscale shows the PC as connected, and use the name or the `100.x.y.z` address from the app.
4. Check both firewall rules exist (step 3).
5. A 401 means the server is reachable and the credentials are wrong; a 429 means too many failed attempts, wait 10 minutes.

## Taking access away

Change `VG_WEB_PASSWORD` and restart the dashboard; remove the phone from your Tailscale devices list; or stop the scheduled task.
