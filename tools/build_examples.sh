#!/usr/bin/env bash
# Build every example of owntech-foundation/examples against this Core checkout.
#
#   tools/build_examples.sh <examples checkout> [output dir]
#
# Each example folder (one holding main.cpp) is copied into src/ and built with env USB; its app.ini sets the
# shield. Examples are built grouped by shield so that most builds are incremental. src/ is restored on exit.
# Writes <output dir>/summary.md (one row per example) and one log per example; exits 1 if any build fails.
# PIO overrides the PlatformIO command (default: pio).
set -uo pipefail

core=$(cd "$(dirname "$0")/.." && pwd)
ex=$(cd "${1:?usage: tools/build_examples.sh <examples checkout> [output dir]}" && pwd)
out=$(mkdir -p "${2:-$core/.pio/examples-build}" && cd "${2:-$core/.pio/examples-build}" && pwd)
pio=${PIO:-pio}

backup=$(mktemp -d)
cp -r "$core/src" "$backup/src"
trap 'rm -rf "$core/src"; cp -r "$backup/src" "$core/src"; rm -rf "$backup"' EXIT

# "<shield> <folder>" per example, sorted so builds sharing a shield follow each other
list=$(cd "$ex" && find . -name main.cpp -not -path './.git/*' -printf '%h\n' | sed 's|^\./||' | sort | while read -r d; do
    s=$(sed -nE 's/^\s*board_shield\s*=\s*(\S+).*/\1/p' "$d/app.ini" 2>/dev/null | head -1)
    echo "${s:-none} $d"
done | sort)

ex_sha=$(git -C "$ex" rev-parse --short HEAD 2>/dev/null || echo unknown)
core_sha=$(git -C "$core" rev-parse --short HEAD 2>/dev/null || echo unknown)
{
    echo "Examples \`$ex_sha\` built against Core \`$core_sha\` (env USB)"
    echo
    echo "| Example | Shield | Build | Time |"
    echo "|---|---|---|---|"
} > "$out/summary.md"

fail=0; n=0
while read -r shield dir; do
    n=$((n + 1))
    log="$out/$(echo "$dir" | tr / _).log"
    rm -rf "$core/src"
    cp -r "$ex/$dir" "$core/src"
    t0=$(date +%s)
    if (cd "$core" && $pio run -e USB) > "$log" 2>&1; then verdict=PASS; else verdict=FAIL; fail=$((fail + 1)); fi
    t=$(( $(date +%s) - t0 ))
    echo "| $dir | $shield | $verdict | ${t} s |" >> "$out/summary.md"
    echo "$verdict  $dir ($shield, ${t} s)"
done <<< "$list"

echo >> "$out/summary.md"
echo "**$((n - fail)) of $n examples build.**" >> "$out/summary.md"
echo "$((n - fail)) of $n examples build; summary in $out/summary.md"
[ "$fail" -eq 0 ]
