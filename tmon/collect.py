"""Data sources. Everything that needs a command or a disk runs in background threads so drawing never waits."""
import glob
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

from .traffic import Traffic

ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")


def log(msg):
    print(msg, file=sys.stderr, flush=True)  # under systemd, stderr goes to the journal


def run(*cmd, timeout=20):
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"{cmd[0]}: {e}")
        return ""


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def default_iface():
    routes = [l.split() for l in read("/proc/net/route").splitlines()[1:]]
    return next((r[0] for r in routes if len(r) > 1 and r[1] == "00000000"), None)


def lan_ip():
    """Source address of the default route (a UDP connect sends no packet)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def hwmon_temp(*names):
    for name in names:
        for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            if read(d + "/name") == name:
                t = read(d + "/temp1_input")
                if t.lstrip("-").isdigit():
                    return int(t) // 1000
    return None


# ---- fast: /proc and /sys, sampled on every redraw ---------------------------------------------------------------

class Counters:
    """CPU usage and network speed from deltas between two samples."""

    def __init__(self, cpu_sensor=""):
        self.prev = None
        self.sensors = (cpu_sensor,) if cpu_sensor else CPU_SENSORS

    def sample(self):
        iface = default_iface()
        cpu = [int(x) for x in read("/proc/stat").splitlines()[0].split()[1:]]
        idle, total = cpu[3] + cpu[4], sum(cpu[:8])
        net = [int(read(f"/sys/class/net/{iface}/statistics/{k}_bytes", "0")) for k in ("rx", "tx")] if iface \
            else [0, 0]
        now = time.monotonic()
        cpu_pct, rx, tx = 0.0, 0.0, 0.0
        if self.prev and self.prev[4] == iface:
            p_now, p_idle, p_total, p_net, _ = self.prev
            dt, dtotal = now - p_now, total - p_total
            cpu_pct = 100 * (1 - (idle - p_idle) / dtotal) if dtotal else 0.0
            rx, tx = (net[0] - p_net[0]) / dt, (net[1] - p_net[1]) / dt
        self.prev = (now, idle, total, net, iface)
        mem = {}
        for l in read("/proc/meminfo").splitlines():
            k, v = l.split(":", 1)
            mem[k] = int(v.split()[0]) * 1024
        return {"cpu": cpu_pct, "rx": rx, "tx": tx, "iface": iface, "threads": os.cpu_count(),
                "cpu_temp": hwmon_temp(*self.sensors), "load": read("/proc/loadavg").split()[:3],
                "uptime": float(read("/proc/uptime", "0").split()[0]),
                "mem_total": mem["MemTotal"], "mem_avail": mem.get("MemAvailable", mem.get("MemFree", 0)),
                "swap_total": mem.get("SwapTotal", 0), "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)}


# ---- slow: commands, refreshed in the background ----------------------------------------------------------------

class Collector(threading.Thread):
    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg, self.data = cfg, {}
        c = cfg
        self.jobs = [(900, self.network)]
        if c["storage"]:
            self.jobs.append((15, self.storage))
        if c["ups"]["name"]:
            self.jobs.append((10, self.ups))
        if c["docker"]["enabled"]:
            self.jobs.append((10, self.containers))
        if c["frigate"]["container"]:
            self.jobs.append((30, self.cameras))
        if c["timers"]["units"] or c["timers"]["pattern"]:
            self.jobs.append((60, self.timers))
        if c["disks"]["glob"] or any(s["btrfs"] for s in c["storage"]):
            self.jobs.append((120, self.disks))
        if c["freshness"]:
            self.jobs.append((120, self.freshness))
        if any(s["scrub"] for s in c["storage"]):
            self.jobs.append((600, self.scrub))

    def run_all(self):
        for _, job in self.jobs:
            self._safe(job)

    def run(self):
        due = [0.0] * len(self.jobs)
        while True:
            for i, (every, job) in enumerate(self.jobs):
                if time.monotonic() >= due[i]:
                    due[i] = time.monotonic() + every
                    self._safe(job)
            time.sleep(1)

    @staticmethod
    def _safe(job):
        try:
            job()
        except Exception as e:  # one broken source must not stop the others
            log(f"{job.__name__}: {e!r}")

    def network(self):
        public, url = None, self.cfg["network"]["public_ip_url"]
        if url:
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    public = parse_public_ip(r.read(4096).decode(errors="replace"))
            except OSError as e:
                log(f"public IP: {e}")
        self.data["network"] = {"lan": lan_ip(), "public": public}

    def storage(self):
        mounts = {}
        for l in read("/proc/mounts").splitlines():
            f = l.split()
            mounts[f[1].replace("\\040", " ")] = f[3].split(",")
        out = []
        for s in self.cfg["storage"]:
            e = {"label": s["label"], "path": s["path"], "missing": False, "degraded": False, "used": 0, "total": 0,
                 "temp": hwmon_temp(s["temp_sensor"]) if s["temp_sensor"] else None}
            if (s["require_mount"] and s["path"] not in mounts) or not os.path.exists(s["path"]):
                e["missing"] = True
            else:
                st = os.statvfs(s["path"])
                e["used"] = (st.f_blocks - st.f_bfree) * st.f_frsize
                e["total"] = e["used"] + st.f_bavail * st.f_frsize
                e["degraded"] = "degraded" in mounts.get(s["path"], [])
            out.append(e)
        self.data["storage"] = out

    def ups(self):
        out = run("upsc", self.cfg["ups"]["name"], timeout=10)
        self.data["ups"] = dict(l.split(": ", 1) for l in out.splitlines() if ": " in l) or None

    def containers(self):
        fmt = '{{.Label "com.docker.compose.project"}}\t{{.Names}}\t{{.State}}\t{{.Status}}'
        try:
            p = subprocess.run(["docker", "ps", "-a", "--format", fmt], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"docker: {e}")
            p = None
        if p is None or p.returncode:
            if p is not None:
                log(f"docker: {p.stderr.strip()}")
            self.data["containers"] = None
        else:
            self.data["containers"] = [l.split("\t") for l in p.stdout.splitlines() if l.count("\t") == 3]

    def cameras(self):
        f = self.cfg["frigate"]
        out = run("docker", "exec", f["container"], "curl", "-s", "--max-time", "5", f["url"])
        try:
            self.data["cameras"] = {n: c.get("camera_fps", 0) for n, c in json.loads(out)["cameras"].items()}
        except (ValueError, KeyError, AttributeError):
            self.data["cameras"] = None

    def timers(self):
        t = self.cfg["timers"]
        patterns = list(t["units"]) or [t["pattern"]]
        try:
            rows = json.loads(run("systemctl", "list-timers", "--all", "-o", "json", *patterns))
        except ValueError:
            self.data["timers"] = None
            return
        nxt = {r["unit"]: r["next"] / 1e6 for r in rows if r.get("next")}
        if t["units"]:
            self.data["timers"] = {label: nxt.get(unit) for unit, label in t["units"].items()}
        else:
            self.data["timers"] = {u.removesuffix(".timer"): ts for u, ts in sorted(nxt.items(), key=lambda x: x[1])}

    def disks(self):
        links = {}
        for pattern in self.cfg["disks"]["glob"]:
            for link in sorted(glob.glob(pattern)):
                if "-part" not in link:
                    links.setdefault(os.path.realpath(link), link)  # by-id has several links per disk
        disks = []
        for dev, link in sorted(links.items()):
            out = run("smartctl", "-n", "standby", "-H", "-A", link)
            disks.append({"dev": os.path.basename(dev), "serial": link.rsplit("_", 1)[-1] if "_" in link else "",
                          "temp": smart_temp(out), "health": smart_health(out)})
        self.data["disks"] = disks
        btrfs = {}
        for s in self.cfg["storage"]:
            if s["btrfs"] and os.path.ismount(s["path"]):
                show = run("btrfs", "filesystem", "show", s["path"])
                profile = re.search(r"^Data,\s*([^:]+):", run("btrfs", "filesystem", "df", s["path"]), re.M)
                try:
                    errors = subprocess.run(["btrfs", "device", "stats", "--check", s["path"]],
                                            stdin=subprocess.DEVNULL, capture_output=True, timeout=20).returncode != 0
                except (OSError, subprocess.TimeoutExpired):
                    errors = None
                btrfs[s["path"]] = {"devices": show.count("devid"), "missing": "missing" in show.lower(),
                                    "errors": errors, "profile": profile.group(1).strip() if profile else "?"}
        self.data["btrfs"] = btrfs

    def freshness(self):
        self.data["freshness"] = {f["label"]: newest(f) for f in self.cfg["freshness"]}

    def scrub(self):
        self.data["scrub"] = {s["path"]: parse_scrub(run("btrfs", "scrub", "status", s["path"]))
                              for s in self.cfg["storage"] if s["scrub"]}


def parse_public_ip(body):
    """Cloudflare's /cdn-cgi/trace ("ip=…" line) or any service answering with the bare address."""
    m = re.search(r"^ip=(\S+)", body, re.M)
    candidate = m.group(1) if m else body.strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def smart_temp(out):
    for l in out.splitlines():
        f = l.split()
        if len(f) > 9 and f[0] in ("194", "190") and f[9].isdigit():
            return int(f[9])
        if l.startswith("Temperature:") and len(f) > 1 and f[1].isdigit():  # NVMe
            return int(f[1])
    return None


