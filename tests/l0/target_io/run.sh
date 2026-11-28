#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-IOM: the IO module of the API regression suite (Core-staging#40): spin.gpio, spin.led, spin.dac,
##        spin.comp, spin.timer and spin.uart at effect depth over SWD; GPIO/LED/DAC levels and the UART frame on the
##        SPOC scope through the MUX; L0-DAC (communication.analog, Core#168) folded in under its IDs.
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or `run.sh --run`: REFLASHES the CUT on the
## ST-Link with env IO (drives GPIO outputs on most pins, the DACs and USART1: a bare Spin only). Otherwise every
## criterion reports BLOCKED. The loopback rows (hand-held wires until #16) also need --supervised and a terminal.
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
exec "$py" "$here/run.py" "$@"
