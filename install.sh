#!/usr/bin/env bash
# Install tmon system-wide: /usr/local/bin/tmon (one-file Python zipapp), /etc/tmon/config.toml, tmon.service.
#   sudo ./install.sh                 install or update (an existing config is never overwritten)
#   sudo ./install.sh --kiosk         … and show tmon on tty1 instead of the login prompt
#   sudo ./install.sh --no-kiosk      tty1 back to a login prompt
#   sudo ./install.sh --uninstall     remove binary and unit (keeps /etc/tmon)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
cd "$(dirname "$0")"
BIN=/usr/local/bin/tmon UNIT=/etc/systemd/system/tmon.service

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "tmon needs Python ≥ 3.10" >&2; exit 1; }
# Python 3.10 (Ubuntu 22.04) has no tomllib: tmon runs with its defaults but cannot read a config file.
toml=yes
python3 -c 'import tomllib' 2>/dev/null || toml=no

no_kiosk() {
  if [[ -f $UNIT ]]; then systemctl disable --now tmon.service 2>/dev/null || true; fi
  systemctl enable --now getty@tty1.service
}

case ${1:-} in
  --uninstall)
    no_kiosk
    rm -f "$BIN" "$UNIT"
    systemctl daemon-reload
    echo "tmon removed (config left in /etc/tmon)"
    exit 0 ;;
  --no-kiosk)
    no_kiosk
    echo "tty1 shows a login prompt again"
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
install -m 644 systemd/tmon.service "$UNIT"
if [[ $toml == no ]]; then
  echo "Python $(python3 -c 'import sys; print(*sys.version_info[:2], sep=".")') cannot read config files (needs 3.11):" \
    "tmon runs with its defaults; /etc/tmon/config.toml not written" >&2
elif [[ ! -e /etc/tmon/config.toml ]]; then
  install -d /etc/tmon
  install -m 644 examples/config.toml /etc/tmon/config.toml
  echo "wrote /etc/tmon/config.toml — edit it, then: tmon --check-config"
fi
systemctl daemon-reload
echo "installed $("$BIN" --version) → $BIN"

if [[ ${1:-} == --kiosk ]]; then
  # Both units would start at boot and they conflict; tty1 belongs to tmon now.
  systemctl disable getty@tty1.service
  systemctl enable tmon.service
  systemctl restart tmon.service
  sleep 3
  systemctl is-active --quiet tmon.service || { journalctl -u tmon -n 20 --no-pager; exit 1; }
  echo "tmon running on tty1 (logins: Alt+F2 … Alt+F6)"
elif systemctl is-active --quiet tmon.service; then
  systemctl restart tmon.service
  echo "restarted tmon.service"
fi
