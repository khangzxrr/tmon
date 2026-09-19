"""--enable-kiosk / --disable-kiosk with systemctl mocked and the unit written to a temporary directory."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tmon import service
from tests.test_cli import run_main


class FakeSystemctl:
    """Records every command; `fail` = commands (as tuples) that exit non-zero."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, **kw):
        self.calls.append(tuple(cmd))
        code = 1 if tuple(cmd) in self.fail else 0
        return subprocess.CompletedProcess(cmd, code, stdout="journal lines\n", stderr="boom" if code else "")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = []
        self.sd = service.Systemd(unit_dir=self.dir.name, out=self.out.append, wait=0)
        self.unit = os.path.join(self.dir.name, "tmon.service")
        for p in (mock.patch("os.geteuid", return_value=0), mock.patch("shutil.which", return_value="/bin/systemctl")):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, fake, fn, *args):
        with mock.patch("subprocess.run", side_effect=fake):
            return fn(*args)

    def test_command(self):
        self.assertEqual(service.command("/usr/local/bin/tmon"), (["/usr/local/bin/tmon"], None))
        self.assertEqual(service.command("/home/me/tmon/tmon/__main__.py", "/usr/bin/python3"),
                         (["/usr/bin/python3", "-m", "tmon"], "/home/me/tmon"))

    def test_unit_text(self):
        text = service.unit_text("tty2", ["/opt/my apps/tmon", "--kiosk"], "/home/me/tmon")
        self.assertIn('ExecStart="/opt/my apps/tmon" --kiosk\n', text)
        self.assertIn("WorkingDirectory=/home/me/tmon\n", text)
        self.assertIn("TTYPath=/dev/tty2\n", text)
        self.assertIn("Conflicts=getty@tty2.service\n", text)
        self.assertNotIn("WorkingDirectory", service.unit_text("tty1", ["tmon"]))

    def test_enable(self):
        fake = FakeSystemctl()
        self.run_with(fake, self.sd.enable, "tty1", ["/usr/local/bin/tmon", "--kiosk"])
        self.assertEqual(service.installed_tty(self.dir.name), "tty1")
        self.assertEqual(fake.calls, [("systemctl", "daemon-reload"), ("systemctl", "disable", "getty@tty1.service"),
                                      ("systemctl", "enable", "tmon.service"), ("systemctl", "restart", "tmon.service"),
                                      ("systemctl", "is-active", "--quiet", "tmon.service")])
        self.assertIn("Alt+F2 … Alt+F6", self.out[-1])

    def test_move_to_another_console(self):
        self.run_with(FakeSystemctl(), self.sd.enable, "tty1", ["tmon"])
        fake = FakeSystemctl()
        self.run_with(fake, self.sd.enable, "tty3", ["tmon"])
        self.assertIn(("systemctl", "enable", "getty@tty1.service"), fake.calls)
        self.assertIn(("systemctl", "start", "getty@tty1.service"), fake.calls)
        self.assertIn(("systemctl", "disable", "getty@tty3.service"), fake.calls)
        self.assertIn("the other consoles", self.out[-1])

    def test_enable_failures(self):
        with self.assertRaises(service.ServiceError):
            self.sd.enable("console", ["tmon"])
        fake = FakeSystemctl(fail={("systemctl", "is-active", "--quiet", "tmon.service")})
        with self.assertRaises(service.ServiceError) as e:
            self.run_with(fake, self.sd.enable, "tty1", ["tmon"])
        self.assertIn("journal lines", str(e.exception))
        fake = FakeSystemctl(fail={("systemctl", "daemon-reload")})
        with self.assertRaises(service.ServiceError) as e:
            self.run_with(fake, self.sd.enable, "tty1", ["tmon"])
        self.assertIn("daemon-reload: boom", str(e.exception))

    def test_needs_root_and_systemd(self):
        with mock.patch("os.geteuid", return_value=1000), self.assertRaises(service.ServiceError):
            self.sd.check()
        with mock.patch("shutil.which", return_value=None), self.assertRaises(service.ServiceError):
            self.sd.check()

    def test_disable(self):
        self.run_with(FakeSystemctl(), self.sd.disable)
        self.assertEqual(self.out[-1], "kiosk mode is not enabled")
        self.run_with(FakeSystemctl(), self.sd.enable, "tty2", ["tmon"])
        fake = FakeSystemctl(fail={("systemctl", "disable", "--now", "tmon.service")})  # tolerated
        self.run_with(fake, self.sd.disable)
        self.assertFalse(os.path.exists(self.unit))
        self.assertEqual(fake.calls[-1], ("systemctl", "enable", "--now", "getty@tty2.service"))
        self.assertIn("tty2 shows a login prompt again", self.out[-1])
        with open(self.unit, "w") as f:  # a unit without TTYPath: removed, no console to give back
            f.write("[Service]\n")
        fake = FakeSystemctl()
        self.run_with(fake, self.sd.disable)
        self.assertEqual(self.out[-1], "kiosk mode removed")

    def test_cli(self):
        calls = []
        with mock.patch.object(service.Systemd, "enable", lambda s, *a: calls.append(("enable",) + a)), \
                mock.patch.object(service.Systemd, "disable", lambda s: calls.append(("disable",))):
            run_main("--enable-kiosk", "--demo", "--tty", "tty2")
            run_main("--disable-kiosk")
        self.assertEqual(calls[0][1], "tty2")
        self.assertEqual(calls[0][2][-2:], ["--kiosk", "--demo"])
        self.assertEqual(calls[1], ("disable",))
        with mock.patch.object(service.Systemd, "enable", side_effect=service.ServiceError("nope")):
            self.assertEqual(run_main("--enable-kiosk", "--demo")[0], "tmon: nope")
        with mock.patch.object(service.Systemd, "disable", side_effect=OSError("ro fs")):
            self.assertEqual(run_main("--disable-kiosk")[0], "tmon: ro fs")

    def test_cli_pins_the_config(self):
        calls = []
        with mock.patch.object(service.Systemd, "enable", lambda s, *a: calls.append(a)), \
                mock.patch("tmon.config.load", return_value=(service_cfg(), "examples/config.toml")):
            run_main("--enable-kiosk")
        self.assertEqual(calls[0][1][-3:], ["--kiosk", "-c", os.path.abspath("examples/config.toml")])


def service_cfg():
    from tmon import config
    return config.merge({})


if __name__ == "__main__":
    unittest.main()
