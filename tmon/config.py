"""Configuration: a TOML file merged over DEFAULTS. Every section is optional; with no file at all tmon shows the
system panel, the root filesystem and Docker containers (if Docker is installed)."""
import os
import shutil
import sys

# A new key goes in DEFAULTS/ENTRY_DEFAULTS, examples/config.toml and the README. Unknown keys are errors on purpose
# (typo detection).
SEARCH = ["/etc/tmon/config.toml", os.path.expanduser("~/.config/tmon/config.toml")]

DEFAULTS = {
    "general": {"title": "", "refresh": 2.0, "cpu_sensor": "", "night": "", "wake_minutes": 5.0, "font": ""},
    "health": {"command": "", "interval_minutes": 5.0, "timeout": 300.0},
    "network": {"public_ip_url": ""},
    "storage": [{"label": "Root", "path": "/"}],
    "disks": {"glob": [], "warn": 45, "crit": 50},
    "ups": {"name": "", "on_battery_note": ""},
    "docker": {"enabled": "auto", "projects": {}},
    "frigate": {"container": "", "url": "http://127.0.0.1:5000/api/stats"},
    "freshness": [],
    "timers": {"units": {}, "pattern": ""},
    "traffic": {"access_log": "", "format": "caddy", "window_minutes": 5.0, "site": "",
                "internal": ["127.0.0.0/8", "::1/128", "172.16.0.0/12"], "show_internal": False},
}
# Defaults for the entries of list sections ([[storage]], [[freshness]]).
ENTRY_DEFAULTS = {
    "storage": {"label": "", "path": "", "require_mount": False, "btrfs": False, "devices": 0, "scrub": False,
                "temp_sensor": "", "warn": 85, "crit": 95},
    "freshness": {"label": "", "glob": "", "stamp": "", "name_format": "", "max_age_hours": 26.0, "level": "crit"},
}
REQUIRED = {"storage": ("label", "path"), "freshness": ("label",)}


class ConfigError(Exception):
    pass


def _merge_table(name, default, value):
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = set(value) - set(default)
    if unknown:
        raise ConfigError(f"[{name}]: unknown key(s) {', '.join(sorted(unknown))}")
    return {**default, **value}


def merge(user):
    cfg = {}
    for section, default in DEFAULTS.items():
        value = user.get(section)
        if value is None:
            value = default
        if isinstance(default, list):
            if not isinstance(value, list):
                raise ConfigError(f"{section} must be a list of tables: [[{section}]]")
            entries = [_merge_table(f"[{section}]", ENTRY_DEFAULTS[section], e) for e in value]
            for e in entries:
                missing = [k for k in REQUIRED[section] if not e[k]]
                if missing:
                    raise ConfigError(f"[[{section}]] entry needs {', '.join(missing)}")
            cfg[section] = entries
        else:
            cfg[section] = _merge_table(section, default, value)
    unknown = set(user) - set(DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")
    return normalize(cfg)


def normalize(cfg):
    d = cfg["disks"]
    if isinstance(d["glob"], str):
        d["glob"] = [d["glob"]] if d["glob"] else []
    docker = cfg["docker"]
    if docker["enabled"] == "auto":
        docker["enabled"] = shutil.which("docker") is not None
    elif not isinstance(docker["enabled"], bool):
        raise ConfigError('[docker] enabled must be true, false or "auto"')
    if cfg["traffic"]["format"] not in ("caddy", "combined"):
        raise ConfigError('[traffic] format must be "caddy" or "combined"')
    for f in cfg["freshness"]:
        if bool(f["glob"]) == bool(f["stamp"]):
            raise ConfigError(f"[[freshness]] {f['label']!r}: set exactly one of glob or stamp")
        if f["level"] not in ("warn", "crit"):
            raise ConfigError(f"[[freshness]] {f['label']!r}: level must be \"warn\" or \"crit\"")
    night = cfg["general"]["night"]
    if night:
        try:
            parse_night(night)
        except ValueError:
            raise ConfigError(f'[general] night must look like "23:00-06:00", got {night!r}') from None
    return cfg


def parse_night(night):
    """"23:00-06:00" → (1380, 360) minutes after midnight."""
    start, end = night.split("-")
    return tuple(int(t.split(":")[0]) * 60 + int(t.split(":")[1]) for t in (start.strip(), end.strip()))


def load(path=None):
    """Load `path`, $TMON_CONFIG, or the first file of SEARCH that exists; defaults only if there is none."""
    path = path or os.environ.get("TMON_CONFIG") or next((p for p in SEARCH if os.path.exists(p)), None)
    if path is None:
        return merge({}), None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror}") from None
    # tomllib arrived in Python 3.11. Importing it only here lets 3.10 (Ubuntu 22.04) run --demo and the defaults.
    try:
        import tomllib
    except ImportError:
        raise ConfigError(f"{path}: reading a config file needs Python 3.11 or newer (this is "
                          f"{sys.version_info.major}.{sys.version_info.minor}); without a config file tmon runs "
                          f"with its defaults") from None
    try:
        return merge(tomllib.loads(data.decode())), path
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from None


def panels(cfg):
    """Which panels have something to show, in screen order."""
    out = ["system"]
    if cfg["storage"] or cfg["disks"]["glob"]:
        out.append("storage")
    if cfg["ups"]["name"]:
        out.append("power")
    if cfg["docker"]["enabled"] or cfg["frigate"]["container"]:
        out.append("services")
    if cfg["freshness"] or any(s["scrub"] for s in cfg["storage"]):
        out.append("backups")
    if cfg["timers"]["units"] or cfg["timers"]["pattern"]:
        out.append("next")
    return out
