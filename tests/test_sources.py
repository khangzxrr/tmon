"""The collectors against stub commands (docker, upsc, smartctl, btrfs, systemctl) placed first on PATH."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tmon import collect, config
from tests.test_collect import SMART_ATA

STUBS = {
    "docker": """case "$1" in
  ps) [ -n "$DOCKER_FAIL" ] && { echo "cannot connect" >&2; exit 1; }
      printf 'web\\tweb-app\\trunning\\tUp 2 hours (healthy)\\nweb\\tweb-db\\texited\\tExited (1) 3 minutes ago\\n' ;;
  exec) [ -n "$FRIGATE_BAD" ] && echo '<html>' || echo '{"cameras": {"door": {"camera_fps": 5.0}}}' ;;
esac""",
    "upsc": """printf 'battery.charge: 97\\nups.status: OL CHRG\\ninput.voltage: 230\\nups.load: 12\\n'""",
    "smartctl": f"""cat <<'EOF'\n{SMART_ATA}EOF""",
    "btrfs": """case "$1 $2" in
  "filesystem show") printf 'Label: data\\n\\tdevid    1 size 3.64TiB\\n\\tdevid    2 size 3.64TiB\\n' ;;
  "filesystem df") printf 'Data, RAID1: total=1.00TiB, used=900.00GiB\\n' ;;
  "device stats") exit 0 ;;
  "scrub status") printf 'Scrub started:    Tue Sep  1 03:00:01 2026\\nStatus:           finished\\nError summary:    no errors found\\n' ;;
esac""",
    "systemctl": """[ -n "$TIMERS_BAD" ] && { echo nope; exit 0; }
