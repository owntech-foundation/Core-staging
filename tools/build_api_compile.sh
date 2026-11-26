#!/usr/bin/env bash
# Build tier of the API regression suite: build tests/firmware/api_compile (every public call) in each shield
# configuration and report which public calls each configuration compiled and linked.
#
#   tools/build_api_compile.sh [output dir] [config ...]     # configs: none twist ownverter twist130 (default: all)
#
# Builds env APIC_<CONFIG> (tests/firmware/pio_tests.ini) one after the other: PlatformIO wipes the other envs'
# build folders when the configuration changes, so each report is taken right after its build, from main.o.
# Writes <output dir>/summary.md and one log per configuration; exits 1 if a build fails or the coverage check fails.
# Restores zephyr/CMakeLists.txt (the build rewrites its BOARD/SHIELD lines). PIO overrides the PlatformIO command.
set -uo pipefail

core=$(cd "$(dirname "$0")/.." && pwd)
out=$(mkdir -p "${1:-$core/.pio/api-compile}" && cd "${1:-$core/.pio/api-compile}" && pwd)
shift || true
configs=${*:-none twist ownverter twist130}
pio=${PIO:-pio}
app=tests/firmware/api_compile

cmake_backup=$(mktemp)
cp "$core/zephyr/CMakeLists.txt" "$cmake_backup"
trap 'cp "$cmake_backup" "$core/zephyr/CMakeLists.txt"; rm -f "$cmake_backup"' EXIT

core_sha=$(git -C "$core" rev-parse --short HEAD 2>/dev/null || echo unknown)
echo "## API compile: every public call, built against Core \`$core_sha\`" > "$out/summary.md"
echo >> "$out/summary.md"

fail=0
python3 "$core/tools/api_calls.py" coverage "$core/$app/main.cpp" --summary "$out/summary.md" > /dev/null || {
    echo "FAIL  coverage: a public call is not called in $app/main.cpp (see $out/summary.md)"; fail=1; }

echo "| Configuration | Env | Build | Time |" > "$out/builds.md"
echo "|---|---|---|---|" >> "$out/builds.md"
for c in $configs; do
    env=APIC_$(echo "$c" | tr '[:lower:]' '[:upper:]')
    log="$out/$env.log"
    mkdir -p "$core/.pio/build/$env/Kconfig"   # Zephyr's CMake wants it after a clean
    t0=$(date +%s)
    if (cd "$core" && $pio run -e "$env") > "$log" 2>&1; then verdict=PASS; else verdict=FAIL; fail=1; fi
    t=$(( $(date +%s) - t0 ))
    echo "| $c | \`$env\` | $verdict | ${t} s |" >> "$out/builds.md"
    echo "$verdict  $c ($env, ${t} s)"
    if [ "$verdict" = PASS ]; then
        nm=$(ls "${PLATFORMIO_CORE_DIR:-$HOME/.platformio}"/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-nm)
        obj=$(find "$core/.pio/build/$env/$app" -name main.o | sort | tail -1)   # twist130 builds from $app/twist130/
        python3 "$core/tools/api_calls.py" compiled "$obj" --nm "$nm" --config "$c" \
            --known "$core/$app/known_failures.txt" --summary "$out/configs.md" > /dev/null || fail=1
    else
        { echo "### API compile: \`$c\`"; echo; echo "**Build failed**: errors from \`$env.log\`:"; echo; echo '```';
          grep -E "error|Error|undefined reference" "$log" | grep -v "^\*\*\*" | sort -u | head -20; echo '```'; echo; } \
            >> "$out/configs.md"
    fi
done
cat "$out/builds.md" >> "$out/summary.md"
echo >> "$out/summary.md"
cat "$out/configs.md" >> "$out/summary.md"
rm -f "$out/builds.md" "$out/configs.md"
echo "summary in $out/summary.md"
exit $fail
