#!/usr/bin/env bash
# Real-world checks on a throwaway machine (the "integration" CI workflow): real btrfs/mdadm/ZFS arrays on loop
# files, real Podman containers, a real k3s cluster, the real systemd service. DESTRUCTIVE: creates and breaks
# storage, installs tmon system-wide. Never run it on a machine you care about.
#   usage: sudo tests/integration/run.sh <install|btrfs|mdadm|zfs|podman|k3s|kiosk>
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ ${CI:-} == true || ${TMON_INTEGRATION:-} == yes ]] ||
  { echo "refusing to run outside CI (set TMON_INTEGRATION=yes on a disposable machine)" >&2; exit 1; }
cd "$(dirname "$0")/../.."
WORK=/var/tmp/tmon-it
CFG=$WORK/config.toml
mkdir -p "$WORK"

frame() { tmon -c "$CFG" --once --no-color --no-health --size 160x60; }
fail() { echo "FAILED: $1"; echo "----- screen -----"; frame || true; exit 1; }
expect() { # expect "<fixed text>" …: every text is on the screen
  local out; out=$(frame)
  for text; do grep -qF -- "$text" <<<"$out" || fail "missing: $text"; echo "ok: $text"; done
}
expect_re() { frame | grep -qE -- "$1" || fail "no match: $1"; echo "ok: /$1/"; }
eventually() { # eventually <seconds> "<regex>": wait until the screen matches
  local until=$((SECONDS + $1))
  while ((SECONDS < until)); do frame | grep -qE -- "$2" && { echo "ok: /$2/"; return; }; sleep 3; done
  fail "timed out waiting for: $2"
}
loopdev() { truncate -s "${2:-300M}" "$WORK/$1.img" && losetup -f --show "$WORK/$1.img"; }
config() { cat >"$CFG"; tmon -c "$CFG" --check-config; }

case ${1:-} in
install)
  ./install.sh
  tmon --version
  tmon --demo --once --size 120x33 >/dev/null
  ;;

btrfs)
  a=$(loopdev b1) b=$(loopdev b2)
  mkfs.btrfs -q -f -d raid1 -m raid1 "$a" "$b"
  mkdir -p /mnt/btr && mount "$a" /mnt/btr
  config <<TOML
[docker]
enabled = false
[kubernetes]
enabled = false
[[storage]]
label = "Pool"
path = "/mnt/btr"
require_mount = true
btrfs = true
devices = 2
scrub = true
TOML
  expect "btrfs      ■ RAID1 · 2/2 disks · no errors" "Scrub      never" "ALL SYSTEMS OK"
  btrfs scrub start -B /mnt/btr >/dev/null
  expect "finished · no errors found"
  umount /mnt/btr
  expect "Pool       ! /mnt/btr NOT MOUNTED" "! /mnt/btr not mounted"
  ;;

mdadm)
  a=$(loopdev m1) b=$(loopdev m2)
  mdadm --create /dev/md0 --run --level=1 --raid-devices=2 --metadata=1.2 --assume-clean "$a" "$b"
  mkfs.ext4 -q /dev/md0 && mkdir -p /mnt/md && mount /dev/md0 /mnt/md
  config <<TOML
[docker]
enabled = false
[kubernetes]
enabled = false
[[storage]]
label = "Array"
path = "/mnt/md"
require_mount = true
mdadm = true
TOML
  cat /proc/mdstat
  expect "mdadm      ■ raid1 · 2/2 disks" "ALL SYSTEMS OK"
  mdadm /dev/md0 --fail "$b"
  cat /proc/mdstat
  expect "DEGRADED 1/2 [U_]" "! mdadm /mnt/md: degraded (1/2 disks, $(basename "$b") failed)"
  mdadm /dev/md0 --remove "$b"
  echo 1000 >/proc/sys/dev/raid/speed_limit_max # KB/s: the rebuild takes minutes, long enough to see it
  mdadm /dev/md0 --add "$b"
  eventually 60 "raid1 · recovery [0-9]+ % · DEGRADED 1/2"
  cat /proc/mdstat
  ;;

zfs)
  modprobe zfs
  truncate -s 300M "$WORK/z1.img" "$WORK/z2.img"
  zpool create -f -m none tank mirror "$WORK/z1.img" "$WORK/z2.img"
  zfs create -o mountpoint=/mnt/tank tank/data
  config <<TOML
[docker]
enabled = false
[kubernetes]
enabled = false
[[storage]]
label = "Tank"
path = "/mnt/tank"
require_mount = true
zfs = true
scrub = true
TOML
  zpool status tank
  expect "zfs        ■ tank ONLINE · no data errors" "Scrub      never" "ALL SYSTEMS OK"
  zpool scrub -w tank
  zpool status tank
  expect "finished · no errors found"
  zpool offline tank "$WORK/z2.img"
  zpool status tank
  expect "tank DEGRADED" "! zfs pool tank is DEGRADED"
  ;;

podman)
  podman pull -q docker.io/library/alpine:3.20 >/dev/null
  podman run -d --name web_app --label com.docker.compose.project=web docker.io/library/alpine:3.20 sleep 600 >/dev/null
  podman run --name web_db --label com.docker.compose.project=web docker.io/library/alpine:3.20 false || true
  podman run -d --name lonely docker.io/library/alpine:3.20 sleep 600 >/dev/null
  config <<TOML
[docker]
enabled = true
command = "podman"
[kubernetes]
enabled = false
TOML
  podman ps -a --format json | head -c 600; echo
  expect "2/3 containers running" "■ web" "■ lonely" "! container web_db is exited"
  ;;

k3s)
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable=traefik --disable=metrics-server" sh -
  k3s kubectl wait --for=condition=Ready node --all --timeout=180s
  for _ in $(seq 60); do # the CoreDNS pod only exists once k3s has applied its manifests
    [[ -n $(k3s kubectl -n kube-system get pods -l k8s-app=kube-dns -o name) ]] && break
    sleep 3
  done
  k3s kubectl -n kube-system wait --for=condition=Ready pod -l k8s-app=kube-dns --timeout=180s
  config <<TOML
[docker]
enabled = false
[kubernetes]
enabled = "auto"
TOML
  tmon -c "$CFG" --check-config | grep -q "services" || fail "kubernetes = auto did not detect k3s"
  expect "Kubernetes ■ nodes 1/1 Ready" "■ kube-system"
  k3s kubectl create namespace demo
  k3s kubectl -n demo run broken --image=registry.invalid/does-not-exist:1 --restart=Never
  eventually 120 "pod demo/broken: (ErrImagePull|ImagePullBackOff)"
  expect "■ demo"
  k3s kubectl get pods -A
  ;;

kiosk)
  tmon --enable-kiosk --demo
  systemctl is-active tmon.service
  grep -q "^ExecStart=/usr/local/bin/tmon --kiosk --demo$" /etc/systemd/system/tmon.service
  ! systemctl is-enabled --quiet getty@tty1.service || fail "getty@tty1 still enabled"
  sleep 3
  systemctl is-active tmon.service # still running after a few redraws
  tmon --disable-kiosk
  [[ ! -e /etc/systemd/system/tmon.service ]] || fail "unit not removed"
  systemctl is-enabled --quiet getty@tty1.service || fail "getty@tty1 not restored"
  echo "ok: kiosk service enabled, running, removed"
  ;;

*)
  echo "usage: $0 <install|btrfs|mdadm|zfs|podman|k3s|kiosk>" >&2
  exit 1
  ;;
esac
