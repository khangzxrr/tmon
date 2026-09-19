"""Command line, --once, and both full-screen modes with the terminal calls mocked."""
import contextlib
import io
import os
import runpy
import sys
import unittest
from datetime import datetime
from unittest import mock

from tmon import cli, config, demo

ATTRS = [0, 0, 0, 0, 0, 0, []]


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(list(argv))
            code = 0
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Keys:
    """Stands in for the keyboard: select() says fd 0 is ready while there are keys left, os.read() returns them."""

    def __init__(self, *keys, winch=False):
        self.keys, self.winch, self.written = list(keys), winch, []

    def select(self, r, w, x, timeout):
        if self.winch and len(r) > 1:
            self.winch = False
            return [r[1]], [], []
        return ([0] if self.keys else []), [], []

    def read(self, fd, n):
        if fd == 0:
            return self.keys.pop(0)
        return b"."

    def write(self, fd, data):
        self.written.append(data)
        return len(data)


class MainTest(unittest.TestCase):
    def test_check_config(self):
        code, out, _ = run_main("--check-config", "-c", "examples/config.toml")
        self.assertEqual(code, 0)
        self.assertIn("panels: system, storage", out)
        code, out, _ = run_main("--check-config", "--demo")
        self.assertIn("traffic", out)
        self.assertIn("health: demo-health-check", out)
        with mock.patch.object(config, "SEARCH", []), mock.patch.dict(os.environ, {}, clear=False) as env:
            env.pop("TMON_CONFIG", None)
            code, out, _ = run_main("--check-config")
        self.assertIn("none found", out)
        self.assertIn("off (alerts from live data only)", out)

    def test_bad_config(self):
        code, _, _ = run_main("-c", "/nonexistent.toml", "--once")
        self.assertIn("No such file", code)

    def test_once(self):
        code, out, _ = run_main("--demo", "--once", "--size", "80x24", "--no-color")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 24)
        self.assertNotIn("\033", out)
        code, out, _ = run_main("--demo", "--once", "--size", "120x33")
        self.assertIn("\033[", out)
        code, _, _ = run_main("--demo", "--once", "--size", "big")
        self.assertIn("--size", code)

    def test_modes_dispatch(self):
        with mock.patch.object(cli, "interactive") as i, mock.patch.object(cli, "kiosk") as k:
            run_main("--demo")
            run_main("--demo", "--kiosk")
        i.assert_called_once()
        k.assert_called_once()
        with mock.patch.object(cli, "interactive", side_effect=OSError("tty gone")):
            code, _, err = run_main("--demo")
        self.assertEqual((code, err.strip()), (1, "tmon: tty gone"))

    def test_python_dash_m(self):
        with mock.patch.object(sys, "argv", ["tmon", "--demo", "--once", "--size", "80x24"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            runpy.run_module("tmon", run_name="__main__")
        self.assertIn("HOMELAB", out.getvalue())


class HelpersTest(unittest.TestCase):
    def test_output(self):
        self.assertEqual(cli.output(["a  ", "\033[31mb\033[0m"], True, False), "a\n\033[31mb\033[0m\n")
        self.assertEqual(cli.output(["a", "\033[31mb\033[0m"], False, True), "\033[1;1Ha\033[2;1Hb")

    def test_in_night(self):
        at = lambda h, m=0: datetime(2026, 9, 19, h, m)
        self.assertFalse(cli.in_night("", at(3)))
        self.assertTrue(cli.in_night("23:00-06:00", at(23, 30)))
        self.assertTrue(cli.in_night("23:00-06:00", at(2)))
        self.assertFalse(cli.in_night("23:00-06:00", at(12)))
        self.assertTrue(cli.in_night("01:00-05:00", at(3)))
        self.assertFalse(cli.in_night("01:00-05:00", at(6)))

    def test_setterm(self):
        with mock.patch("subprocess.run") as run:
            cli.setterm("--blank", "poke")
        self.assertEqual(run.call_args[0][0], ["setterm", "--blank", "poke"])


class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.cfg = demo.config_()
        self.source = demo.DemoSource(self.cfg)
        for target, value in (("termios.tcgetattr", lambda fd: list(ATTRS)), ("termios.tcsetattr", None),
                              ("shutil.get_terminal_size", lambda *a: os.terminal_size((120, 33))),
                              ("os.get_terminal_size", lambda *a: os.terminal_size((120, 33)))):
            p = mock.patch(target, side_effect=value) if value else mock.patch(target)
            p.start()
            self.addCleanup(p.stop)

    def patch_io(self, keys):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch("select.select", side_effect=keys.select))
        stack.enter_context(mock.patch("os.read", side_effect=keys.read))
        stack.enter_context(mock.patch("os.write", side_effect=keys.write))
        return stack

    def test_interactive(self):
        keys = Keys(b"r", b"x", b"q", winch=True)
        with self.patch_io(keys), mock.patch("os.isatty", return_value=True), mock.patch("signal.signal") as sig:
            cli.interactive(self.cfg, self.source, True)
        frames = [w for w in keys.written if b"TRAFFIC" in w]
        self.assertGreaterEqual(len(frames), 3)
        self.assertIn(b"\033[?1049h", keys.written[0])
        self.assertIn(b"\033[?1049l", keys.written[-1])
        self.assertTrue(any(b"checking now" in f for f in frames))
        handlers = {c[0][0]: c[0][1] for c in sig.call_args_list}
        with mock.patch("os.write") as w:
            handlers[cli.signal.SIGWINCH]()
        w.assert_called_once()
        with self.assertRaises(SystemExit):
            handlers[cli.signal.SIGTERM]()

    def test_interactive_ctrl_c_and_eof(self):
        for key in (KeyboardInterrupt, b""):
            keys = Keys(b"")
            if key is KeyboardInterrupt:
                keys.select = mock.Mock(side_effect=KeyboardInterrupt)
            with self.patch_io(keys), mock.patch("os.isatty", return_value=True), mock.patch("signal.signal"):
                cli.interactive(self.cfg, self.source, False)
            self.assertIn(b"\033[?1049l", keys.written[-1])

    def test_interactive_needs_a_terminal(self):
        with mock.patch("os.isatty", return_value=False), self.assertRaises(SystemExit):
            cli.interactive(self.cfg, self.source, True)

    def test_kiosk(self):
        cfg = dict(self.cfg, general=dict(self.cfg["general"], night="00:00-23:59", wake_minutes=0,
                                          font="Lat15-TerminusBold32x16", refresh=0))
        keys = Keys(b"r", b"R", b"")
        states = iter([["c1"], ["c1"], ["c2"]])
        framebuffer = iter([False, False])  # no monitor at first, then one is plugged in
        clock = iter(range(0, 10**6, 100))
        with self.patch_io(keys), mock.patch("os.ttyname", return_value="/dev/tty1"), \
                mock.patch("subprocess.run") as run, mock.patch.object(cli, "read", return_value="tty1"), \
                mock.patch("os.path.exists", side_effect=lambda p: next(framebuffer, True)), \
                mock.patch.object(cli.render, "live_problems", side_effect=lambda c, d: next(states, ["c2"])), \
                mock.patch("time.monotonic", side_effect=lambda: float(next(clock))), \
                mock.patch.object(self.source, "check_now", wraps=self.source.check_now) as check, \
                self.assertRaises(SystemExit) as exit_:
            cli.kiosk(cfg, self.source, True)
        self.assertEqual(exit_.exception.code, "tty hung up")
        calls = [c[0][0] for c in run.call_args_list]
        self.assertIn(["setfont", "-C", "/dev/tty1", "Lat15-TerminusBold32x16"], calls)
        self.assertIn(["setterm", "--blank", "force"], calls)
        self.assertTrue(any(b"Alt+F2" in w for w in keys.written))
        self.assertEqual(check.call_count, 3)  # problems changed once, "r" and "R" pressed

    def test_kiosk_problem_keeps_screen_on(self):
        cfg = dict(self.cfg, general=dict(self.cfg["general"], night="00:00-23:59", wake_minutes=0, refresh=0))
        self.source.health = dict(self.source.health, fails=["disk gone"])
        keys = Keys(b"")
        with self.patch_io(keys), mock.patch("os.ttyname", return_value="/dev/tty1"), \
                mock.patch("subprocess.run") as run, mock.patch.object(cli, "read", return_value="tty2"), \
                self.assertRaises(SystemExit):
            cli.kiosk(cfg, self.source, False)
        self.assertNotIn(["setterm", "--blank", "force"], [c[0][0] for c in run.call_args_list])


class DemoTest(unittest.TestCase):
    def test_source_methods(self):
        src = demo.DemoSource(demo.config_())
        src.start()
        src.run_once()
        self.assertFalse(src.snapshot()["checking"])
        src.check_now()
        self.assertTrue(src.snapshot()["checking"])


if __name__ == "__main__":
    unittest.main()
