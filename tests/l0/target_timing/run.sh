#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-TIM: cost of the Core API hot-path calls and critical-task timing on the CUT, DWT cycles over SWD
##        (Core-staging#37).
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or `run.sh --run [--repeats N]`: REFLASHES
## the CUT on the ST-Link with env TIM (starts PWMA: a bare Spin only). Otherwise every criterion reports BLOCKED.
## `--run` alone takes the bench lock (a no-op under run_all.sh or reference_run.sh, which already hold it).
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
if [[ " $* " == *" --run "* ]]; then
    . "$repo/tests/common/bench_lock.sh"
    bench_lock "tests/l0/target_timing/run.sh $*" || exit 1
fi
exec "$py" "$here/run.py" "$@"
