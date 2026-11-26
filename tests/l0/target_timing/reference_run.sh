#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file reference_run.sh
## @brief L0-TIM reference timing run (Core-staging#37): runs L0-TIM on main and on pre-zephyr-4.4, one after the
##        other, under the bench lock; writes both into reference.json and compares them; puts the CUT back on
##        Core-CUT at the end.
##
## Each ref is checked out in its own worktree under $TIMING_REF_DIR (default ~/code/Core-timing-ref, on /home),
## and tests/ is overlaid from the commit running this script (the tag predates L0-TIM; Core itself is not
## touched: the firmware times Core at its API boundary). Each ref is one campaign under this checkout's tests/out/
## (record.md, timing.json, timing.md) and shows in the control center. REFLASHES the CUT; unattended.
##
## The reference is written by evaluate.py --update-reference with the reason below; nothing is committed:
## review tests/l0/target_timing/reference.json and commit it with its reason.
## @return 0 when both refs ran; 1 if one failed; 2 on a bad option.
##
## @par Usage
##   SPOC_CUT_DIR=~/code/Core-CUT bash tests/l0/target_timing/reference_run.sh
##   options: --repeats N (default 100)  --task-runs N (default 10000)  --no-restore
##            --refs "main=<rev> pre-zephyr-4.4=<rev>" (default: the staging remote's main, tag pre-zephyr-4.4)
##            --reason "..." (default: first reference run, Core-staging#37)
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
repeats=100 task_runs=10000 restore=1 refs="" reason="first reference timing run, main vs pre-zephyr-4.4 (Core-staging#37)"
while [ $# -gt 0 ]; do
    case "$1" in
        --repeats) repeats=$2; shift ;;
        --task-runs) task_runs=$2; shift ;;
        --refs) refs=$2; shift ;;
        --reason) reason=$2; shift ;;
        --no-restore) restore=0 ;;
        -h|--help) sed -n '/^## @brief/,/^##            --reason/s/^## \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "reference_run: unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

# The staging remote: the one whose URL names Core-staging, else origin (the runner's checkout).
remote=$(git -C "$repo" remote -v | awk '/Core-staging/ {print $1; exit}')
remote=${remote:-origin}
git -C "$repo" fetch -q "$remote" main --tags || echo "reference_run: fetch from $remote failed; using local refs" >&2
[ -n "$refs" ] || refs="main=$remote/main pre-zephyr-4.4=pre-zephyr-4.4^{commit}"
harness=$(git -C "$repo" rev-parse HEAD)
if [ -n "$(git -C "$repo" status --porcelain --untracked-files=no -- tests/)" ]; then
    echo "reference_run: tests/ has uncommitted changes; the overlay uses the committed $harness" >&2
fi
base=${TIMING_REF_DIR:-$HOME/code/Core-timing-ref}
export SPOC_CUT_DIR=${SPOC_CUT_DIR:-$HOME/code/Core-CUT}
stamp=$(date +%F)_$(date +%H%M%S)
summary="$repo/tests/out/timing-reference_$stamp"
mkdir -p "$summary" "$base"

. "$repo/tests/common/bench_lock.sh"
bench_lock "L0-TIM reference run (tests/l0/target_timing/reference_run.sh)" || exit 1
bash "$repo/tests/common/target_preflight.sh" --env=TIM || echo "reference_run: preflight reports missing items (above)" >&2

finish() {
    if [ "$restore" = 1 ]; then
        echo "== bench to rest: CUT on Core-CUT ($SPOC_CUT_DIR)"
        bash "$repo/tests/common/bench_restore.sh" || echo "reference_run: bench restore FAILED: restore by hand" >&2
    fi
}
trap finish EXIT

rc=0
for pair in $refs; do
    name=${pair%%=*} rev=${pair#*=}
    sha=$(git -C "$repo" rev-parse --verify -q "$rev") || { echo "reference_run: $rev not found" >&2; rc=1; continue; }
    wt="$base/$name"
    echo "== $name: $rev = ${sha:0:7}, worktree $wt"
    git -C "$repo" worktree remove --force "$wt" 2>/dev/null; rm -rf "$wt"
    git -C "$repo" worktree add -q --detach "$wt" "$sha" || { rc=1; continue; }
    # The harness's tests/ over the ref's (the tag has no L0-TIM); Core sources stay the ref's.
    git -C "$wt" checkout -q "$harness" -- tests/
    out=$(mktemp -d "$repo/tests/out/$(date +%F)_${sha:0:7}_$(date +%H%M%S)_timing-$name-XXXX")
    log="$out/l0_target_timing.log"
    # Only main's run is a current result; another ref's campaign is kept for comparison (control center).
    [ "$name" = main ] || printf '{"reference": "%s at %s (L0-TIM reference run, Core-staging#37)"}\n' "$name" \
        "${sha:0:7}" >"$out/reference.json"
    (cd "$wt" && CORE_TARGET=1 CORE_TEST_OUT="$out" TIMING_REF_SHA="$name ${sha:0:7}" TIMING_HARNESS_SHA="${harness:0:7}" \
        bash tests/l0/target_timing/run.sh --repeats "$repeats" --task-runs "$task_runs") >"$log" 2>&1
    r=$?
    if [ $r -ne 0 ] && ! grep -q $'^@@\t[^\t]*\tFAIL\t' "$log"; then
        printf '@@\tL0-TIM-00\tFAIL\ttarget_timing did not complete\texit code 0\texit code %d, see %s\n' "$r" \
            "$(basename "$log")" >>"$log"
    fi
    # The record stamps the ref's worktree (sha of the ref, "-dirty" from the tests/ overlay).
    python3 "$wt/tests/common/make_record.py" --out "$out" --levels L0 "$log" || true
    if [ -f "$out/timing.json" ]; then
        cp "$out/timing.json" "$summary/$name.json"
        python3 "$here/evaluate.py" "$out/timing.json" --update-reference --name "$name" \
            --reason "$reason; $name = ${sha:0:7}, harness ${harness:0:7}"
    else
        echo "reference_run: $name produced no timing.json, see $log" >&2
        rc=1
    fi
done

if [ -f "$summary/pre-zephyr-4.4.json" ] && [ -f "$summary/main.json" ]; then
    python3 "$here/evaluate.py" --compare "$summary/pre-zephyr-4.4.json" "$summary/main.json" \
        --out "$summary/main-vs-pre-zephyr-4.4.md"
fi
python3 "$repo/tests/common/control_center.py" --out "$repo/tests/out" || true
echo
echo "Reference run done (rc $rc). Results: $summary"
echo "Next: review $here/reference.json and commit it with its reason:"
echo "  git add tests/l0/target_timing/reference.json && git commit -m '[test] L0-TIM reference: $reason'"
exit $rc
