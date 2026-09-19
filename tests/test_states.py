"""Renderer, config and traffic edge states: missing data, failures, odd configs."""
import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from tests import needs_tomllib
from tmon import config, demo, render, traffic


def text(cfg, s, W=120, H=60):
    return render.ANSI.sub("", "\n".join(render.build(W, H, cfg, s, datetime.now())))


class RenderStatesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = demo.config_()
        self.s = demo.DemoSource(self.cfg).snapshot()
        self.d = self.s["slow"]

    def test_no_data_anywhere(self):
        self.s["slow"] = {}
        self.s["health"] = None
        self.s["traffic"] = {"error": "Permission denied", "entries": [], "window": 300}
        out = text(self.cfg, self.s)
        self.assertIn("running the first health check", out)
        self.assertIn("no data from the UPS", out)
        self.assertIn("docker not answering", out)
        self.assertIn("access log: Permission denied", out)
        self.assertGreaterEqual(out.count("no data"), 8)

    def test_failures(self):
        self.s["health"] = dict(self.s["health"], fails=["one"], warns=[])
        self.d["ups"] = {"ups.status": "OB DISCHRG", "battery.charge": "40"}
        self.d["disks"][0]["health"] = "FAILED"
        self.d["disks"][1]["health"] = "?"
        self.d["btrfs"]["/srv/data"] = {"devices": 1, "missing": True, "errors": None, "profile": "RAID1"}
        self.d["storage"][0]["degraded"] = True
        self.d["freshness"] = {"Nightly": None, "Off-site": time.time() - 90 * 86400}
        self.d["scrub"] = {"/srv/data": {"started": time.time(), "status": "running", "errors": "no errors found"}}
        self.d["timers"]["Backup"] = None
        self.d["cameras"] = None
        self.s["checking"] = True
        out = text(self.cfg, self.s)
        for want in ("! 1 PROBLEM ", "checking now", "ON BATTERY · shutdown in 5 min", "SMART FAILED",
                     "SMART unknown (root?)", "1/2 disks · DEGRADED · errors unknown", "Nightly    ■ never",
                     "running · no errors found", "not scheduled", "Cameras    no data"):
            self.assertIn(want, out)

    def test_all_ok_and_charging(self):
        self.s["health"] = dict(self.s["health"], warns=[])
        self.d["ups"] = {"ups.status": "OL CHRG"}
        out = text(self.cfg, self.s)
        self.assertIn("ALL SYSTEMS OK", out)
        self.assertIn("on mains power · charging", out)
        self.assertIn("Battery    no data", out)

    def test_unscrubbed_and_multiple_filesystems(self):
        extra = dict(self.cfg["storage"][0], label="Backup", path="/srv/backup")
        cfg = dict(self.cfg, storage=self.cfg["storage"] + [extra], freshness=[])
        self.d["storage"].append(dict(self.d["storage"][0], label="Backup", path="/srv/backup"))
        self.d["scrub"] = {}
        out = text(cfg, self.s)
        self.assertIn("MAINTENANCE", out)
        self.assertIn("RAID fs", out)
        self.assertIn("Backup fs  no data", out)
        self.assertIn("Scrub Backup never", out)

    def test_docker_auto_groups_and_empty(self):
        cfg = dict(self.cfg, docker={"enabled": True, "projects": {}})
        self.d["containers"] = [["web", "web-1", "running", "Up (health: starting)"], ["", "lonely", "exited", "x"]]
        out = text(cfg, self.s)
        self.assertIn("■ web", out)
        self.assertIn("■ lonely", out)
        self.d["containers"] = []
        self.assertIn("no containers", text(cfg, self.s))
        self.assertNotIn("SERVICES", text(dict(cfg, docker={"enabled": False, "projects": {}},
                                               frigate={"container": ""}), self.s))
        self.assertIn(" SERVICES", text(dict(cfg, docker={"enabled": False, "projects": {}}), self.s))

    def test_live_problems(self):
        cfg = self.cfg
        self.d["containers"] = [["nextcloud", "nc", "running", "Up (unhealthy)"]]
        self.d["storage"][1]["missing"] = True
        self.d["btrfs"]["/srv/data"]["errors"] = True
        self.d["disks"][0]["health"] = "FAILED"
        problems = render.live_problems(cfg, self.d)
        for want in ("container nc is unhealthy", "Immich: no containers", "/ not mounted",
                     "btrfs /srv/data: device errors", "disk sda SMART FAILED"):
            self.assertIn(want, problems)
        self.d["containers"] = None
        self.assertIn("docker not answering", render.live_problems(cfg, self.d))

    def test_traffic_empty_and_long_paths(self):
        now = time.time()
        long_path = "/remote.php/dav/files/alice/" + "very-long-folder-name/" * 8
        self.s["traffic"] = {"error": None, "window": 300, "entries": [
            (now, "cloud", "GET", long_path, 200, "8.8.8.8", "internet"),
            (now, "cloud", "GET", "/app.js", 200, "8.8.8.8", "internet")]}
        out = text(self.cfg, self.s)
        self.assertIn("/remote.php/dav/fil", out)
        self.assertNotIn("app.js", out)
        self.s["traffic"]["entries"] = []
        self.assertIn("no requests in the last 5 min", text(self.cfg, self.s))

    def test_small_formats(self):
        self.assertEqual(render.size(5 * 2**30), "5.0 GB")
        self.assertEqual(render.size(500 * 2**30), "500 GB")
        self.assertEqual(render.rate(2048), "2 KB/s")
        self.assertEqual(render.ago(20), "<1 min")
        self.assertEqual(render.ago(5 * 86400), "5 days")
        self.assertEqual(render.when(datetime(2020, 1, 2, 3, 4).timestamp()), "2 Jan 03:04")


