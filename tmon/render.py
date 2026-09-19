"""Turns a snapshot into screen lines. Pure: no I/O, so it can be tested and fed demo data."""
import re
import socket
import time
from datetime import datetime, timedelta

from . import config as config_mod

RESET, BOLD, GRAY = "\033[0m", "\033[1m", "\033[90m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"
ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
# Traffic list: page assets are counted but not listed; IDs in paths become … so that 200 thumbnail requests are one
# line "×200".
STATIC = re.compile(r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf)(…)?$", re.I)
ID = re.compile(r"(?<=/)([0-9a-fA-F-]{16,}|\d{3,}|(?=[^/]*\d)[A-Za-z0-9_-]{24,})(?=/|…|$)")  # slugs without digits stay
TWO_COLUMNS_MIN = 100


# ---- formatting -------------------------------------------------------------------------------------------------

def size(b):
    if b < 2**30:
        return f"{b / 2**20:.0f} MB"
    gb = b / 2**30
    if gb >= 1000:
        return f"{gb / 1024:.1f} TB"
    return f"{gb:.1f} GB" if gb < 10 else f"{gb:.0f} GB"


def rate(bps):
    if bps >= 2**20:
        return f"{bps / 2**20:.1f} MB/s"
    return f"{bps / 2**10:.0f} KB/s"


