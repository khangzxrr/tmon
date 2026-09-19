"""Command line and the terminal: an interactive full-screen view (default), a kiosk that owns a Linux console
such as tty1 (--kiosk, run by tmon.service), or a single frame on stdout (--once)."""
import argparse
import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
from datetime import datetime

from . import __version__, config, demo, render, service
from .collect import LiveSource, log, read


def output(lines, color, positioned):
    if not color:
        lines = [render.ANSI.sub("", l) for l in lines]
    if positioned:  # absolute positioning per row: stray kernel messages get overwritten, nothing scrolls
        text = "".join(f"\033[{i + 1};1H{l}" for i, l in enumerate(lines))
    else:
        text = "\n".join(l.rstrip() for l in lines) + "\n"
    return text


def in_night(night, now):
    if not night:
        return False
    start, end = config.parse_night(night)
    minute = now.hour * 60 + now.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def setterm(*args):
    subprocess.run(["setterm", *args], stdin=subprocess.DEVNULL, check=False)  # acts on stdout = the console


def interactive(cfg, source, color):
    if not (os.isatty(0) and os.isatty(1)):
        sys.exit("tmon: not a terminal (use --once to print a single frame)")
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.signal(signal.SIGWINCH, lambda *_: os.write(wake_w, b"."))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    saved = termios.tcgetattr(0)
    attrs = termios.tcgetattr(0)
    attrs[3] &= ~(termios.ICANON | termios.ECHO)  # keys arrive at once; Ctrl-C still works
    termios.tcsetattr(0, termios.TCSANOW, attrs)
    os.write(1, b"\033[?1049h\033[?25l\033[2J")  # alternate screen, hide cursor
    try:
        source.start()
        while True:
            cols, rows = shutil.get_terminal_size()
            os.write(1, output(render.build(cols, rows, cfg, source.snapshot(), datetime.now()), color, True).encode())
            ready = select.select([0, wake_r], [], [], cfg["general"]["refresh"])[0]
            if wake_r in ready:
                os.read(wake_r, 64)
                os.write(1, b"\033[2J")
            if 0 in ready:
                keys = os.read(0, 64).lower()
                if not keys or b"q" in keys:
                    return
                if b"r" in keys:
                    source.check_now()
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(0, termios.TCSANOW, saved)
        os.write(1, b"\033[?25h\033[?1049l")


