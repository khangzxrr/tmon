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

- **Python standard library only.** Nothing to `pip install`; installs as a single executable file. Any Linux
  distribution (it reads `/proc` and `/sys`): [requirements](#requirements).
- **Read-only.** It runs `docker ps`, `upsc`, `smartctl -n standby` (never wakes a sleeping disk), `btrfs … show/stats`,
  `zpool status`,
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

## Requirements

**Linux and Python ≥ 3.10** — nothing else to try it. A config file needs **Python ≥ 3.11** (`tomllib`); on 3.10
(Ubuntu 22.04) tmon runs with its defaults and says so if it finds a config file.

| Distribution | `python3` | Works |
|---|---|---|
| Debian 12 / 13 | 3.11 / 3.13 | yes |
| Ubuntu 24.04 | 3.12 | yes |
| Ubuntu 22.04 | 3.10 | defaults and `--demo` only, no config file |
| Arch, Fedora | current | yes |

Panels call system tools only when they are configured; a missing tool shows `no data`, nothing breaks:

| Panel | Tool | Debian / Ubuntu | Arch | Fedora |
|---|---|---|---|---|
| Services, Cameras | `docker` or `podman` | `docker.io` / `podman` | `docker` / `podman` | Docker's repo / `podman` |
| Services: Kubernetes | `kubectl` (k3s brings its own) | k3s, `kubectl` | k3s, `kubectl` | k3s, `kubectl` |
| Power | `upsc` | `nut-client` | `nut` | `nut-client` |
| Storage: SMART | `smartctl` | `smartmontools` | `smartmontools` | `smartmontools` |
| Storage: btrfs, Scrub | `btrfs` | `btrfs-progs` | `btrfs-progs` | `btrfs-progs` |
| Storage: ZFS, Scrub | `zpool` | `zfsutils-linux` | `zfs-utils` (AUR / archzfs) | `zfs` (OpenZFS repo) |
| Storage: mdadm | none (`/proc/mdstat`) | | | |
| Next | `systemctl` | systemd | systemd | systemd |
| Kiosk mode | `setterm`, `setfont` | `util-linux`, `kbd` | `util-linux`, `kbd` | `util-linux`, `kbd` |

`install.sh` and kiosk mode need **systemd**. Without it (Alpine, Void, …) tmon still runs in a terminal; only the timers
panel and the tty1 service don't apply.

**Kiosk font names differ per distribution.** The example `Lat15-TerminusBold32x16` comes with Debian/Ubuntu's
`console-setup`. On Arch and Fedora install Terminus (`pacman -S terminus-font`, `dnf install terminus-fonts-console`)
and use `ter-v32b`. A font that doesn't exist only leaves the default tiny font; `ls /usr/share/consolefonts
/usr/share/kbd/consolefonts 2>/dev/null` lists what you have.

Keys: `q` quit · `r` run the health check now. Needs at least ~100 columns for the two-column layout
(narrower terminals stack the panels).

## Install

```bash
sudo ./install.sh             # /usr/local/bin/tmon + /etc/tmon/config.toml (kept if it exists)
sudo ./install.sh --kiosk     # … and show it on the local monitor (tty1): runs tmon --enable-kiosk
sudo ./install.sh --no-kiosk  # tty1 back to a login prompt: runs tmon --disable-kiosk
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
| Storage | `[[storage]]` / `[disks]` (a grid above `compact_above` disks) | `statvfs`, `/proc/mounts` (any filesystem); RAID health from `btrfs filesystem show/df` + `btrfs device stats --check`, `/proc/mdstat` (mdadm) or `zpool status` (ZFS); `smartctl -H -A` |
| Power | `[ups] name` | `upsc` (Network UPS Tools) |
| Services | Docker or Podman installed / a local Kubernetes cluster / `[frigate]` | `docker ps -a` / `podman ps -a` grouped by compose project; `kubectl get pods,nodes` grouped by namespace or app; Frigate `/api/stats` fps per camera |
| Backups | `[[freshness]]` / `scrub = true` | newest file matching a glob, or a stamp file; `btrfs scrub status` / `zpool status` |
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
compose projects with no containers), Kubernetes nodes NotReady and pods crash-looping, failing to pull, failed or
not ready after 5 minutes, UPS on battery, unmounted or degraded filesystems, btrfs device errors,
degraded or inactive md arrays, ZFS pools that aren't ONLINE or have data errors, failed SMART.

