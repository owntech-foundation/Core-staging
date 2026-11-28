#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-TKD: the TASK + DATA module of the API regression suite (Core-staging#39): every task (TaskAPI) and
##        spin.data (DataAPI) call at effect depth on the chip, over SWD.
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or `run.sh --run`: REFLASHES the CUT on the
## ST-Link with env TKD (DAC2 on PA6, PWMA started in one profile: a bare Spin only). Otherwise every criterion
## reports BLOCKED.
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
exec "$py" "$here/run.py" "$@"