def smart_health(out):
    if "STANDBY" in out:
        return "standby"
    if "PASSED" in out or ": OK" in out:
        return "PASSED"
    return "FAILED" if "FAILED" in out else "?"


def newest(f):
    """Timestamp of the newest match of a [[freshness]] entry, or None."""
    if f["stamp"]:
        content = read(f["stamp"])
        if content.isdigit():
            return int(content)
        try:
            return os.path.getmtime(f["stamp"])
        except OSError:
            return None
    times = []
    for p in glob.glob(f["glob"]):
        try:
            times.append(datetime.strptime(os.path.basename(p), f["name_format"]).timestamp()
                         if f["name_format"] else os.path.getmtime(p))
        except (ValueError, OSError):
            pass  # e.g. "<date>.partial" = still running or interrupted
    return max(times, default=None)


def parse_scrub(out):
    started = re.search(r"Scrub started:\s+(.+)", out)
    status = re.search(r"Status:\s+(\S+)", out)
    errors = re.search(r"Error summary:\s+(.+)", out)
    try:
        ts = datetime.strptime(started.group(1).strip(), "%a %b %d %H:%M:%S %Y").timestamp()
    except (AttributeError, ValueError):
        ts = None
    return {"started": ts, "status": status.group(1) if status else None,
            "errors": errors.group(1).strip() if errors else None}


