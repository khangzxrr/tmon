"""Many disks: the disk grid, big md arrays, and panels that are cut (never dropped) when the screen is too small."""
import unittest
from datetime import datetime

from tmon import demo, render


def disks(n, **changes):
    out = [{"dev": f"sd{chr(97 + i)}", "serial": f"SER{i:04d}", "temp": 38 + i % 5, "health": "PASSED"}
           for i in range(n)]
    for i, change in changes.items():
        out[int(i[1:])].update(change)
    return out


class ManyDisksTest(unittest.TestCase):
    def setUp(self):
        self.cfg = demo.config_()
        self.s = demo.DemoSource(self.cfg).snapshot()

    def screen(self, W=120, H=33):
        lines = render.build(W, H, self.cfg, self.s, datetime.now())
        self.assertEqual(len(lines), H)
        return render.ANSI.sub("", "\n".join(lines))

    def test_grid(self):
        self.s["slow"]["disks"] = disks(12)
        out = self.screen()
        self.assertIn("12 disks   ■ sda 38°  ■ sdb 39°  ■ sdc 40°  ■ sdd 41°", out)
        self.assertIn("           ■ sdi 41°  ■ sdj 42°  ■ sdk 38°  ■ sdl 39°", out)
        self.assertNotIn("SER0001", out)
        self.assertIn("/apps/files/", out)  # the traffic list still has room

    def test_disks_needing_attention_keep_a_full_row(self):
        self.s["slow"]["disks"] = disks(12, d4={"temp": 47}, d6={"health": "FAILED"})
        out = self.screen()
        self.assertIn("sde        SER0004   47 °C   SMART PASSED", out)
        self.assertIn("sdg        SER0006   39 °C   SMART FAILED", out)
        self.assertIn("10 disks   ■ sda 38°", out)
        self.assertNotIn("■ sdg", out)

    def test_threshold(self):
        self.s["slow"]["disks"] = disks(4)
        self.assertIn("sdd        SER0003", self.screen())
        self.s["slow"]["disks"] = disks(12)
        self.cfg["disks"]["compact_above"] = 12
        self.assertIn("sdl        SER0011", self.screen(H=45))

    def test_not_root_and_no_temperature(self):
        self.s["slow"]["disks"] = disks(6, d0={"health": "?", "temp": None}, d1={"health": "standby"})
        out = self.screen()
        self.assertIn("6 disks    ■ sda —°   ■ sdb 39°", out)

    def test_narrow_grid(self):
        self.s["slow"]["disks"] = disks(6)
        out = self.screen(W=80, H=80)
        self.assertIn("6 disks    ■ sda 38°  ■ sdb 39°  ■ sdc 40°  ■ sdd 41°  ■ sde 42°  ■ sdf 38°", out)

    def test_big_md_array(self):
        self.cfg["storage"][0].update(btrfs=False, mdadm=True, scrub=False, devices=0)
        self.s["health_enabled"] = False
        md = {"state": "active", "level": "raid6", "disks": 12, "working": 11, "failed": 1, "spares": 0,
              "failed_devs": ["sdg"], "status": "UUUUUU_UUUUU", "action": "recovery", "progress": 8.0}
        self.s["slow"]["mdadm"] = {"/srv/data": md}
        out = self.screen()
        self.assertIn("mdadm      ■ raid6 · recovery 8 % · DEGRADED 11/12 (slot 6)", out)
        self.assertIn("! mdadm /srv/data: degraded (11/12 disks, sdg failed)", out)
        # failed member flagged, but mdstat has no map / no names (older kernels)
        self.s["slow"]["mdadm"]["/srv/data"] = dict(md, status="", failed_devs=[], working=None, action=None)
        out = self.screen()
        self.assertIn("mdadm      ■ raid6 · DEGRADED 11/12\n", out.replace(" " * 10, "\n"))
        self.assertIn("degraded (None/12 disks, 1 failed)", out)
        self.s["slow"]["mdadm"]["/srv/data"] = dict(md, status="U" * 12, working=12, action=None)
        self.assertIn("mdadm      ■ raid6 · DEGRADED 12/12 ", self.screen())

    def test_panels_are_cut_not_dropped(self):
        self.s["slow"]["disks"] = disks(26) + [dict(d, dev="sd" + d["dev"]) for d in disks(14)]  # 40 disks
        self.cfg["disks"]["compact_above"] = 100  # one row each: 40 rows of disks
        for W, H in ((120, 33), (80, 60)):
            out = self.screen(W, H)
            for panel in ("SYSTEM", "STORAGE", "POWER", "SERVICES", "BACKUPS", "NEXT", "TRAFFIC"):
                self.assertIn(panel, out, (W, H))
            self.assertRegex(out, r"… \d+ more")

    def test_tiny_terminal_shows_what_fits(self):
        self.s["slow"]["disks"] = disks(40)
        self.assertEqual(len(render.build(60, 8, self.cfg, self.s, datetime.now())), 8)

    def test_shrink(self):
        panels = [["T1", "a", "b", "c", "d", "e"], ["T2", "x"]]
        cut = render.shrink(panels, 7, two=False)
        self.assertEqual(cut[1], ["T2", "x"])
        self.assertEqual(cut[0][:2], ["T1", "a"])
        self.assertIn("… 4 more", render.ANSI.sub("", cut[0][-1]))
        self.assertEqual(render.layout_height(cut, False), 7)
        # never below title + first line + "… more": a smaller budget stops there
        self.assertEqual(render.layout_height(render.shrink(panels, 3, two=False), False), 7)
        self.assertEqual(render.shrink(panels, 100, two=True), panels)


if __name__ == "__main__":
    unittest.main()
