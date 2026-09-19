"""Follows a web server access log (Caddy JSON or Apache/nginx "combined") and keeps the last few minutes."""
import collections
import ipaddress
import json
import os
import re
import time
from datetime import datetime

COMBINED = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d{3})')


def classify(ip, internal):
    if any(ip in net for net in internal):
        return "internal"  # the host itself, containers, reverse-proxy health checks
    return "internet" if ip.is_global else "LAN"


def clean_uri(uri):
    """Never show query strings: they often carry tokens."""
    path, sep, _ = uri.partition("?")
    return path + ("…" if sep else "")


def parse_line(line, fmt, site=""):
    """→ (ts, site, method, uri, status, ip) or None."""
    try:
        if fmt == "caddy":
            d = json.loads(line)
            r = d["request"]
            return (float(d["ts"]), r.get("host", "").split(".")[0] or site or "-", r["method"], clean_uri(r["uri"]),
                    int(d.get("status", 0)), ipaddress.ip_address(r.get("client_ip") or r["remote_ip"]))
        m = COMBINED.match(line.decode(errors="replace") if isinstance(line, bytes) else line)
        if not m:
            return None
        ip, ts, method, uri, status = m.groups()
        return (datetime.strptime(ts, "%d/%b/%Y:%H:%M:%S %z").timestamp(), site or "-", method, clean_uri(uri),
                int(status), ipaddress.ip_address(ip))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


class Traffic:
    """Survives log rotation (re-opens when the inode changes) and truncation."""

    def __init__(self, cfg):
        self.path, self.fmt, self.site = cfg["access_log"], cfg["format"], cfg["site"]
        self.window = cfg["window_minutes"] * 60
        self.internal = [ipaddress.ip_network(n) for n in cfg["internal"]]
        self.show_internal = cfg["show_internal"]
        self.f, self.buf = None, b""
        self.entries = collections.deque(maxlen=50000)

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError as e:
            return {"error": e.strerror, "entries": [], "window": self.window}
        try:
            if self.f is None or os.fstat(self.f.fileno()).st_ino != st.st_ino:
                first = self.f is None
                if self.f:
                    self._read()  # the rest of the file that was just rotated away
                    self.f.close()
                self.f, self.buf = open(self.path, "rb"), b""
                if first and st.st_size > 2**22:  # at start, only the tail can be within the window
                    self.f.seek(st.st_size - 2**22)
                    self.f.readline()
            elif st.st_size < self.f.tell():
                self.f.seek(0)
            self._read()
        except OSError as e:
            self.f = None
            return {"error": e.strerror, "entries": list(self.entries), "window": self.window}
        cutoff = time.time() - self.window
        while self.entries and self.entries[0][0] < cutoff:
            self.entries.popleft()
        return {"error": None, "entries": list(self.entries), "window": self.window}

    def _read(self):
        *lines, self.buf = (self.buf + self.f.read()).split(b"\n")
        cutoff = time.time() - self.window
        for line in lines:
            e = parse_line(line, self.fmt, self.site)
            if e is None or e[0] < cutoff:
                continue
            where = classify(e[5], self.internal)
            if where != "internal" or self.show_internal:
                self.entries.append(e[:5] + (str(e[5]), where))
