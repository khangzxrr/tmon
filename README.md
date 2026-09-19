# tmon

A full-screen **terminal status monitor for home servers and NAS boxes**. One screen answers "is everything OK?"
and shows what's going on: CPU, memory, storage and RAID, disk SMART, UPS, Docker services, cameras, backups,
upcoming timers, and a live list of the web requests hitting your reverse proxy.

Run it in any terminal (over SSH, too), or let it own the **monitor plugged into the server** (tty1, no desktop, no
browser — ≈ 15 MB RAM, ~0 % CPU) with the monitor switched off at night.

```
 HOMELAB   up 3 days  ·  LAN 192.168.1.10 ·  public 203.0.113.83                                Sat 19 Sep 2026   22:11 
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  ■ OK · 1 WARNING    48 checks passed · checked 2 min ago
   • disk sdb at 44 °C (limit 45 °C)

 SYSTEM                                                      STORAGE
 CPU        ███░░░░░░░░░░░░░  21 %   45 °C                   RAID       ██████░░░░░░░░░░  36 %  1.3 TB / 3.6 TB
 Load       0.42  0.51  0.47   (12 threads)                  NVMe       ████░░░░░░░░░░░░  26 %  61 GB / 233 GB  38 °C
 Memory     ███████░░░░░░░░░  42 %   6.7 GB / 16 GB          btrfs      ■ RAID1 · 2/2 disks · no errors
 Swap       0 MB used                                        sda        ZA1B2C3D   41 °C   SMART PASSED
 Network    ↓   2.1 MB/s   ↑   498 KB/s   enp2s0             sdb        ZA4E5F6G   44 °C   SMART PASSED

 POWER  (UPS)                                                SERVICES   16/16 containers running
 Status     ■ on mains power                                 ■ Nextcloud ■ Immich    ■ Authentik ■ Frigate   
 Battery    ████████████████ 100 %                           ■ Caddy     ■ Postgres  ■ Beszel    ■ DDNS      
 Mains      226.0 V     load 18 %                            Cameras    ■ front_door 5 fps   ■ garden 5 fps

 BACKUPS                                                     NEXT
 Nightly    ■ today 13:11   9 h 0 min ago                    Backup     tomorrow 13:11   in 14 h 59 min
 Off-site   ■ 7 Sep 22:11   12 days ago                      RAID check tomorrow 00:11   in 1 h 59 min
 Scrub      ■ 1 Sep 22:11   finished · no errors found       Scrub      1 Oct 22:11   in 11 days

 TRAFFIC   last 5 min: 400 requests · 101 from the internet (2 IPs) · 19 refused (4xx) · 7 errors (5xx)
 22:11:00  cloud   GET      /apps/files/                                            ×122  200  192.168.1.1     LAN
 22:11:00  cloud   PROPFIND /remote.php/dav/files/alice/Photos/                      ×80  200  192.168.1.1     LAN
 22:10:59  cloud   PUT      /remote.php/dav/uploads/alice/…/1                        ×65  200  192.168.1.1     LAN
 22:10:54  auth    GET      /application/o/authorize/…                               ×14  200  192.168.1.1     LAN
 22:10:52  auth    POST     /api/v3/flows/executor/default-login/…                    ×9  200  192.168.1.1     LAN
 22:10:50  photos  GET      /api/assets/…/thumbnail…                                 ×44  200  192.168.1.1     LAN
 22:10:42  photos  GET      /.env                                                     ×8  404  192.168.1.1     LAN
 22:10:41  photos  GET      /api/timeline/buckets…                                   ×30  200  192.168.1.1     LAN
 22:10:31  photos  GET      /api/server/ping                                         ×19  200  192.168.1.1     LAN
 q = quit   ·   r = check now                                                                         updated 22:11:03 
```

*(`tmon --demo`: made-up data. The real thing is in colour: green/yellow/red dots, bars and banner.)*

- **Python ≥ 3.11 standard library only.** No pip dependencies; installs as a single executable file.
- **Read-only.** It runs `docker ps`, `upsc`, `smartctl -n standby` (never wakes a sleeping disk), `btrfs … show/stats`,
  `systemctl list-timers` and your health-check command; it changes nothing.
- **Degrades gracefully.** A missing tool or permission shows `no data` in that row and a line in the journal/stderr.
  Run it as root for SMART and btrfs error counters.

## Try it

```bash
git clone https://github.com/khangzxrr/tmon && cd tmon
python3 -m tmon --demo        # every panel, fake data
python3 -m tmon               # your machine: system, "/" and Docker containers without any config
sudo python3 -m tmon --once   # print one frame and exit
```

Keys: `q` quit · `r` run the health check now. Needs at least ~100 columns for the two-column layout
(narrower terminals stack the panels).

## Install

```bash
sudo ./install.sh             # /usr/local/bin/tmon + /etc/tmon/config.toml (kept if it exists) + tmon.service
sudo ./install.sh --kiosk     # … and show it on the local monitor (tty1) instead of the login prompt
sudo ./install.sh --no-kiosk  # tty1 back to a login prompt
sudo ./install.sh --uninstall
```

