import unittest
from datetime import datetime

from tmon import config, demo, render


class RenderTest(unittest.TestCase):
    def frame(self, W, H, mutate=None, **kw):
        cfg = demo.config_()
        s = demo.DemoSource(cfg).snapshot()
        if mutate:
            mutate(cfg, s)
        return render.build(W, H, cfg, s, datetime.now(), **kw)

    def test_exact_size(self):
        for W, H in ((120, 33), (80, 24), (100, 40), (200, 60), (40, 10)):
            for kiosk in (False, True):
                lines = self.frame(W, H, kiosk=kiosk)
                self.assertEqual(len(lines), H)
                self.assertEqual([render.vlen(l) for l in lines], [W] * (H - 1) + [W - 1], (W, H))

    def test_demo_content(self):
        text = render.ANSI.sub("", "\n".join(self.frame(120, 33)))
        for word in ("HOMELAB", "SYSTEM", "STORAGE", "POWER", "SERVICES", "BACKUPS", "NEXT", "TRAFFIC", "RAID1",
                     "/api/assets/…/thumbnail…"):
            self.assertIn(word, text)
        self.assertNotIn(".js", text)

    def test_alerts_take_rows_from_traffic(self):
        def broken(cfg, s):
            s["health"] = dict(s["health"], fails=[f"problem {i}" for i in range(20)])
        text = render.ANSI.sub("", "\n".join(self.frame(120, 33, broken)))
        self.assertIn("20 PROBLEMS", text)
        self.assertIn("more: demo-health-check", text)
        traffic_rows = text.split("TRAFFIC", 1)[1].splitlines()[1:-1]
        self.assertEqual(len([l for l in traffic_rows if l.strip()]), 4)

    def test_live_problems_without_health_command(self):
        def no_health(cfg, s):
            s["health_enabled"] = False
            s["slow"]["containers"][0][2] = "exited"
            s["slow"]["ups"]["ups.status"] = "OB DISCHRG"
            s["slow"]["storage"][0]["missing"] = True
        text = render.ANSI.sub("", "\n".join(self.frame(120, 40, no_health)))
        self.assertIn("3 PROBLEMS", text)
        self.assertIn("ON BATTERY · shutdown in 5 min", text)
        self.assertIn("NOT MOUNTED", text)

    def test_minimal_config(self):
        cfg = config.merge({"docker": {"enabled": False}})
        s = {"fast": demo.DemoSource(demo.config_()).snapshot()["fast"], "slow": {}, "health": None,
             "health_enabled": False, "checking": False, "traffic": None}
        text = render.ANSI.sub("", "\n".join(render.build(120, 33, cfg, s, datetime.now())))
        self.assertIn("ALL SYSTEMS OK", text)
        self.assertIn("Root", text)
        self.assertNotIn("SERVICES", text)

    def test_formatting(self):
        self.assertEqual(render.size(3 * 2**40), "3.0 TB")
        self.assertEqual(render.rate(2 * 2**20), "2.0 MB/s")
        self.assertEqual(render.ago(3 * 3600 + 60), "3 h 1 min")
        self.assertEqual(render.vlen(render.fit("\033[31mabcdef\033[0m", 3)), 3)


if __name__ == "__main__":
    unittest.main()