# ---- health check command ---------------------------------------------------------------------------------------

LINE = re.compile(r"^\s*\[?(OK|WARN|FAIL)\]?\b[\s:]*(.*)$")


def parse_health(out, returncode=0):
    ok, warns, fails = 0, [], []
    for line in ANSI.sub("", out).splitlines():
        m = LINE.match(line)
        if not m:
            continue
        kind, text = m.groups()
        if kind == "OK":
            ok += 1
        else:
            (warns if kind == "WARN" else fails).append(text.strip())
    if returncode and not fails:
        fails.append(f"health check exited with status {returncode}")
    return {"time": time.time(), "ok": ok, "warns": warns, "fails": fails}


class Health(threading.Thread):
    """Runs the health command every interval, or at once when `wake` is set."""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        h = cfg["health"]
        self.command, self.interval, self.timeout = h["command"], h["interval_minutes"] * 60, h["timeout"]
        self.wake = threading.Event()
        self.result, self.running = None, False

    def check(self):
        self.running = True
        try:
            p = subprocess.run(self.command, shell=True, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=self.timeout)
            self.result = parse_health(p.stdout, p.returncode)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.result = parse_health(f"FAIL health check did not finish: {e}")
        self.running = False

    def run(self):
        while True:
            self.check()
            self.wake.wait(self.interval)
            self.wake.clear()


# ---- everything together ----------------------------------------------------------------------------------------

class LiveSource:
    def __init__(self, cfg):
        self.cfg = cfg
        self.collector = Collector(cfg)
        self.counters = Counters(cfg["general"]["cpu_sensor"])
        self.health = Health(cfg) if cfg["health"]["command"] else None
        self.traffic = Traffic(cfg["traffic"]) if cfg["traffic"]["access_log"] else None

    def start(self):
        self.collector.start()
        if self.health:
            self.health.start()

    def run_once(self, health=True):
        """Synchronous refresh for --once."""
        start = time.monotonic()
        self.counters.sample()  # CPU % and network speed need two samples
        self.collector.run_all()
        if self.health and health:
            self.health.check()
        time.sleep(max(0.0, 0.5 - (time.monotonic() - start)))

    def check_now(self):
        if self.health:
            self.health.wake.set()

    def snapshot(self):
        return {"fast": self.counters.sample(), "slow": self.collector.data,
                "health": self.health.result if self.health else None,
                "health_enabled": self.health is not None, "checking": bool(self.health and self.health.running),
                "traffic": self.traffic.poll() if self.traffic else None}
