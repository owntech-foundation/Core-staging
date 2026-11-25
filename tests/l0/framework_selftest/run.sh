#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file run.sh
## @brief L0-FWK: the test framework checks itself, without hardware.
##
## Prints one result line per criterion (tests/common/README.md) so that a
## campaign exercises every verdict the record, review page and control center
## render: PASS or FAIL, INFO, and PASS or BLOCKED.
## @return 0 unless a criterion fails.
set -uo pipefail
repo=$(cd "$(dirname "$0")/../../.." && pwd)
tab=$'\t'
line() { echo "@@${tab}$1${tab}$2${tab}$3${tab}$4${tab}$5"; }
rc=0

# L0-FWK-01: the catalogue loads and is consistent.
if msg=$(python3 "$repo/tests/common/catalogue.py" 2>&1); then
    line L0-FWK-01 PASS "catalogue.json loads and is consistent" "catalogue.py exit 0" "$(echo "$msg" | head -1 | tr -s ' ')"
else
    line L0-FWK-01 FAIL "catalogue.json loads and is consistent" "catalogue.py exit 0" "$(echo "$msg" | head -1)"
    rc=1
fi

# L0-FWK-02: host Python, for the record.
line L0-FWK-02 INFO "host Python" "-" "$(python3 -c 'import platform; print(platform.python_version())')"

# L0-FWK-03: the review page can be written (.venv with tests/requirements.txt).
if [ -x "$repo/.venv/bin/python" ] && "$repo/.venv/bin/python" -c 'import numpy, matplotlib' 2>/dev/null; then
    line L0-FWK-03 PASS "review page dependencies" "numpy and matplotlib in .venv" "$("$repo/.venv/bin/python" -c 'import numpy, matplotlib; print("numpy", numpy.__version__, "matplotlib", matplotlib.__version__)')"
else
    line L0-FWK-03 BLOCKED "review page dependencies" "numpy and matplotlib in .venv" "no .venv: python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt"
fi
exit $rc
