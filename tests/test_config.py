import unittest

from tmon import config


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config.merge({})
        self.assertEqual([(s["label"], s["path"]) for s in cfg["storage"]], [("Root", "/")])
        self.assertFalse(cfg["storage"][0]["btrfs"])
        self.assertIn("system", config.panels(cfg))

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

    def test_parse_night(self):
        self.assertEqual(config.parse_night("23:00-06:30"), (1380, 390))


if __name__ == "__main__":
    unittest.main()