def kiosk(cfg, source, color):
    """Own a Linux virtual console: no signals from the keyboard, screen off at night, font for big monitors."""
    g = cfg["general"]
    tty = os.ttyname(1)
    attrs = termios.tcgetattr(0)  # no signals, no echo, no flow control (Ctrl-S would freeze the screen)
    attrs[0] &= ~(termios.IXON | termios.ICRNL)
    attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG | termios.IEXTEN)
    termios.tcsetattr(0, termios.TCSANOW, attrs)
    source.start()
    last_key, screen_on, last_blank, seen, framebuffer = time.monotonic(), True, 0.0, None, None
    while True:
        now = datetime.now()
        # Without a monitor at boot the console stays in VGA text mode; plugging one in creates the framebuffer
        # later, with the default tiny font. (Re)apply the font and screen settings whenever that changes.
        if os.path.exists("/sys/class/graphics/fb0") != framebuffer:
            framebuffer = os.path.exists("/sys/class/graphics/fb0")
            if g["font"]:
                subprocess.run(["setfont", "-C", tty, g["font"]], stdin=subprocess.DEVNULL, check=False)
            setterm("--blank", "0", "--powerdown", "0", "--powersave", "powerdown", "--cursor", "off")
            os.write(1, b"\033%G\033[2J")  # UTF-8 mode, clear
        s = source.snapshot()
        problems = render.live_problems(cfg, s["slow"])
        if seen is not None and problems != seen:
            source.check_now()  # something visibly broke or recovered: re-run the health check now
        seen = problems
        cols, rows = os.get_terminal_size(1)
        os.write(1, output(render.build(cols, rows, cfg, s, now, kiosk=True), color, True).encode())

        # screen off at night, unless a key was pressed recently or there is a problem
        failing = bool(s["health"] and s["health"]["fails"]) if s["health_enabled"] else bool(problems)
        want_on = not in_night(g["night"], now) or time.monotonic() - last_key < g["wake_minutes"] * 60 or failing
        if read("/sys/class/tty/tty0/active") == os.path.basename(tty):  # never blank someone's login console
            if want_on and not screen_on:
                setterm("--blank", "poke")
            elif not want_on and (screen_on or time.monotonic() - last_blank > 60):  # kernel messages unblank it
                setterm("--blank", "force")
                last_blank = time.monotonic()
            screen_on = want_on

        if select.select([0], [], [], g["refresh"])[0]:
            keys = os.read(0, 64)
            if not keys:
                sys.exit("tty hung up")
            last_key = time.monotonic()
            if b"r" in keys.lower():
                source.check_now()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tmon", description="Full-screen terminal status monitor for a home server.")
    ap.add_argument("-c", "--config", help="TOML config (default: $TMON_CONFIG, /etc/tmon/config.toml, "
                                           "~/.config/tmon/config.toml)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="print one frame to stdout and exit")
    mode.add_argument("--kiosk", action="store_true", help="own a Linux console (tty1): for tmon.service")
    mode.add_argument("--check-config", action="store_true", help="validate the config and list the panels")
    mode.add_argument("--enable-kiosk", action="store_true",
                      help="run tmon on a console (--tty) at boot instead of its login prompt: writes, enables and "
                           "starts tmon.service (root)")
    mode.add_argument("--disable-kiosk", action="store_true", help="remove tmon.service, login prompt back (root)")
    ap.add_argument("--tty", default="tty1", help="with --enable-kiosk: the console to take over (default: tty1)")
    ap.add_argument("--demo", action="store_true", help="made-up data for every panel")
    ap.add_argument("--no-health", action="store_true", help="with --once: skip the health command")
    ap.add_argument("--no-color", action="store_true", help="plain text (also: NO_COLOR=1)")
    ap.add_argument("--size", metavar="COLSxROWS", help="with --once: frame size (default: the terminal's)")
    ap.add_argument("--version", action="version", version=f"tmon {__version__}")
    args = ap.parse_args(argv)

    if args.disable_kiosk:
        try:
            return service.Systemd().disable()
        except (service.ServiceError, OSError) as e:
            sys.exit(f"tmon: {e}")
    try:
        cfg, path = (demo.config_(), "(demo)") if args.demo else config.load(args.config)
    except config.ConfigError as e:
        sys.exit(f"tmon: {e}")
    if args.check_config:
        print(f"config: {path or 'none found, using defaults'}")
        print(f"panels: {', '.join(config.panels(cfg))}"
              f"{', traffic' if cfg['traffic']['access_log'] else ''}")
        print(f"health: {cfg['health']['command'] or 'off (alerts from live data only)'}")
        return
    if args.enable_kiosk:
        cmd, workdir = service.command()
        # The service runs exactly what was just validated: this tmon, this config (or the demo).
        cmd += ["--kiosk"] + (["--demo"] if args.demo else ["-c", os.path.abspath(path)] if path else [])
        try:
            return service.Systemd().enable(args.tty, cmd, workdir)
        except (service.ServiceError, OSError) as e:
            sys.exit(f"tmon: {e}")
    color = not args.no_color and "NO_COLOR" not in os.environ
    source = demo.DemoSource(cfg) if args.demo else LiveSource(cfg)

    if args.once:
        cols, rows = shutil.get_terminal_size((120, 33))
        if args.size:
            try:
                cols, rows = (int(x) for x in args.size.lower().split("x"))
            except ValueError:
                sys.exit("tmon: --size must look like 120x33")
        source.run_once(health=not args.no_health)
        sys.stdout.write(output(render.build(cols, rows, cfg, source.snapshot(), datetime.now()), color, False))
        return
    try:
        (kiosk if args.kiosk else interactive)(cfg, source, color)
    except OSError as e:
        log(f"tmon: {e}")
        sys.exit(1)
