#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-HRI: an up-down HRTIM timing unit keeps its switching frequency when it is initialised again
##        (Core#172).
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or `run.sh --run`: REFLASHES the CUT on the
## ST-Link with env HRI, and needs the SPOC bench (MUX routes PA8 to the scope). Otherwise every criterion reports
## BLOCKED.
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
exec "$py" "$here/run.py" "$@"
