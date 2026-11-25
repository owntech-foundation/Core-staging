#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file install.sh
## @brief Sets up the self-hosted GitHub runner that drives the SPOC bench (tests/runner/README.md).
##
## Run as root from a Core-staging checkout. Safe to run again: each step checks what is already there.
##   1. user spoc-runner (no sudo, no docker; groups plugdev and dialout), home /var/lib/spoc-runner
##   2. udev rules 99-spoc-bench.rules (/dev/spoc-scope, /dev/spoc-stlink, /dev/spoc-cut, /dev/spoc-mux)
##   3. pre-job hook in /usr/local/lib/spoc-runner/, root-owned
##   4. as spoc-runner: PlatformIO, the test venv, a copy of Core-CUT (from a local SPOC_Software checkout:
##      the repository is private and the runner user holds no credentials)
##   5. the actions runner: download, register (needs RUNNER_TOKEN), .env, systemd service
## @return 0 when every step is done.
##
## @par Usage
##   sudo RUNNER_TOKEN=$(gh api -X POST repos/owntech-foundation/Core-staging/actions/runners/registration-token --jq .token) \
##        SPOC_SOFTWARE_SRC=$HOME/code/SPOC_Software bash tests/runner/install.sh
## (sudo drops variables set before it; set them after it, as above. The token is valid for one hour.)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root: see the usage at the top of $0" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
USER_NAME=spoc-runner
HOME_DIR=/var/lib/spoc-runner   # the path everything refers to; a bind mount of DATA_DIR, never a symlink
# Where the runner's files really live: PlatformIO toolchains, Core-CUT and job builds take over 12 GB. On a laptop
# whose / is a small partition, keep them on the large one (the first install filled / and broke the build).
DATA_DIR=${SPOC_RUNNER_DATA:-/home/spoc-runner}
MIN_FREE_GB=15
HOOK_DIR=/usr/local/lib/spoc-runner
RUNNER_VERSION=${RUNNER_VERSION:-2.338.2}
RUNNER_URL=https://github.com/owntech-foundation/Core-staging
RUNNER_DIR=$HOME_DIR/actions-runner
CUT_DIR=$HOME_DIR/Core-CUT
VENV=$HOME_DIR/venv
SPOC_SOFTWARE_SRC=${SPOC_SOFTWARE_SRC:-}
step() { echo; echo "== $*"; }
as_runner() { sudo -u "$USER_NAME" -H bash -c "cd $HOME_DIR && $1"; }

step "0. disk space"
# A bind mount, not a symlink: PlatformIO reaches Zephyr through the path it is given while CMake resolves the real
# one, and the build then cannot find its own objects ("offsets.c.o not found").
if [ -L "$HOME_DIR" ]; then
    echo "REFUSED: $HOME_DIR is a symlink; the Zephyr build fails through it. Replace it with a bind mount (README)" >&2; exit 1
fi
mkdir -p "$HOME_DIR"
if [ "$DATA_DIR" != "$HOME_DIR" ]; then
    mkdir -p "$DATA_DIR"
    grep -qE "^$DATA_DIR[[:space:]]+$HOME_DIR[[:space:]]" /etc/fstab ||
        echo "$DATA_DIR $HOME_DIR none bind 0 0" >> /etc/fstab
    mountpoint -q "$HOME_DIR" || { systemctl daemon-reload; mount "$HOME_DIR"; }
fi
free_gb=$(df -P --block-size=1G "$HOME_DIR" | awk 'NR==2 {print $4}')
echo "$HOME_DIR ($(findmnt -no SOURCE "$HOME_DIR" 2>/dev/null || echo "$DATA_DIR")): ${free_gb} GB free"
if [ "$free_gb" -lt "$MIN_FREE_GB" ] && [ ! -x "$HOME_DIR/.platformio/penv/bin/pio" ]; then
    echo "REFUSED: under $MIN_FREE_GB GB free for the runner; set SPOC_RUNNER_DATA to a larger partition" >&2; exit 1
fi

step "1. user $USER_NAME"
if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir "$HOME_DIR" --shell /bin/bash "$USER_NAME"
fi
chown "$USER_NAME:$USER_NAME" "$HOME_DIR"
usermod -aG plugdev,dialout "$USER_NAME"
for g in sudo docker; do
    if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
        echo "REFUSED: $USER_NAME is in group $g, which gives root; remove it (gpasswd -d $USER_NAME $g)" >&2; exit 1
    fi
done
chmod 0750 "$HOME_DIR"
id "$USER_NAME"

