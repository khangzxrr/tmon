import os
import sys
import tempfile
import unittest
from unittest import mock

from tests import needs_tomllib
from tmon import config


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config.merge({})
        self.assertEqual([(s["label"], s["path"]) for s in cfg["storage"]], [("Root", "/")])
        self.assertFalse(cfg["storage"][0]["btrfs"])
        self.assertIn("system", config.panels(cfg))

    @needs_tomllib
    def test_example_config_is_valid(self):
        cfg, path = config.load("examples/config.toml")
        self.assertEqual(path, "examples/config.toml")
        self.assertEqual(cfg["disks"]["glob"], ["/dev/disk/by-id/ata-*"])
        self.assertIn("backups", config.panels(cfg))

    def test_unknown_keys_are_errors(self):
        for bad in ({"genral": {}}, {"ups": {"nmae": "x"}}, {"storage": [{"label": "a", "path": "/", "x": 1}]}):
            with self.assertRaises(config.ConfigError):
                config.merge(bad)

    def test_entry_validation(self):
        with self.assertRaises(config.ConfigError):
            config.merge({"storage": [{"label": "no path"}]})
        with self.assertRaises(config.ConfigError):
            config.merge({"freshness": [{"label": "both", "glob": "/a", "stamp": "/b"}]})
        with self.assertRaises(config.ConfigError):
            config.merge({"general": {"night": "late"}})
        with self.assertRaises(config.ConfigError):
            config.merge({"traffic": {"format": "json"}})

    def test_disk_glob_string(self):
        self.assertEqual(config.merge({"disks": {"glob": "/dev/sd?"}})["disks"]["glob"], ["/dev/sd?"])

    def test_python_3_10_without_tomllib(self):
        with mock.patch.dict(sys.modules, {"tomllib": None}):  # a None entry makes the import fail
            with self.assertRaises(config.ConfigError) as e:
                config.load("examples/config.toml")
        self.assertIn("needs Python 3.11 or newer", str(e.exception))
        with mock.patch.dict(sys.modules, {"tomllib": None}), mock.patch.object(config, "SEARCH", []), \
                mock.patch.dict(os.environ) as env:
            env.pop("TMON_CONFIG", None)
            self.assertEqual(config.load()[1], None)  # no file: defaults, no tomllib needed

    @needs_tomllib
    def test_not_utf8(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False) as f:
            f.write(b"title = '\xff'\n")
        self.addCleanup(os.remove, f.name)
        with self.assertRaises(config.ConfigError):
            config.load(f.name)

    def test_parse_night(self):
        self.assertEqual(config.parse_night("23:00-06:30"), (1380, 390))


if __name__ == "__main__":
    unittest.main()
