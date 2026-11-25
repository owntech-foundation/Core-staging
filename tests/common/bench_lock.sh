#!/usr/bin/env bash
## SPDX-License-Identifier: LGPL-2.1
## Copyright (c) 2026 OWNTECH TECHNOLOGIES
##
## @file bench_lock.sh
## @brief Host-wide lock on the bench: one user of the ST-Link, Spins and scope at a time.
##
## Sourced, not run. bench_lock takes the lock for the rest of the calling shell (and its children), waiting
## while someone else holds it. The lock is shared by every user of the host (a person, an assistant's session,
## the spoc-runner service), unlike the workflow's concurrency group, which only orders GitHub jobs. A shell
## that already holds it exports SPOC_BENCH_LOCKED=1, so nested scripts do not wait on themselves.
##
## @par Usage
##   . tests/common/bench_lock.sh && bench_lock "what I am doing"
SPOC_BENCH_LOCK=${SPOC_BENCH_LOCK:-/run/lock/spoc-bench.lock}
bench_lock() {
    [ "${SPOC_BENCH_LOCKED:-0}" = 1 ] && return 0
    command -v flock >/dev/null || { echo "bench_lock: flock not installed; bench NOT locked" >&2; return 0; }
    [ -e "$SPOC_BENCH_LOCK" ] || (umask 0; : >"$SPOC_BENCH_LOCK") 2>/dev/null
    exec 9<"$SPOC_BENCH_LOCK" || { echo "bench_lock: cannot open $SPOC_BENCH_LOCK" >&2; return 1; }
    if ! flock -n 9; then
        echo "bench_lock: bench busy ($(cat "$SPOC_BENCH_LOCK.who" 2>/dev/null || echo unknown)); waiting for it" >&2
        flock 9
    fi
    (umask 0; echo "$(id -un) pid $$ since $(date +%T): $1" >"$SPOC_BENCH_LOCK.who") 2>/dev/null
    export SPOC_BENCH_LOCKED=1
}
