"""mdadm (/proc/mdstat) and ZFS (zpool status): parsing, collecting and what the screen shows."""
import contextlib
import io
import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from tmon import collect, config, demo, render

MDSTAT = """Personalities : [raid1] [raid6] [raid5] [raid4]
md0 : active raid1 sdb1[1] sda1[0]
      3906886464 blocks super 1.2 [2/2] [UU]
      bitmap: 0/30 pages [0KB], 65536KB chunk

md1 : active raid5 sde[3] sdd[1] sdc[0](F)
      7813774336 blocks super 1.2 level 5, 512k chunk, algorithm 2 [3/2] [_UU]
      [=>...................]  recovery =  8.5% (333000000/3906887168) finish=300.1min speed=198000K/sec

md2 : inactive sdf[0](S)
      3906887512 blocks super 1.2

unused devices: <none>
"""
ZPOOL_OK = """  pool: tank
 state: ONLINE
  scan: scrub repaired 0B in 00:10:02 with 0 errors on Sun Sep  6 00:34:03 2026
config:

        NAME        STATE     READ WRITE CKSUM
        tank        ONLINE       0     0     0
          mirror-0  ONLINE       0     0     0

errors: No known data errors
"""
ZPOOL_SCRUBBING = """  pool: tank
 state: ONLINE
  scan: scrub in progress since Sat Sep 19 22:00:01 2026
\t1.23T scanned at 1.2G/s, 400G issued at 400M/s, 3.6T total
\t0B repaired, 10.9% done, 02:14:03 to go
errors: No known data errors
"""
ZPOOL_RESILVER = """  pool: tank
 state: DEGRADED
  scan: resilver in progress since Sat Sep 19 22:00:01 2026
\t12.5% done
errors: 2 data errors, use '-v' for a list
"""
MOUNTS = """/dev/md0 /srv/md ext4 rw,relatime 0 0
/dev/md/gone /srv/gone ext4 rw 0 0
tank/data /srv/tank zfs rw,xattr 0 0
/dev/sda1 /srv/plain ext4 rw 0 0
"""


class ParseTest(unittest.TestCase):
    def test_mdstat(self):
        a = collect.parse_mdstat(MDSTAT)
        self.assertEqual({k: (v["state"], v["level"], v["disks"], v["working"], v["status"]) for k, v in a.items()},
                         {"md0": ("active", "raid1", 2, 2, "UU"), "md1": ("active", "raid5", 3, 2, "_UU"),
                          "md2": ("inactive", "?", 1, None, "")})
        self.assertEqual((a["md1"]["failed"], a["md1"]["action"], a["md1"]["progress"]), (1, "recovery", 8.5))
        self.assertEqual(a["md2"]["spares"], 1)
        self.assertEqual((a["md1"]["failed_devs"], a["md0"]["failed_devs"]), (["sdc"], []))
        self.assertEqual(collect.parse_mdstat(""), {})

    def test_md_name(self):
        self.assertEqual(collect.md_name("/dev/md0"), "md0")
        self.assertIsNone(collect.md_name("/dev/sda1"))

    def test_zpool(self):
        z = collect.parse_zpool_status(ZPOOL_OK)
        self.assertEqual((z["health"], z["errors"], z["action"]), ("ONLINE", "No known data errors", None))
        self.assertEqual(z["scrub"]["status"], "finished")
        self.assertEqual(z["scrub"]["errors"], "no errors found")
        self.assertEqual(datetime.fromtimestamp(z["scrub"]["started"]), datetime(2026, 9, 6, 0, 34, 3))
        z = collect.parse_zpool_status(ZPOOL_SCRUBBING)
        self.assertEqual((z["action"], z["progress"], z["scrub"]["status"], z["scrub"]["errors"]),
                         ("scrub", 10.9, "running", "10.9 % done"))
        z = collect.parse_zpool_status(ZPOOL_RESILVER)
        self.assertEqual((z["health"], z["action"], z["progress"]), ("DEGRADED", "resilver", 12.5))
        self.assertIsNone(z["scrub"]["status"])
        z = collect.parse_zpool_status(ZPOOL_OK.replace("with 0 errors", "with 3 errors"))
        self.assertEqual(z["scrub"]["errors"], "3 errors")
        z = collect.parse_zpool_status("  scan: scrub canceled on Sat Sep 19 22:00:01 2026\n")
        self.assertEqual((z["scrub"]["status"], z["health"], z["errors"]), ("canceled", "?", "?"))
        z = collect.parse_zpool_status("  scan: scrub in progress since whenever\n")
        self.assertEqual((z["scrub"]["started"], z["scrub"]["errors"]), (None, "in progress"))


class CollectTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        zpool = os.path.join(d.name, "zpool")
        with open(zpool, "w") as f:
            f.write(f"#!/bin/sh\n[ -n \"$ZPOOL_FAIL\" ] && exit 1\ncat <<'EOF'\n{ZPOOL_OK}EOF\n")
        os.chmod(zpool, 0o755)
        for p in (mock.patch.dict(os.environ, {"PATH": d.name + os.pathsep + os.environ["PATH"]}),
                  mock.patch.object(collect, "read", side_effect=self.fake_read),
                  mock.patch("os.path.exists", return_value=True),
                  mock.patch("os.statvfs", return_value=os.statvfs("/"))):
            p.start()
            self.addCleanup(p.stop)
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        entry = lambda label, path, **kw: dict({"label": label, "path": path}, **kw)
        self.cfg = config.merge({"docker": {"enabled": False}, "storage": [
            entry("MD", "/srv/md", mdadm=True, devices=2), entry("Gone", "/srv/gone", mdadm=True),
            entry("Plain", "/srv/plain", mdadm=True), entry("Tank", "/srv/tank", zfs=True, scrub=True),
            entry("NotZfs", "/srv/plain", zfs=True)]})

    @staticmethod
    def fake_read(path, default=""):
        return {"/proc/mounts": MOUNTS, "/proc/mdstat": MDSTAT}.get(path, default)

    def test_collect(self):
        c = collect.Collector(self.cfg)
        self.assertIn(c.zfs, [job for _, job in c.jobs])
        self.assertNotIn(c.scrub, [job for _, job in c.jobs])  # zfs scrubs come from zpool status
        c.run_all()
        md, zfs = c.data["mdadm"], c.data["zfs"]
        self.assertEqual(md["/srv/md"]["status"], "UU")
        self.assertIn("not in /proc/mdstat", md["/srv/gone"]["error"])
        self.assertIn("no md array mounted", md["/srv/plain"]["error"])
        self.assertEqual((zfs["/srv/tank"]["pool"], zfs["/srv/tank"]["health"]), ("tank", "ONLINE"))
        self.assertIn("no ZFS dataset", zfs["/srv/plain"]["error"])
        with mock.patch.dict(os.environ, {"ZPOOL_FAIL": "1"}):
            c.zfs()
        self.assertEqual(c.data["zfs"]["/srv/tank"]["error"], "zpool status tank failed")