`install.sh` packs the `tmon/` package into one Python zipapp. Alternatives: `pipx install .`, or just
`python3 -m tmon` from the checkout.

## Configure

Edit `/etc/tmon/config.toml` (or `~/.config/tmon/config.toml`, `$TMON_CONFIG`, `-c FILE`). Every section is optional —
[`examples/config.toml`](examples/config.toml) documents every key. Validate with `tmon --check-config`.

| Panel | Shown when | Source |
|---|---|---|
| Banner + alerts | always | `[health] command` output, else tmon's own findings (below) |
| System | always | `/proc`, `/sys` (CPU %, load, memory, swap, CPU temperature, default-route network speed) |
| Storage | `[[storage]]` / `[disks]` | `statvfs`, `/proc/mounts`; `btrfs filesystem show/df`, `btrfs device stats --check`; `smartctl -H -A` |
| Power | `[ups] name` | `upsc` (Network UPS Tools) |
| Services | Docker installed / `[frigate]` | `docker ps -a` grouped by compose project; Frigate `/api/stats` fps per camera |
| Backups | `[[freshness]]` / `scrub = true` | newest file matching a glob, or a stamp file; `btrfs scrub status` |
| Next | `[timers]` | `systemctl list-timers` |
| Traffic | `[traffic] access_log` | Caddy JSON or nginx/Apache "combined" access log |

**Health checks.** Point `[health] command` at any script that prints one line per check starting with `OK`, `WARN`
or `FAIL` (colours are fine). The banner turns green / yellow / red, and each `WARN`/`FAIL` line is listed. It runs every
`interval_minutes`, on the `r` key, and **at once when tmon sees something change** (a container stops, the UPS goes
on battery, a filesystem disappears). A non-zero exit without `FAIL` lines counts as one problem.

```bash
#!/bin/sh
ok()   { echo "OK    $1"; }
fail() { echo "FAIL  $1"; }
curl -fs http://127.0.0.1:8080/health >/dev/null && ok "app answers" || fail "app down"
```

Without a health command, the banner reports what tmon sees itself: stopped or unhealthy containers (and configured
compose projects with no containers), UPS on battery, unmounted or degraded filesystems, btrfs device errors,
failed SMART.

**Traffic.** The last `window_minutes` of requests, newest first, one line per distinct request: time, site (first
label of the host name), method, path, `×N`, status (green / yellow 4xx / red 5xx), client IP and `LAN` / `internet`.
IDs in paths (UUIDs, long numbers and tokens) become `…` so 300 thumbnail requests are one line; page assets (`.js`,
`.css`, images, fonts) are counted but not listed; requests from the host itself and containers are hidden
(`internal`). **Query strings are never shown** (they often carry tokens), but paths are — including file names in
WebDAV URLs. Keep that in mind before putting the screen where visitors can read it. Caddy example:

```caddyfile
(access) {
	log {
		output file /var/log/caddy/access.log { roll_size 20MiB roll_keep 5 }
		format filter {
			request>headers delete
			resp_headers delete
		}
	}
}
cloud.example.com {
	import access
	reverse_proxy nextcloud:80
}
```

Behind NAT hairpinning, LAN clients using the public name all appear as your router's IP.

## Kiosk mode (monitor on the server)

`tmon --kiosk` is meant to be started by [`systemd/tmon.service`](systemd/tmon.service) on **tty1**:

- The console has **no shell**: Ctrl-C/Z/S do nothing, `r` re-runs the health check. **Alt+F2 … F6** are normal login
  consoles, Alt+F1 returns.
- `font` sets a console font (Terminus Bold 32×16 → 120 × 33 characters on 1920×1080). If the monitor was plugged in
  after boot, the framebuffer appears later; tmon notices and re-applies the font.
- `night = "23:00-06:00"` powers the monitor down (console blank + VESA powerdown); any key wakes it for
  `wake_minutes`; a problem keeps it on. It only blanks while tty1 is the visible console.
- Peek from SSH: `sudo cat /dev/vcs1 | fold -w 120`. Logs: `journalctl -u tmon`.

## Development

```bash
python3 -m unittest           # from the repo root
python3 -m tmon --demo --once --size 80x24
```

**Commit messages** start with a type — `feat`/`feature`, `fix`, `docs`, `refactor`, `perf`, `test`, `style`, `build`,
`ci`, `chore`, `revert` — and an optional scope: `fix(traffic): follow rotated logs`; `!` before `:` marks a breaking
change. Enable the local check once per clone with `git config core.hooksPath .githooks`; the
[commit messages](.github/workflows/commit-messages.yml) workflow rejects non-conforming commits on every push and pull
request.

`tmon/render.py` is pure (snapshot in, lines out), `collect.py` gathers data in background threads, `traffic.py`
follows the access log across rotations, `cli.py` owns the terminal. `demo.py` feeds the renderer fake data.

tmon started as the status screen of [a home NAS](https://github.com/khangzxrr/debian-nas-blueprint) (private).

## License

MIT
