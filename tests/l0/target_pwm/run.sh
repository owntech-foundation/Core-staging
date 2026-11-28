#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-PWM: the PWM module of the API regression suite (Core-staging#38): every PwmHAL call at effect depth
##        over SWD, outputs/frequency/phase/convention on the SPOC scope through the MUX, latency to effect.
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or `run.sh --run`: REFLASHES the CUT on the
## ST-Link with env PWM (starts outputs of PWMA-PWMD: a bare Spin only). Otherwise every criterion reports BLOCKED.
## The supervised rows (hand-held probes) also need --supervised (CORE_SUPERVISED=1) and a terminal.
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
exec "$py" "$here/run.py" "$@"