def ago(seconds):
    m = int(max(seconds, 0) // 60)
    if m < 1:
        return "<1 min"
    if m < 60:
        return f"{m} min"
    if m < 48 * 60:
        return f"{m // 60} h {m % 60} min"
    return f"{m // 1440} days"


def when(ts, now=None):
    d, today = datetime.fromtimestamp(ts), (now or datetime.now()).date()
    day = {today: "today", today - timedelta(days=1): "yesterday",
           today + timedelta(days=1): "tomorrow"}.get(d.date(), f"{d.day} {d:%b}")
    return f"{day} {d:%H:%M}"


def vlen(s):
    return len(ANSI.sub("", s))


def fit(s, width):
    """Pad or cut s to exactly `width` visible characters (colour codes don't count)."""
    out, n = [], 0
    for part in re.split(f"({ANSI.pattern})", s):
        if ANSI.fullmatch(part):
            out.append(part)
        else:
            take = part[:max(0, width - n)]
            out.append(take)
            n += len(take)
    return "".join(out) + RESET + " " * (width - n)


def dot(color):
    return f"{color}■{RESET}"


def level(value, warn, bad):
    return RED if value >= bad else YELLOW if value >= warn else GREEN


def bar(pct, color, width=16):
    filled = round(min(max(pct, 0), 100) / 100 * width)
    return f"{color}{'█' * filled}{GRAY}{'░' * (width - filled)}{RESET} {pct:3.0f} %"


def temp(t, warn, bad):
    return f"{GRAY}— °C{RESET}" if t is None else f"{level(t, warn, bad)}{t} °C{RESET}"


def title(text, extra=""):
    return f" {BOLD}{text}{RESET}{GRAY}{extra}{RESET}"


def row(label, content):
    return f" {GRAY}{label:<10}{RESET} {content}"  # long labels push the content right but never touch it


NO_DATA = f"{GRAY}no data{RESET}"


# ---- panels -----------------------------------------------------------------------------------------------------

def system_panel(f):
    mem_used = f["mem_total"] - f["mem_avail"]
    mem_pct = 100 * mem_used / f["mem_total"] if f["mem_total"] else 0
    swap = f"{size(f['swap_used'])} used" if f["swap_total"] else f"{GRAY}none{RESET}"
    return [title("SYSTEM"),
            row("CPU", f"{bar(f['cpu'], level(f['cpu'], 70, 90))}   {temp(f['cpu_temp'], 70, 85)}"),
            row("Load", f"{'  '.join(f['load'])}   {GRAY}({f['threads']} threads){RESET}"),
            row("Memory", f"{bar(mem_pct, level(mem_pct, 80, 90))}   {size(mem_used)} / {size(f['mem_total'])}"),
            row("Swap", swap),
            row("Network", f"↓ {rate(f['rx']):>10}   ↑ {rate(f['tx']):>10}   {GRAY}{f['iface'] or ''}{RESET}")]


def storage_panel(cfg, d, width=60):
    lines = [title("STORAGE")]
    st, btrfs = d.get("storage"), d.get("btrfs")
    if st is None:
        lines += [row(s["label"], NO_DATA) for s in cfg["storage"]]
    else:
        for s, e in zip(cfg["storage"], st):
            if e["missing"]:
                lines.append(row(e["label"], f"{RED}! {e['path']} NOT MOUNTED{RESET}"))
                continue
            pct = 100 * e["used"] / e["total"] if e["total"] else 0
            t = f"  {temp(e['temp'], 60, 70)}" if s["temp_sensor"] else ""
            lines.append(row(e["label"], f"{bar(pct, level(pct, s['warn'], s['crit']))}"
                                         f"  {size(e['used'])} / {size(e['total'])}{t}"))
    fs = [s for s in cfg["storage"] if s["btrfs"] or s["mdadm"] or s["zfs"]]
    for s in fs:
        kind = "btrfs" if s["btrfs"] else "mdadm" if s["mdadm"] else "zfs"
        label = kind if len(fs) == 1 else f"{s['label']} fs"
        b = (d.get(kind) or {}).get(s["path"])
        if b is None:
            lines.append(row(label, NO_DATA))
            continue
        if "error" in b:
            lines.append(row(label, f"{RED}! {b['error']}{RESET}"))
            continue
        if kind != "btrfs":
            lines.append(row(label, (md_row if kind == "mdadm" else zfs_row)(s, b)))
            continue
        degraded = any(e["degraded"] for e in st or [] if e["path"] == s["path"])
        count = f"{b['devices']}/{s['devices']} disks" if s["devices"] else f"{b['devices']} disks"
        broken = b["missing"] or degraded or (s["devices"] and b["devices"] != s["devices"]) or b["errors"]
        errors = "errors unknown (root?)" if b["errors"] is None else "ERRORS" if b["errors"] else "no errors"
        lines.append(row(label, f"{dot(RED if broken else GREEN)} {b['profile']} · {count}"
                                f"{' · DEGRADED' if degraded else ''} · {errors}"))
    return lines + disk_rows(cfg["disks"], d.get("disks") or [], width)


def disk_rows(dk, disks, width):
    """One row per disk; above `compact_above` disks, healthy ones share rows in a grid and only disks that need
    attention (SMART failed, at or above the warning temperature) keep a full row with their serial number."""
    def full(disk):
        color = GREEN if disk["health"] == "PASSED" else GRAY if disk["health"] in ("standby", "?") else RED
        health = "unknown (root?)" if disk["health"] == "?" else disk["health"]
        return row(disk["dev"], f"{disk['serial']}   {temp(disk['temp'], dk['warn'], dk['crit'])}"
                                f"   {color}SMART {health}{RESET}")

    if len(disks) <= dk["compact_above"]:
        return [full(x) for x in disks]
    attention = [x for x in disks if x["health"] == "FAILED" or (x["temp"] or 0) >= dk["warn"]]
    fine = [x for x in disks if x not in attention]
    cells = [(f"{x['dev']} {'—' if x['temp'] is None else x['temp']}°", GREEN if x["health"] == "PASSED" else GRAY)
             for x in fine]
    cell_w = max((len(c) for c, _ in cells), default=0) + 4  # "■ " + text + 2 spaces
    per = max(1, (width - 12) // cell_w)
    lines = [full(x) for x in attention]
    for i in range(0, len(cells), per):
        grid = "".join(f"{dot(color)} {text:<{cell_w - 2}}" for text, color in cells[i:i + per])
        lines.append(row(f"{len(fine)} disks" if i == 0 else "", grid))
    return lines


def md_row(s, m):
    if m["state"] != "active":
        spares = f" · {m['spares']} spare" if m["spares"] else ""
        return f"{dot(RED)} {RED}{m['state'].upper()}{RESET} · {m['disks']} disks{spares}"
    # Kept short: it shares a half-width column. Failed members are named in the banner (live_problems).
    working = m["working"] if m["working"] is not None else m["disks"] - m["failed"]
    degraded = working < m["disks"] or m["failed"]
    wrong_count = s["devices"] and m["disks"] != s["devices"]
    color = RED if degraded or wrong_count else YELLOW if m["action"] else GREEN
    where = ""
    if degraded and m["status"]:
        # small arrays: the whole map [U_]; big ones: the missing slots, numbered like `mdadm --detail` (RaidDevice)
        missing = ",".join(str(i) for i, c in enumerate(m["status"]) if c == "_")
        where = f" [{m['status']}]" if len(m["status"]) <= 6 else f" (slot {missing})" if missing else ""
    parts = [m["level"]]
    if m["action"]:
        parts.append(f"{m['action']} {m['progress']:.0f} %")
    parts.append(f"DEGRADED {working}/{m['disks']}{where}" if degraded else f"{working}/{m['disks']} disks")
    if wrong_count:
        parts.append(f"expected {s['devices']}")
    return f"{dot(color)} {' · '.join(parts)}"


def zfs_row(s, z):
    data_ok = z["errors"] == "No known data errors"
    color = RED if z["health"] != "ONLINE" or not data_ok else YELLOW if z["action"] == "resilver" else GREEN
    action = f" · {z['action']} {z['progress']:.0f} %" if z["action"] == "resilver" and z["progress"] is not None \
        else " · resilvering" if z["action"] == "resilver" else ""
    errors = "no data errors" if data_ok else z["errors"].split(",")[0]  # "2 data errors, use '-v' for a list"
    return f"{dot(color)} {z['pool']} {z['health']}{action} · {errors}"


def power_panel(cfg, d):
    ups = d.get("ups")
    lines = [title("POWER", "  (UPS)")]
    if ups is None:
        return lines + [row("Status", f"{RED}! no data from the UPS{RESET}")]
    flags = ups.get("ups.status", "").split()
    if "OB" in flags:
        note = cfg["ups"]["on_battery_note"]
        lines.append(row("Status", f"{RED}! ON BATTERY{f' · {note}' if note else ''}{RESET}"))
    else:
        lines.append(row("Status", f"{dot(GREEN)} on mains power{' · charging' if 'CHRG' in flags else ''}"))
    try:
        charge = float(ups["battery.charge"])
        lines.append(row("Battery", bar(charge, RED if charge < 50 else YELLOW if charge < 80 else GREEN)))
    except (KeyError, ValueError):
        lines.append(row("Battery", NO_DATA))
    lines.append(row("Mains", f"{ups.get('input.voltage', '?')} V     {GRAY}load{RESET} {ups.get('ups.load', '?')} %"))
    return lines


def stack_color(items):
    if not items or any(state != "running" or "unhealthy" in status for _, _, state, status in items):
        return RED
    return YELLOW if any("starting" in status for *_, status in items) else GREEN


def container_groups(cfg, containers):
    """[(label, [containers])]: the configured compose projects, or every project + standalone containers."""
    projects = cfg["docker"]["projects"]
    if projects:
        return [(label, [c for c in containers if c[0] == p]) for p, label in projects.items()]
    names = sorted({c[0] for c in containers if c[0]})
    return ([(p, [c for c in containers if c[0] == p]) for p in names] +
            [(c[1], [c]) for c in sorted(containers, key=lambda c: c[1]) if not c[0]])


def services_panel(cfg, d, width):
    lines = []
    if cfg["docker"]["enabled"]:
        containers = d.get("containers")
        if containers is None:
            lines += [title("SERVICES"), row("Docker", f"{RED}! docker not answering{RESET}")]
        else:
            running = sum(c[2] == "running" for c in containers)
            lines.append(title("SERVICES", f"   {running}/{len(containers)} containers running"))
            groups = container_groups(cfg, containers)
            cw = max((len(label) for label, _ in groups), default=8) + 3
            per = max(1, (width - 1) // cw)
            cells = [f"{dot(stack_color(items))} {label:<{cw - 2}}" for label, items in groups]
            lines += [" " + "".join(cells[i:i + per]) for i in range(0, len(cells), per)]
            if not groups:
                lines.append(row("", f"{GRAY}no containers{RESET}"))
    else:
        lines.append(title("SERVICES"))
    if cfg["frigate"]["container"]:
        cams = d.get("cameras")
        lines.append(row("Cameras", NO_DATA if cams is None else "   ".join(
            f"{dot(GREEN if fps >= 1 else RED)} {name} {fps:.0f} fps" for name, fps in cams.items())))
    return lines


def backups_panel(cfg, d, now):
    lines = [title("BACKUPS" if cfg["freshness"] else "MAINTENANCE")]
    fresh, scrubs, ts_now = d.get("freshness"), d.get("scrub") or {}, now.timestamp()
    for f in cfg["freshness"]:
        if fresh is None:
            lines.append(row(f["label"], NO_DATA))
            continue
        ts, old = fresh.get(f["label"]), RED if f["level"] == "crit" else YELLOW
        if ts is None:
            lines.append(row(f["label"], f"{dot(old)} never"))
        else:
            color = GREEN if ts_now - ts < f["max_age_hours"] * 3600 else old
            lines.append(row(f["label"], f"{dot(color)} {when(ts, now)}   {GRAY}{ago(ts_now - ts)} ago{RESET}"))
    scrubbed = [s for s in cfg["storage"] if s["scrub"]]
    for s in scrubbed:
        label = "Scrub" if len(scrubbed) == 1 else f"Scrub {s['label']}"
        sc = scrubs.get(s["path"]) if s["btrfs"] else ((d.get("zfs") or {}).get(s["path"]) or {}).get("scrub")
        sc = sc or {}
        if sc.get("started"):
            clean = sc.get("status") == "finished" and sc.get("errors") == "no errors found"
            running = sc.get("status") == "running"
            lines.append(row(label, f"{dot(YELLOW if running else GREEN if clean else RED)} {when(sc['started'], now)}"
                                    f"   {GRAY}{sc.get('status')} · {sc.get('errors')}{RESET}"))
        else:
            lines.append(row(label, f"{GRAY}never{RESET}"))
    return lines


def next_panel(d, now):
    timers = d.get("timers")
    lines = [title("NEXT")]
    if timers is None:
        return lines + [row("Timers", NO_DATA)]
    for label, ts in timers.items():
        lines.append(row(label, f"{GRAY}not scheduled{RESET}" if ts is None else
                     f"{when(ts, now)}   {GRAY}in {ago(ts - now.timestamp())}{RESET}"))
    return lines


def traffic_panel(t, rows, W):
    if t["error"] and not t["entries"]:
        return [title("TRAFFIC"), row("", f"{GRAY}access log: {t['error']}{RESET}")]
    visible, window = t["entries"], t["window"]
    outside = {e[5] for e in visible if e[6] == "internet"}
    refused = sum(400 <= e[4] < 500 for e in visible)
    failed = sum(e[4] >= 500 for e in visible)
    minutes = f"{window / 60:g} min"
    lines = [title("TRAFFIC", f"   last {minutes}: {len(visible)} requests · "
                              f"{sum(e[6] == 'internet' for e in visible)} from the internet "
                              f"({len(outside)} IP{'s' * (len(outside) != 1)}) · "
                              f"{refused} refused (4xx) · {failed} errors (5xx)")]
    groups = {}  # (site, method, path) → [newest entry, count]; dicts keep insertion order = newest first
    for e in reversed(visible):
        path = ID.sub("…", e[3])
        if STATIC.search(path):
            continue
        g = groups.setdefault((e[1], e[2], path), [e, 0])
        g[1] += 1
    shown = list(groups.items())[:rows]
    site_w = min(max((len(site) for (site, _, _), _ in shown), default=6), 12)
    path_w = max(W - 60 - site_w, 10)
    for (site, method, path), (e, count) in shown:
        if len(path) > path_w:
            path = path[:path_w // 2 - 1] + "…" + path[-(path_w - path_w // 2):]
        status = RED if e[4] >= 500 else YELLOW if e[4] >= 400 else GREEN
        client = f"{YELLOW if e[6] == 'internet' else GRAY}{e[5]:<15} {e[6]}{RESET}"
        lines.append(f" {GRAY}{datetime.fromtimestamp(e[0]):%H:%M:%S}{RESET}  {site[:site_w]:<{site_w}}  "
                     f"{method[:8]:<8} {path:<{path_w}} {f'×{count}' if count > 1 else '':>5}  {status}{e[4]}{RESET}"
                     f"  {client}")
    if not groups and rows > 0:
        lines.append(row("", f"{GRAY}no requests in the last {minutes}{RESET}"))
    return lines


# ---- problems ---------------------------------------------------------------------------------------------------

def live_problems(cfg, d):
    """What the fast sources see as broken. Shown as alerts when there is no health command; with one, a change
    triggers an immediate health check."""
    out = []
    containers = d.get("containers")
    if cfg["docker"]["enabled"] and containers is not None:
        out += [f"container {c[1]} is {c[2] if c[2] != 'running' else 'unhealthy'}" for c in containers
                if c[2] != "running" or "unhealthy" in c[3]]
        for p, label in cfg["docker"]["projects"].items():
            if not any(c[0] == p for c in containers):
                out.append(f"{label}: no containers")
    elif cfg["docker"]["enabled"] and "containers" in d:
        out.append("docker not answering")
    if cfg["ups"]["name"] and "OB" in (d.get("ups") or {}).get("ups.status", "").split():
        out.append("UPS on battery")
    for e in d.get("storage") or []:
        if e["missing"]:
            out.append(f"{e['path']} not mounted")
        elif e["degraded"]:
            out.append(f"{e['path']} mounted degraded")
    for path, b in (d.get("btrfs") or {}).items():
        if b["missing"] or b["errors"]:
            out.append(f"btrfs {path}: {'device missing' if b['missing'] else 'device errors'}")
    for path, m in (d.get("mdadm") or {}).items():
        if "error" in m:
            out.append(f"mdadm {path}: {m['error']}")
        elif m["state"] != "active":
            out.append(f"mdadm {path}: array {m['state']}")
        elif m["failed"] or (m["working"] is not None and m["working"] < m["disks"]):
            names = ", ".join(m.get("failed_devs") or [])
            failed = f", {names} failed" if names else f", {m['failed']} failed" if m["failed"] else ""
            out.append(f"mdadm {path}: degraded ({m['working']}/{m['disks']} disks{failed})")
    for path, z in (d.get("zfs") or {}).items():
        if "error" in z:
            out.append(f"zfs {path}: {z['error']}")
        elif z["health"] != "ONLINE":
            out.append(f"zfs pool {z['pool']} is {z['health']}")
        elif z["errors"] != "No known data errors":
            out.append(f"zfs pool {z['pool']}: {z['errors'].split(',')[0]}")
    out += [f"disk {x['dev']} SMART FAILED" for x in d.get("disks") or [] if x["health"] == "FAILED"]
    return out


# ---- screen -----------------------------------------------------------------------------------------------------

def banner_and_alerts(cfg, s):
    if not s["health_enabled"]:
        problems = live_problems(cfg, s["slow"])
        if problems:
            n = len(problems)
            return f" \033[41;97m ! {n} PROBLEM{'S' if n > 1 else ''} {RESET}", \
                [f"   {RED}! {p}{RESET}" for p in problems]
        return f" \033[42;30m ■ ALL SYSTEMS OK {RESET}", []
    h = s["health"]
    if h is None:
        return f" {YELLOW}■ running the first health check…{RESET}", []
    n, w = len(h["fails"]), len(h["warns"])
    if n:
        banner = f"\033[41;97m ! {n} PROBLEM{'S' if n > 1 else ''} {RESET}"
    elif w:
        banner = f"\033[43;30m ■ OK · {w} WARNING{'S' if w > 1 else ''} {RESET}"
    else:
        banner = f"\033[42;30m ■ ALL SYSTEMS OK {RESET}"
    checking = " · checking now…" if s["checking"] else ""
    banner = f" {banner}{GRAY}   {h['ok']} checks passed · checked {ago(time.time() - h['time'])} ago{checking}{RESET}"
    return banner, [f"   {RED}! {a}{RESET}" for a in h["fails"]] + [f"   {YELLOW}• {a}{RESET}" for a in h["warns"]]


def layout_height(panels, two):
    """Rows the panels take, with a blank row after each block (two panels side by side = one block)."""
    if not two:
        return sum(len(p) + 1 for p in panels)
    return sum(max(len(p) for p in panels[i:i + 2]) + 1 for i in range(0, len(panels), 2))


def shrink(panels, budget, two):
    """Cut the tallest panel, one row at a time, until everything fits in `budget` rows: every panel keeps its title
    and first line, and the cut rows are counted in a "… N more" line. A panel is never dropped silently."""
    panels, hidden = [list(p) for p in panels], [0] * len(panels)
    while layout_height(panels, two) > budget:
        cuttable = [i for i, p in enumerate(panels) if len(p) >= 4]
        if not cuttable:
            break  # a terminal this small shows what fits
        i = max(cuttable, key=lambda k: len(panels[k]))
        p = panels[i]
        if hidden[i]:
            del p[-2]
            hidden[i] += 1
        else:
            del p[-2:]
            hidden[i] = 2
            p.append("")
        p[-1] = row("", f"{GRAY}… {hidden[i]} more rows{RESET}")
    return panels


def build(W, H, cfg, s, now, kiosk=False):
    """→ exactly H strings of exactly W visible characters (the last one W-1, so the terminal never scrolls)."""
    f, d = s["fast"], s["slow"]
    net = d.get("network") or {}
    name = cfg["general"]["title"] or socket.gethostname().upper()
    public = f"  ·  public {net.get('public') or '?'}" if cfg["network"]["public_ip_url"] else ""
    left = f" {BOLD}{name}{RESET}{GRAY}   up {ago(f['uptime'])}  ·  LAN {net.get('lan') or '?'}{public}{RESET}"
    right = f"{BOLD}{now:%a} {now.day} {now:%b %Y   %H:%M}{RESET} "
    lines = [fit(left, W - vlen(right)) + right, GRAY + "─" * W + RESET]
    banner, alerts = banner_and_alerts(cfg, s)

    two = W >= TWO_COLUMNS_MIN
    half = W // 2 if two else W
    built = {"system": lambda: system_panel(f), "storage": lambda: storage_panel(cfg, d, half),
             "power": lambda: power_panel(cfg, d), "services": lambda: services_panel(cfg, d, half),
             "backups": lambda: backups_panel(cfg, d, now), "next": lambda: next_panel(d, now)}
    panels = [built[p]() for p in config_mod.panels(cfg)]
    traffic = s.get("traffic")
    # header, banner, a few alerts, blank, traffic title, footer: what's left is for the panels
    budget = H - len(lines) - 1 - min(len(alerts), 3) - 1 - (1 if traffic else 0) - 1
    panels = shrink(panels, budget, two)
    if two:
        pairs = [(panels[i], panels[i + 1] if i + 1 < len(panels) else []) for i in range(0, len(panels), 2)]
        blocks = [[fit(a, half) + b for a, b in zip(x + [""] * (len(y) - len(x)), y + [""] * (len(x) - len(y)))]
                  for x, y in pairs]
    else:
        blocks = panels

    if kiosk:
        footer = f"{GRAY} r = check now   ·   Alt+F2 = login console"
        night = cfg["general"]["night"]
        footer += f"{f'   ·   screen off {night}, any key wakes it' if night else ''}{RESET}"
    else:
        footer = f"{GRAY} q = quit   ·   r = check now{RESET}" if s["health_enabled"] else f"{GRAY} q = quit{RESET}"

    # Alerts and the traffic list share the free rows; alerts first, but the traffic list keeps at least 4.
    panel_rows = sum(len(b) + 1 for b in blocks)
    free = max(H - len(lines) - 1 - 1 - panel_rows - (1 if traffic else 0) - 1, 0)  # banner, blank, traffic title, footer
    keep = 4 if traffic else 0
    shown = alerts[:max(free - keep, 0)]
    if shown and len(shown) < len(alerts):
        more = len(alerts) - len(shown) + 1
        hint = f": {cfg['health']['command']}" if s["health_enabled"] else ""
        shown[-1:] = [f"   {GRAY}… {more} more{hint}{RESET}"]
    lines += [banner] + shown + [""]
    for b in blocks:
        lines += b + [""]
    if traffic:
        lines += traffic_panel(traffic, free - len(shown), W)
    lines = lines[:H - 1] + [""] * (H - 1 - len(lines))
    stamp = f"{GRAY}updated {now:%H:%M:%S} {RESET}"
    lines.append(fit(footer, W - vlen(stamp) - 1) + stamp)
    return [fit(l, W if i < H - 1 else W - 1) for i, l in enumerate(lines)]
