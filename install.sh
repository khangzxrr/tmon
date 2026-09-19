#!/usr/bin/env bash
# Install tmon system-wide: /usr/local/bin/tmon (one-file Python zipapp) and /etc/tmon/config.toml.
#   sudo ./install.sh                 install or update (an existing config is never overwritten)
#   sudo ./install.sh --kiosk         … and show tmon on tty1 instead of the login prompt (= tmon --enable-kiosk)
#   sudo ./install.sh --no-kiosk      tty1 back to a login prompt (= tmon --disable-kiosk)
#   sudo ./install.sh --uninstall     remove the binary and the kiosk service (keeps /etc/tmon)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
cd "$(dirname "$0")"
BIN=/usr/local/bin/tmon UNIT=/etc/systemd/system/tmon.service

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "tmon needs Python ≥ 3.10" >&2; exit 1; }
# Python 3.10 (Ubuntu 22.04) has no tomllib: tmon runs with its defaults but cannot read a config file.
toml=yes
python3 -c 'import tomllib' 2>/dev/null || toml=no

case ${1:-} in
  --uninstall)
    if [[ -e $UNIT ]]; then "$BIN" --disable-kiosk; fi
    rm -f "$BIN"
    echo "tmon removed (config left in /etc/tmon)"
    exit 0 ;;
  --no-kiosk)
    "$BIN" --disable-kiosk
    exit 0 ;;
  '' | --kiosk) ;;
  *) echo "unknown option $1" >&2; exit 1 ;;
esac

build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
mkdir "$build/src"
cp -r tmon "$build/src/"
find "$build/src" -name __pycache__ -prune -exec rm -rf {} +
python3 -m zipapp "$build/src" -m tmon.cli:main -p '/usr/bin/env python3' -o "$build/tmon"
install -m 755 "$build/tmon" "$BIN"
if [[ $toml == no ]]; then
  echo "Python $(python3 -c 'import sys; print(*sys.version_info[:2], sep=".")') cannot read config files (needs 3.11):" \
    "tmon runs with its defaults; /etc/tmon/config.toml not written" >&2
elif [[ ! -e /etc/tmon/config.toml ]]; then
  install -d /etc/tmon
  install -m 644 examples/config.toml /etc/tmon/config.toml
  echo "wrote /etc/tmon/config.toml — edit it, then: tmon --check-config"
fi
echo "installed $("$BIN" --version) → $BIN"

if [[ ${1:-} == --kiosk ]]; then
  "$BIN" --enable-kiosk
elif systemctl is-active --quiet tmon.service 2>/dev/null; then
  systemctl restart tmon.service
  echo "restarted tmon.service"
fi
