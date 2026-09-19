import os
import tempfile
import unittest

from tmon import collect

SMART_ATA = """SMART overall-health self-assessment test result: PASSED
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
194 Temperature_Celsius     0x0022   038   045   000    Old_age   Always       -       38 (0 16 0 0 0)
"""
SMART_NVME = "SMART overall-health self-assessment test result: PASSED\nTemperature:                        41 Celsius\n"


class CollectTest(unittest.TestCase):
    def test_health_parse(self):
        out = "debian — up\n\n\033[1mSystem\033[0m\n  \033[32mOK  \033[0m  clock\n  \033[33mWARN\033[0m  disk warm\n" \
              "  \033[31mFAIL\033[0m  frigate not running\n[OK] other\nFAIL: thing"
        r = collect.parse_health(out, 2)
        self.assertEqual(r["ok"], 2)
        self.assertEqual(r["warns"], ["disk warm"])
        self.assertEqual(r["fails"], ["frigate not running", "thing"])

    def test_health_exit_code_without_fail_lines(self):
        self.assertEqual(collect.parse_health("OK a\n", 1)["fails"], ["health check exited with status 1"])

    def test_smart(self):
        self.assertEqual(collect.smart_temp(SMART_ATA), 38)
        self.assertEqual(collect.smart_temp(SMART_NVME), 41)
        self.assertEqual(collect.smart_health(SMART_ATA), "PASSED")
        self.assertEqual(collect.smart_health("Device is in STANDBY mode, exit(2)"), "standby")
        self.assertEqual(collect.smart_health(""), "?")

    def test_public_ip(self):
        self.assertEqual(collect.parse_public_ip("fl=1\nip=203.0.113.5\nts=1"), "203.0.113.5")
        self.assertEqual(collect.parse_public_ip("198.51.100.1\n"), "198.51.100.1")
        self.assertIsNone(collect.parse_public_ip("<html>"))

    def test_scrub(self):
        out = "UUID: x\nScrub started:    Tue Sep  1 03:00:01 2026\nStatus:           finished\n" \
              "Error summary:    no errors found\n"
        r = collect.parse_scrub(out)
        self.assertEqual((r["status"], r["errors"]), ("finished", "no errors found"))
        self.assertIsNotNone(r["started"])

    def test_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("2026-09-01_0230", "2026-09-02_0230", "2026-09-03_0230.partial"):
                os.mkdir(os.path.join(d, name))
            f = {"stamp": "", "glob": d + "/*", "name_format": "%Y-%m-%d_%H%M"}
            self.assertEqual(collect.newest(f), collect.datetime(2026, 9, 2, 2, 30).timestamp())
            stamp = os.path.join(d, "stamp")
            with open(stamp, "w") as fh:
                fh.write("1700000000\n")
            self.assertEqual(collect.newest({"stamp": stamp, "glob": ""}), 1700000000)
            self.assertIsNone(collect.newest({"stamp": d + "/nope", "glob": ""}))

    def test_counters_on_this_machine(self):
        c = collect.Counters()
        c.sample()
        s = c.sample()
        self.assertGreater(s["mem_total"], 0)
        self.assertTrue(0 <= s["cpu"] <= 100)


if __name__ == "__main__":
    unittest.main()
