"""--enable-kiosk / --disable-kiosk: run tmon on a Linux console (tty1 by default) as a systemd service, in place of
that console's login prompt. The other consoles (Alt+F2 …) keep their login prompts."""
import os
import re
import shutil
import subprocess
import sys
import time

UNIT = "tmon.service"
UNIT_DIR = "/etc/systemd/system"

TEMPLATE = """\
# Written by tmon --enable-kiosk; remove with tmon --disable-kiosk.
[Unit]
Description=tmon status screen on {tty}
Documentation=https://github.com/khangzxrr/tmon
# The console belongs to tmon instead of a login prompt; the other consoles still give a normal login.
Conflicts=getty@{tty}.service
After=getty@{tty}.service systemd-user-sessions.service console-setup.service docker.service nut-monitor.service

[Service]
ExecStart={exec_start}
{workdir}Environment=TERM=linux
StandardInput=tty
StandardOutput=tty
StandardError=journal
TTYPath=/dev/{tty}
TTYReset=yes
TTYVHangup=yes
Restart=always
RestartSec=5
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=multi-user.target
"""


class ServiceError(Exception):
    pass


def command(argv0=None, executable=None):
    """How the service starts this same tmon: the installed script/zipapp itself, or `python -m tmon` from the
    directory holding the package (a checkout or site-packages). → (argv, working directory or None)."""
    argv0 = os.path.abspath(argv0 or sys.argv[0])
    if os.path.basename(argv0) == "__main__.py":
        return [executable or sys.executable, "-m", "tmon"], os.path.dirname(os.path.dirname(argv0))
    return [argv0], None


def quote(arg):
    return f'"{arg}"' if re.search(r'[\s"\\]', arg) else arg  # systemd's ExecStart quoting


def unit_text(tty, argv, workdir=None):
    return TEMPLATE.format(tty=tty, exec_start=" ".join(quote(a) for a in argv),
                           workdir=f"WorkingDirectory={quote(workdir)}\n" if workdir else "")


def installed_tty(unit_dir=UNIT_DIR):
    try:
        with open(os.path.join(unit_dir, UNIT)) as f:
            m = re.search(r"^TTYPath=/dev/(tty\d+)$", f.read(), re.M)
            return m.group(1) if m else None
    except OSError:
        return None


class Systemd:
    def __init__(self, unit_dir=UNIT_DIR, out=print, wait=3.0):
        self.unit_dir, self.out, self.wait = unit_dir, out, wait

    def check(self):
        if os.geteuid() != 0:
            raise ServiceError("run as root (sudo)")
        if not shutil.which("systemctl"):
            raise ServiceError("kiosk mode needs systemd (no systemctl here); run tmon --kiosk from your own init")

    def systemctl(self, *args, check=True):
        p = subprocess.run(["systemctl", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if check and p.returncode:
            raise ServiceError(f"systemctl {' '.join(args)}: {p.stderr.strip() or f'exit {p.returncode}'}")
        return p.returncode

    def enable(self, tty, argv, workdir=None):
        if not re.fullmatch(r"tty\d+", tty):
            raise ServiceError(f"--tty must look like tty1, got {tty!r}")
        self.check()
        old = installed_tty(self.unit_dir)
        path = os.path.join(self.unit_dir, UNIT)
        with open(path, "w") as f:
            f.write(unit_text(tty, argv, workdir))
        self.out(f"wrote {path}")
        self.systemctl("daemon-reload")
        if old and old != tty:
            self.systemctl("enable", f"getty@{old}.service")  # moving to another console: give the old one back
        # Both units would start at boot and they conflict: the console belongs to tmon now.
        self.systemctl("disable", f"getty@{tty}.service")
        self.systemctl("enable", UNIT)
        self.systemctl("restart", UNIT)
        if old and old != tty:
            self.systemctl("start", f"getty@{old}.service")
        time.sleep(self.wait)
        if self.systemctl("is-active", "--quiet", UNIT, check=False):
            log = subprocess.run(["journalctl", "-u", UNIT, "-n", "20", "--no-pager"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True).stdout
            raise ServiceError(f"{UNIT} did not stay running:\n{log}")
        n = tty[3:]
        others = "Alt+F2 … Alt+F6" if n == "1" else "the other consoles"
        self.out(f"tmon runs on {tty} (Alt+F{n}) and starts at boot; logins: {others}. Undo: tmon --disable-kiosk")

    def disable(self):
        self.check()
        tty = installed_tty(self.unit_dir)
        path = os.path.join(self.unit_dir, UNIT)
        if tty is None and not os.path.exists(path):
            self.out("kiosk mode is not enabled")
            return
        self.systemctl("disable", "--now", UNIT, check=False)
        os.remove(path)
        self.systemctl("daemon-reload")
        if tty:
            self.systemctl("enable", "--now", f"getty@{tty}.service")
        self.out(f"kiosk mode removed{f'; {tty} shows a login prompt again' if tty else ''}")
