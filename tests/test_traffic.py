import json
import os
import tempfile
import time
import unittest

from tmon import config, traffic


def caddy(ts, ip, uri="/", status=200, host="cloud.example.com"):
    return json.dumps({"ts": ts, "status": status, "request": {"client_ip": ip, "method": "GET", "host": host,
                                                               "uri": uri}}) + "\n"


class TrafficTest(unittest.TestCase):
    def test_parse_combined(self):
        line = '203.0.113.9 - - [19/Sep/2026:10:00:00 +0000] "GET /index.php?token=secret HTTP/1.1" 404 12 "-" "x"'
        e = traffic.parse_line(line, "combined", "web")
        self.assertEqual(e[1:5], ("web", "GET", "/index.php…", 404))
        self.assertEqual(str(e[5]), "203.0.113.9")

    def test_query_strings_never_shown(self):
        e = traffic.parse_line(caddy(1.0, "1.2.3.4", "/s/abc?password=x"), "caddy")
        self.assertEqual(e[3], "/s/abc…")

    def test_garbage(self):
        self.assertIsNone(traffic.parse_line(b"not json", "caddy"))
        self.assertIsNone(traffic.parse_line("nonsense", "combined"))

    def test_follow_window_internal_and_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "access.log")
            cfg = dict(config.merge({})["traffic"], access_log=path)
            now = time.time()
            with open(path, "w") as f:
                f.write(caddy(now - 3600, "8.8.8.8"))  # too old
                f.write(caddy(now - 10, "127.0.0.1"))     # internal
                f.write(caddy(now - 5, "192.168.1.1"))
                f.write(caddy(now - 1, "9.9.9.9"))
            t = traffic.Traffic(cfg)
            r = t.poll()
            self.assertIsNone(r["error"])
            self.assertEqual([(e[5], e[6]) for e in r["entries"]], [("192.168.1.1", "LAN"), ("9.9.9.9", "internet")])
            os.rename(path, path + ".1")  # rotation: new inode
            with open(path, "w") as f:
                f.write(caddy(now, "1.1.1.1"))
            self.assertEqual(len(t.poll()["entries"]), 3)
            os.remove(path)
            self.assertEqual(t.poll()["error"], "No such file or directory")


if __name__ == "__main__":
    unittest.main()