echo '[{"unit": "backup.timer", "next": 1800000000000000}, {"unit": "scrub.timer", "next": 1700000000000000}, {"unit": "idle.timer", "next": null}]'""",
}


class Stubbed(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        bin_dir = os.path.join(self.dir.name, "bin")
        os.mkdir(bin_dir)
        for name, body in STUBS.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
        patcher = mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.disks = os.path.join(self.dir.name, "by-id")
        os.mkdir(self.disks)
        for name in ("ata-DISK_SER1", "ata-DISK_SER1-part1", "ata-DISK_SER2"):
            open(os.path.join(self.disks, name), "w").close()
        self.stamp = os.path.join(self.dir.name, "stamp")
        with open(self.stamp, "w") as f:
            f.write("1700000000")
        ip = os.path.join(self.dir.name, "ip")
        with open(ip, "w") as f:
            f.write("fl=1\nip=203.0.113.5\n")
        self.cfg = config.merge({
            "network": {"public_ip_url": "file://" + ip},
            "storage": [{"label": "Root", "path": "/", "btrfs": True, "devices": 2, "scrub": True,
                         "temp_sensor": "nope"},
                        {"label": "Gone", "path": os.path.join(self.dir.name, "missing"), "require_mount": True}],
            "disks": {"glob": os.path.join(self.disks, "ata-*")},
            "ups": {"name": "ups@localhost"},
            "docker": {"enabled": True},
            "frigate": {"container": "frigate"},
            "freshness": [{"label": "Stamp", "stamp": self.stamp}],
            "timers": {"units": {"backup.timer": "Backup", "missing.timer": "Missing"}},
        })
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)


class CollectorTest(Stubbed):
    def test_all_jobs(self):
        c = collect.Collector(self.cfg)
        c.run_all()
        d = c.data
        self.assertEqual(d["network"]["public"], "203.0.113.5")
        self.assertEqual([e["missing"] for e in d["storage"]], [False, True])
        self.assertIsNone(d["storage"][0]["temp"])
        self.assertEqual(d["ups"]["ups.status"], "OL CHRG")
        self.assertEqual([c[1] for c in d["containers"]], ["web-app", "web-db"])
        self.assertEqual(d["cameras"], {"door": 5.0})
        self.assertEqual(d["timers"], {"Backup": 1800000000.0, "Missing": None})
        self.assertEqual([(x["dev"], x["serial"], x["temp"], x["health"]) for x in d["disks"]],
                         [("ata-DISK_SER1", "SER1", 38, "PASSED"), ("ata-DISK_SER2", "SER2", 38, "PASSED")])
        self.assertEqual(d["btrfs"]["/"], {"devices": 2, "missing": False, "errors": False, "profile": "RAID1"})
        self.assertEqual(d["freshness"], {"Stamp": 1700000000})
        self.assertEqual(d["scrub"]["/"]["status"], "finished")

    def test_timer_pattern_sorted_by_next_run(self):
        cfg = config.merge({"timers": {"pattern": "*"}, "docker": {"enabled": False}})
        c = collect.Collector(cfg)
        c.timers()
        self.assertEqual(list(c.data["timers"]), ["scrub", "backup"])

    def test_broken_sources(self):
        c = collect.Collector(dict(self.cfg, network={"public_ip_url": "file:///nonexistent/ip"}))
        with mock.patch.dict(os.environ, {"DOCKER_FAIL": "1", "FRIGATE_BAD": "1", "TIMERS_BAD": "1"}):
            c.run_all()
        self.assertIsNone(c.data["network"]["public"])
        self.assertIsNone(c.data["containers"])
        self.assertIsNone(c.data["cameras"])
        self.assertIsNone(c.data["timers"])
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            c.containers()
            c.ups()
            self.assertEqual(collect.run("nonexistent-command"), "")
        self.assertIsNone(c.data["containers"])
        self.assertIsNone(c.data["ups"])
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            c.disks()
        self.assertIsNone(c.data["btrfs"]["/"]["errors"])

    def test_a_crashing_job_does_not_stop_the_others(self):
        c = collect.Collector(self.cfg)

        def storage():
            raise RuntimeError("boom")

        c.jobs = [(15, storage), (10, c.ups)]
        c.run_all()
        self.assertIn("ups", c.data)

    def test_background_thread(self):
        c = collect.Collector(self.cfg)
        c.start()
        deadline = time.time() + 10
        while "scrub" not in c.data and time.time() < deadline:
            time.sleep(0.05)
        self.assertIn("scrub", c.data)

    def test_helpers(self):
        self.assertEqual(collect.read("/nonexistent", "x"), "x")
        self.assertIsNone(collect.hwmon_temp("no-such-sensor"))
        with mock.patch("socket.socket", side_effect=OSError):
            self.assertIsNone(collect.lan_ip())
        with mock.patch.object(collect, "read", side_effect=lambda p, d="": "12345" if p.endswith("temp1_input")
                               else "fake" if p.endswith("/name") else d), \
                mock.patch("glob.glob", return_value=["/sys/class/hwmon/hwmon0"]):
            self.assertEqual(collect.hwmon_temp("fake"), 12)

    def test_freshness_mtime(self):
        with open(self.stamp, "w") as f:
            f.write("not a number")
        self.assertEqual(collect.newest({"stamp": self.stamp, "glob": ""}), os.path.getmtime(self.stamp))
        self.assertEqual(collect.newest({"stamp": "", "glob": self.stamp, "name_format": ""}),
                         os.path.getmtime(self.stamp))


class HealthTest(Stubbed):
    def test_command(self):
        h = collect.Health(config.merge({"health": {"command": "printf 'OK a\\nWARN b\\n'"}}))
        h.check()
        self.assertEqual((h.result["ok"], h.result["warns"]), (1, ["b"]))
        self.assertFalse(h.running)

    def test_timeout(self):
        h = collect.Health(config.merge({"health": {"command": "sleep 5", "timeout": 0.1}}))
        h.check()
        self.assertIn("did not finish", h.result["fails"][0])

    def test_wake(self):
        h = collect.Health(config.merge({"health": {"command": "echo OK x", "interval_minutes": 60}}))
        h.start()
        deadline = time.time() + 5
        while h.result is None and time.time() < deadline:
            time.sleep(0.02)
        first = h.result["time"]
        h.wake.set()
        while h.result["time"] == first and time.time() < deadline:
            time.sleep(0.02)
        self.assertGreater(h.result["time"], first)


class LiveSourceTest(Stubbed):
    def test_snapshot(self):
        log = os.path.join(self.dir.name, "access.log")
        with open(log, "w") as f:
            f.write(json.dumps({"ts": time.time(), "status": 200, "request": {
                "client_ip": "8.8.8.8", "method": "GET", "host": "a.example.com", "uri": "/"}}) + "\n")
        cfg = dict(self.cfg, health={"command": "echo FAIL x", "interval_minutes": 5, "timeout": 5},
                   traffic=dict(self.cfg["traffic"], access_log=log))
        src = collect.LiveSource(cfg)
        src.run_once()
        s = src.snapshot()
        self.assertEqual(s["health"]["fails"], ["x"])
        self.assertTrue(s["health_enabled"])
        self.assertEqual(len(s["traffic"]["entries"]), 1)
        self.assertIn("storage", s["slow"])
        src.start()
        src.check_now()
        self.assertTrue(src.health.wake.is_set() or src.health.running or src.health.result)

    def test_without_health_and_traffic(self):
        src = collect.LiveSource(dict(self.cfg, docker=dict(self.cfg["docker"], enabled=False)))
        src.run_once(health=False)
        src.check_now()  # no health command: nothing to wake
        s = src.snapshot()
        self.assertIsNone(s["health"])
        self.assertIsNone(s["traffic"])
        self.assertFalse(s["checking"])


if __name__ == "__main__":
    unittest.main()