class RenderTest(unittest.TestCase):
    def setUp(self):
        base = demo.config_()
        self.cfg = dict(base, storage=[
            dict(base["storage"][0], label="MD", path="/srv/md", btrfs=False, mdadm=True, scrub=False, devices=0),
            dict(base["storage"][0], label="Tank", path="/srv/tank", btrfs=False, zfs=True, devices=0)])
        self.s = demo.DemoSource(base).snapshot()
        self.s["health_enabled"] = False
        self.s["slow"]["storage"] = [dict(self.s["slow"]["storage"][0], label="MD", path="/srv/md"),
                                     dict(self.s["slow"]["storage"][0], label="Tank", path="/srv/tank")]
        arrays = collect.parse_mdstat(MDSTAT)
        self.md = {"/srv/md": arrays["md0"]}
        self.zfs = {"/srv/tank": dict(collect.parse_zpool_status(ZPOOL_OK), pool="tank")}
        self.arrays = arrays

    def screen(self):
        self.s["slow"]["mdadm"], self.s["slow"]["zfs"] = self.md, self.zfs
        return render.ANSI.sub("", "\n".join(render.build(120, 50, self.cfg, self.s, datetime.now())))

    def test_healthy(self):
        out = self.screen()
        self.assertIn("MD fs      ■ raid1 · 2/2 disks", out)
        self.assertIn("Tank fs    ■ tank ONLINE · no data errors", out)
        self.assertIn("Scrub      ■ 6 Sep 00:34   finished · no errors found", out)
        self.assertIn("ALL SYSTEMS OK", out)

    def test_broken(self):
        self.md = {"/srv/md": self.arrays["md1"]}
        self.zfs = {"/srv/tank": dict(collect.parse_zpool_status(ZPOOL_RESILVER), pool="tank")}
        out = self.screen()
        self.assertIn("MD fs      ■ raid5 · recovery 8 % · DEGRADED 2/3 [_UU]", out)
        self.assertIn("Tank fs    ■ tank DEGRADED · resilver 12 % · 2 data errors", out)
        self.assertIn("Scrub      never", out)
        self.assertIn("mdadm /srv/md: degraded (2/3 disks, sdc failed)", out)
        self.assertIn("zfs pool tank is DEGRADED", out)

    def test_inactive_errors_and_missing(self):
        self.md = {"/srv/md": self.arrays["md2"]}
        self.zfs = {"/srv/tank": {"error": "zpool status tank failed", "pool": "tank"}}
        out = self.screen()
        self.assertIn("INACTIVE · 1 disks · 1 spare", out)
        self.assertIn("! zpool status tank failed", out)
        self.assertIn("mdadm /srv/md: array inactive", out)
        self.assertIn("zfs /srv/tank: zpool status tank failed", out)
        self.md = {"/srv/md": {"error": "no md array mounted on /srv/md"}}
        self.zfs = {}
        out = self.screen()
        self.assertIn("mdadm /srv/md: no md array mounted", out)
        self.assertIn("Tank fs    no data", out)

    def test_single_array_label_rebuild_and_data_errors(self):
        self.cfg = dict(self.cfg, storage=self.cfg["storage"][1:])
        self.s["slow"]["storage"] = self.s["slow"]["storage"][1:]
        z = collect.parse_zpool_status(ZPOOL_OK.replace("No known data errors", "1 data errors"))
        self.zfs = {"/srv/tank": dict(z, pool="tank")}
        out = self.screen()
        self.assertIn("zfs        ■ tank ONLINE · 1 data errors", out)
        self.assertIn("zfs pool tank: 1 data errors", out)
        z = dict(collect.parse_zpool_status(ZPOOL_RESILVER), pool="tank", progress=None, health="ONLINE",
                 errors="No known data errors")
        self.zfs = {"/srv/tank": z}
        self.assertIn("tank ONLINE · resilvering · no data errors", self.screen())

    def test_md_expected_devices_and_check(self):
        m = dict(self.arrays["md0"], action="check", progress=40.0, status="", working=None)
        self.cfg["storage"][0]["devices"] = 3
        self.md = {"/srv/md": m}
        out = self.screen()
        self.assertIn("raid1 · check 40 % · 2/2 disks · expected 3", out)


class ConfigTest(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(config.ConfigError):
            config.merge({"storage": [{"label": "a", "path": "/", "btrfs": True, "zfs": True}]})
        with self.assertRaises(config.ConfigError):
            config.merge({"storage": [{"label": "a", "path": "/", "mdadm": True, "scrub": True}]})
        cfg = config.merge({"storage": [{"label": "a", "path": "/", "zfs": True, "scrub": True}]})
        self.assertIn("backups", config.panels(cfg))


if __name__ == "__main__":
    unittest.main()
