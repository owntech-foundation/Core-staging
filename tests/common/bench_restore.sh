#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file bench_restore.sh
## @brief Puts the SPOC bench back in its rest state: the CUT runs Core-CUT again.
##
## On-target tests (those marked "## @flashes-cut") leave their own firmware on the CUT, and the SPOC tests
## need Core-CUT. Core-CUT lives in the private SPOC_Software repository (branch Core-CUT), so the bench host
## keeps a checkout of it and names it in $SPOC_CUT_DIR. This script uploads it over the ST-Link (PlatformIO
## env STLink), then checks that the CUT's ThingSet shell answers as Core-CUT. The SPOC bench runner calls it
## before and after every campaign; run it by hand after a session that reflashed the CUT.
## @return 0 when the CUT answers as Core-CUT; 1 otherwise; 2 when $SPOC_CUT_DIR is not set up.
##
## @par Usage
##   SPOC_CUT_DIR=~/code/SPOC_Software bash tests/common/bench_restore.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
PIO=${PIO:-$HOME/.platformio/penv/bin/pio}
cut=${SPOC_CUT_DIR:-}
if [ -z "$cut" ] || [ ! -f "$cut/platformio.ini" ]; then
    echo "bench_restore: SPOC_CUT_DIR must name a checkout of SPOC_Software, branch Core-CUT (now '${cut}')" >&2
    exit 2
fi
echo "bench_restore: uploading Core-CUT ($(git -C "$cut" rev-parse --short HEAD 2>/dev/null || echo '?')) over the ST-Link"
if ! "$PIO" run -d "$cut" -e STLink -t upload; then
    echo "bench_restore: upload failed" >&2
    exit 1
fi
py=python3; [ -x "$here/../../.venv/bin/python" ] && py="$here/../../.venv/bin/python"
# The CUT boots in about 2 s; boards.find retries.
port=$(cd "$here" && "$py" -c "from spoc.boards import find; print(find('cut', tries=5) or '')")
if [ -z "$port" ]; then
    echo "bench_restore: the CUT does not answer as Core-CUT after the upload" >&2
    exit 1
fi
echo "bench_restore: CUT on Core-CUT, shell on $port"
