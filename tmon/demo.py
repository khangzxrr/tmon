"""--demo: every panel filled with made-up data, to try tmon anywhere and to take screenshots. Only documentation IPs
and invented names here: never real hosts, addresses or disk serials."""
import math
import random
import time

from . import config

CONFIG = {
    "general": {"title": "HOMELAB"},
    "health": {"command": "demo-health-check"},
    "network": {"public_ip_url": "https://1.1.1.1/cdn-cgi/trace"},
    "storage": [{"label": "RAID", "path": "/srv/data", "require_mount": True, "btrfs": True, "devices": 2,
                 "scrub": True},
                {"label": "NVMe", "path": "/", "temp_sensor": "nvme"}],
    "disks": {"glob": "/dev/disk/by-id/ata-*"},
    "ups": {"name": "ups@localhost", "on_battery_note": "shutdown in 5 min"},
    "docker": {"enabled": True, "projects": {"nextcloud": "Nextcloud", "immich": "Immich", "authentik": "Authentik",
                                             "frigate": "Frigate", "caddy": "Caddy", "postgres": "Postgres",
                                             "beszel": "Beszel", "ddns": "DDNS"}},
    "frigate": {"container": "frigate"},
    "freshness": [{"label": "Nightly", "glob": "/srv/data/backups/*", "max_age_hours": 26},
                  {"label": "Off-site", "stamp": "/var/lib/offsite/last-success", "max_age_hours": 720,
                   "level": "warn"}],
    "timers": {"units": {"backup.timer": "Backup", "raid-check.timer": "RAID check", "scrub.timer": "Scrub"}},
    "traffic": {"access_log": "/var/log/caddy/access.log"},
}
PATHS = [("cloud", "GET", "/apps/files/"), ("cloud", "PROPFIND", "/remote.php/dav/files/alice/Photos/"),
         ("cloud", "PUT", "/remote.php/dav/uploads/alice/web-file-upload-3f2a9c81d0e4b7a6/1"),
         ("photos", "GET", "/api/assets/5b0e7a4c-9d21-4f6e-8a3b-c1d2e3f4a5b6/thumbnail…"),
         ("photos", "GET", "/api/timeline/buckets…"), ("photos", "GET", "/api/server/ping"),
         ("auth", "GET", "/application/o/authorize/…"), ("auth", "POST", "/api/v3/flows/executor/default-login/…"),
         ("cam", "GET", "/api/review…"), ("mon", "GET", "/api/collections/systems/records…"),
         ("cloud", "GET", "/wp-login.php"), ("photos", "GET", "/.env")]
CLIENTS = [("192.168.1.1", "LAN")] * 6 + [("203.0.113.24", "internet"), ("198.51.100.7", "internet")]


def config_():
    return config.merge(CONFIG)


class DemoSource:
    def __init__(self, cfg):
        self.cfg, self.t0 = cfg, time.time()
        now = time.time()
        rnd = random.Random(1)
        self.traffic = []
        for i in range(400):
            site, method, path = PATHS[min(int(rnd.expovariate(0.35)), len(PATHS) - 1)]
            ip, where = rnd.choice(CLIENTS)
            status = 404 if path in ("/wp-login.php", "/.env") else rnd.choice([200] * 40 + [302, 401, 500])
            self.traffic.append((now - 290 + i * 0.72, site, method, path, status, ip, where))
        self.checking_until = 0.0
        self.health = {"time": now - 140, "ok": 48, "fails": [], "warns": ["disk sdb at 44 °C (limit 45 °C)"]}

    def start(self):
        pass

    def run_once(self, health=True):
        pass

    def check_now(self):
        self.checking_until = time.time() + 3
        self.health = dict(self.health, time=time.time() + 3)

    def snapshot(self):
        t = time.time() - self.t0
        wave = (math.sin(t / 7) + 1) / 2
        gb = 2**30
        fast = {"cpu": 6 + 30 * wave, "rx": 180_000 + 4e6 * wave, "tx": 60_000 + 9e5 * (1 - wave), "iface": "enp2s0",
                "threads": 12, "cpu_temp": 41 + round(8 * wave), "load": ["0.42", "0.51", "0.47"],
                "uptime": 3 * 86400 + 5 * 3600 + t, "mem_total": 16 * gb, "mem_avail": 9.3 * gb,
                "swap_total": 4 * gb, "swap_used": 0}
        now = time.time()
        containers = [[p, f"{p}-{i}", "running", "Up 3 days (healthy)"] for p in self.cfg["docker"]["projects"]
                      for i in range(2)]
        slow = {"network": {"lan": "192.168.1.10", "public": "203.0.113.83"},
                "storage": [{"label": "RAID", "path": "/srv/data", "missing": False, "degraded": False,
                             "used": 1.31 * 2**40, "total": 3.64 * 2**40, "temp": None},
                            {"label": "NVMe", "path": "/", "missing": False, "degraded": False,
                             "used": 61 * gb, "total": 233 * gb, "temp": 38}],
                "btrfs": {"/srv/data": {"devices": 2, "missing": False, "errors": False, "profile": "RAID1"}},
                "disks": [{"dev": "sda", "serial": "ZA1B2C3D", "temp": 41, "health": "PASSED"},
                          {"dev": "sdb", "serial": "ZA4E5F6G", "temp": 44, "health": "PASSED"}],
                "ups": {"ups.status": "OL", "battery.charge": "100", "input.voltage": "226.0", "ups.load": "18"},
                "containers": containers, "cameras": {"front_door": 5.0, "garden": 5.0},
                "freshness": {"Nightly": now - 9 * 3600, "Off-site": now - 12 * 86400},
                "scrub": {"/srv/data": {"started": now - 18 * 86400, "status": "finished",
                                        "errors": "no errors found"}},
                "timers": {"Backup": now + 15 * 3600, "RAID check": now + 2 * 3600, "Scrub": now + 12 * 86400}}
        shift = t % 300
        entries = [(e[0] + shift,) + e[1:] for e in self.traffic]
        return {"fast": fast, "slow": slow, "health": self.health, "health_enabled": True,
                "checking": time.time() < self.checking_until,
                "traffic": {"error": None, "entries": entries, "window": 300}}