class ConfigErrorsTest(unittest.TestCase):
    def test_shapes(self):
        for bad in ({"ups": "x"}, {"storage": {"label": "a"}}, {"docker": {"enabled": "yes"}},
                    {"freshness": [{"label": "a", "glob": "/x", "level": "info"}]}):
            with self.assertRaises(config.ConfigError):
                config.merge(bad)

    @needs_tomllib
    def test_bad_toml_and_env(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write("[general\n")
        self.addCleanup(os.remove, f.name)
        with self.assertRaises(config.ConfigError):
            config.load(f.name)
        os.environ["TMON_CONFIG"] = "examples/config.toml"
        try:
            self.assertEqual(config.load()[1], "examples/config.toml")
        finally:
            del os.environ["TMON_CONFIG"]

    def test_panels_without_storage(self):
        cfg = config.merge({"storage": [], "docker": {"enabled": False}})
        self.assertEqual(config.panels(cfg), ["system"])


class TrafficEdgesTest(unittest.TestCase):
    def test_truncate_big_start_and_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "access.log")
            cfg = dict(config.merge({})["traffic"], access_log=path, format="combined")
            stamp = time.strftime("%d/%b/%Y:%H:%M:%S +0000", time.gmtime())
            line = f'8.8.8.8 - - [{stamp}] "GET / HTTP/1.1" 200 1 "-" "x"\n'
            with open(path, "w") as f:
                f.write("#" * (2**22 + 100) + "\n" + line)  # > 4 MiB: only the tail is read at start
            t = traffic.Traffic(cfg)
            self.assertEqual(len(t.poll()["entries"]), 1)
            with open(path, "w") as f:  # truncated in place (copytruncate)
                f.write(line)
            self.assertEqual(len(t.poll()["entries"]), 2)
            with mock.patch.object(t, "_read", side_effect=OSError(5, "Input/output error")):
                r = t.poll()  # a read error is reported, the entries so far are kept
            self.assertEqual((r["error"], len(r["entries"])), ("Input/output error", 2))
            self.assertIsNone(t.f)


if __name__ == "__main__":
    unittest.main()
