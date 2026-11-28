#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-NVS: the MetaData / NVS module of the API regression suite (Core-staging#42): the 15 spin.metaData
##        calls and the NVS store/retrieve paths, across ST-Link resets and reflashes. Nightly tier.
##
## On target only with tests/run_all.sh --l0 --target --nightly (CORE_TARGET=1 CORE_NIGHTLY=1), or `run.sh --run`:
## REFLASHES the CUT on the ST-Link (env NVS, env SMOKE, env NVS again) and WRITES its NVS partition, which it backs up
## over SWD first and writes back at the end. Otherwise every criterion reports BLOCKED.
## @flashes-cut
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
py=python3; [ -x "$repo/.venv/bin/python" ] && py="$repo/.venv/bin/python"
exec "$py" "$here/run.py" "$@"