step "2. udev rules"
install -m 0644 "$here/99-spoc-bench.rules" /etc/udev/rules.d/99-spoc-bench.rules
udevadm control --reload
# Re-apply the rules to the bench devices only (scope 1ab1, ST-Link 0483, Spins 2fe3) and their children.
# A bare `udevadm trigger` replays every device on the computer, display and input included, and can restart
# the desktop session (it did, on the first install).
for dev in /sys/bus/usb/devices/*; do
    [ -f "$dev/idVendor" ] || continue
    case "$(cat "$dev/idVendor")" in
        1ab1|0483|2fe3) udevadm trigger --action=change --parent-match="$dev" ;;
    esac
done
udevadm settle --timeout=10
sleep 1
ls -l /dev/spoc-* 2>/dev/null || echo "no /dev/spoc-* yet: replug the bench USB (scope, ST-Link, Spins)"

step "3. pre-job hook"
install -d -o root -g root -m 0755 "$HOOK_DIR"
install -o root -g root -m 0755 "$here/job_started.sh" "$HOOK_DIR/job_started.sh"

step "4. tools for $USER_NAME"
if [ ! -x "$HOME_DIR/.platformio/penv/bin/pio" ]; then
    as_runner "curl -fsSL -o get-platformio.py https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py && python3 get-platformio.py && rm get-platformio.py"
fi
# OpenOCD 3.1200.0: Core asks for tool-openocd unversioned, so a fresh install gets the newest (3.1200.7), whose
# stlink.cfg rejects the `transport select hla_swd` that platform ststm32@19.0.0 adds for ST-Link uploads.
OPENOCD_VERSION=3.1200.0
ocd_pkg=$HOME_DIR/.platformio/packages/tool-openocd/package.json
if ! grep -q "\"version\": \"$OPENOCD_VERSION\"" "$ocd_pkg" 2>/dev/null; then
    as_runner "rm -rf .platformio/packages/tool-openocd .platformio/packages/tool-openocd@* && .platformio/penv/bin/pio pkg install -g -t platformio/tool-openocd@$OPENOCD_VERSION"
fi
if [ ! -x "$VENV/bin/python" ]; then as_runner "python3 -m venv $VENV"; fi
install -o "$USER_NAME" -m 0644 "$repo/tests/requirements.txt" "$HOME_DIR/requirements.txt"
as_runner "$VENV/bin/pip install -q -r requirements.txt"
if [ ! -d "$CUT_DIR/.git" ]; then
    [ -n "$SPOC_SOFTWARE_SRC" ] || { echo "set SPOC_SOFTWARE_SRC to a local SPOC_Software checkout holding branch Core-CUT" >&2; exit 1; }
    # Core-CUT may be a local branch or only origin/Core-CUT there; take whichever exists.
    src_git=(git -c safe.directory='*' -C "$SPOC_SOFTWARE_SRC")
    ref=
    for r in refs/heads/Core-CUT refs/remotes/origin/Core-CUT; do
        if "${src_git[@]}" rev-parse --verify -q "$r" >/dev/null; then ref=$r; break; fi
    done
    [ -n "$ref" ] || { echo "no Core-CUT or origin/Core-CUT in $SPOC_SOFTWARE_SRC (git fetch origin there first)" >&2; exit 1; }
    git init -q "$CUT_DIR"
    git -c safe.directory='*' -C "$CUT_DIR" fetch -q "$SPOC_SOFTWARE_SRC" "$ref"
    git -C "$CUT_DIR" checkout -q -B Core-CUT FETCH_HEAD
    chown -R "$USER_NAME:$USER_NAME" "$CUT_DIR"
fi
echo "Core-CUT $(git -C "$CUT_DIR" rev-parse --short HEAD)"
# First build of Core-CUT fetches the toolchains; later bench restores run offline.
as_runner "$HOME_DIR/.platformio/penv/bin/pio run -d $CUT_DIR -e STLink"

step "5. actions runner $RUNNER_VERSION"
case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;; esac
if [ ! -x "$RUNNER_DIR/config.sh" ]; then
    as_runner "mkdir -p $RUNNER_DIR && cd $RUNNER_DIR && curl -fsSL https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-linux-$arch-$RUNNER_VERSION.tar.gz | tar xz"
fi
if [ ! -f "$RUNNER_DIR/.runner" ]; then
    [ -n "${RUNNER_TOKEN:-}" ] || { echo "set RUNNER_TOKEN (see the usage at the top of $0)" >&2; exit 1; }
    as_runner "cd $RUNNER_DIR && ./config.sh --unattended --url $RUNNER_URL --token $RUNNER_TOKEN --name spoc-$(hostname -s) --labels spoc --work _work --replace"
fi
# The runner reads .env at start. Root-owned, so a job cannot drop the hook by rewriting it.
cat > "$RUNNER_DIR/.env" <<EOF
ACTIONS_RUNNER_HOOK_JOB_STARTED=$HOOK_DIR/job_started.sh
SPOC_CUT_DIR=$CUT_DIR
SPOC_VENV=$VENV
RIGOL_USBTMC=/dev/spoc-scope
LANG=C.UTF-8
EOF
chown root:root "$RUNNER_DIR/.env"; chmod 0644 "$RUNNER_DIR/.env"
cd "$RUNNER_DIR"
[ -f .service ] || ./svc.sh install "$USER_NAME"   # svc.sh install writes .service
./svc.sh start
./svc.sh status | head -5
echo
echo "Done. Check: gh api repos/owntech-foundation/Core-staging/actions/runners --jq '.runners[]|{name,status,labels:[.labels[].name]}'"
echo "While working on the bench by hand: sudo $RUNNER_DIR/svc.sh stop (and start afterwards)."