**Many disks.** Up to `compact_above` (4) disks, each gets a row with serial, temperature and SMART status. Above
that, healthy disks share rows as a grid (`■ sda 38°  ■ sdb 39° …`) and only disks that need attention keep a full row.
Big md arrays name the missing slot (`DEGRADED 11/12 (slot 6)`, numbered like `mdadm --detail`) instead of the full
`[UU_…]` map. If the panels still don't fit the screen, the tallest one is cut with a `… N more rows` line: every panel
keeps its title, none disappears.

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

```bash
sudo tmon --enable-kiosk             # tty1 shows tmon from now on, also after a reboot
sudo tmon --enable-kiosk --tty tty2  # another console (tty1 gets its login prompt back if it had tmon)
sudo tmon --disable-kiosk            # remove it; the console shows a login prompt again
```

`--enable-kiosk` checks the config, writes `/etc/systemd/system/tmon.service`, disables that console's login prompt
(`getty@tty1`: both would start at boot and conflict), enables and starts the service, and shows the journal if it
doesn't stay up. The service runs **the same tmon and the same config** you ran the command with: the installed
`tmon`, or `python3 -m tmon` from your checkout, with `-c <that config>` (or `--demo` for a showroom screen). Run it
again after moving the checkout or the config. It works with any install method and needs systemd.

On the console:

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
pip install coverage && python3 -m coverage run -m unittest && python3 -m coverage report
```

**Test coverage must stay ≥ 90 %** (lines and branches, `fail_under` in `pyproject.toml`): the
[tests](.github/workflows/tests.yml) workflow fails below it on Python 3.10, 3.11 and 3.13, and the `pre-push` hook checks it
before every push. Collectors are tested against stub commands on `PATH` (`tests/test_sources.py`), the terminal modes
with the terminal calls mocked (`tests/test_cli.py`).

**Commit messages** start with a type — `feat`/`feature`, `fix`, `docs`, `refactor`, `perf`, `test`, `style`, `build`,
`ci`, `chore`, `revert` — and an optional scope: `fix(traffic): follow rotated logs`; `!` before `:` marks a breaking
change. Enable the local checks (commit message, and tests + coverage before a push) once per clone with
`git config core.hooksPath .githooks`; the
[commit messages](.github/workflows/commit-messages.yml) workflow rejects non-conforming commits on every push and pull
request.

**Integration tests** ([integration](.github/workflows/integration.yml) workflow, on every pull request): on a
throwaway VM, `tests/integration/run.sh` installs tmon with `install.sh`, builds real btrfs, mdadm and ZFS arrays on
loop files, scrubs and breaks them, starts Podman containers and a k3s cluster with a pod that can't pull its image, and
enables/disables the kiosk service, checking the screen after each step. It is destructive and refuses to run
outside CI.

`main` is protected: changes land through pull requests once the `conventional`, `unittest (3.10)`,
`unittest (3.11)`, `unittest (3.13)` and `real-world` (integration) checks pass on a branch that is up to date with `main`. No force-pushes.

`tmon/render.py` is pure (snapshot in, lines out), `collect.py` gathers data in background threads, `traffic.py`
follows the access log across rotations, `cli.py` owns the terminal. `demo.py` feeds the renderer fake data.

tmon started as the status screen of [a home NAS](https://github.com/khangzxrr/debian-nas-blueprint) (private).

## License

MIT
