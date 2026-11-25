#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-TGT: build -> flash -> SWD read on a Spin through an ST-Link.
##
## On target only with tests/run_all.sh --l0 --target (CORE_TARGET=1), or
## `run.sh --run`: REFLASHES the Spin on the ST-Link. Otherwise every criterion
## reports BLOCKED.
set -uo pipefail
exec python3 "$(dirname "$0")/run.py" "$@"
