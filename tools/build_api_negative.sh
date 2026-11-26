#!/usr/bin/env bash
# Negative build checks of the API build tier: a setting the shield cannot support must stop the build at configure
# time with a message that names the option and the missing device-tree node, not with a `__device_dts_ord_N` error.
#
#   tools/build_api_negative.sh [output dir]
#
# Builds each env APIC_NEG_* (tests/firmware/pio_tests.ini, apps in tests/firmware/api_compile/negative/). A check
# passes when the build fails AND its log carries the expected message. Appends a table to
# <output dir>/summary.md; exits 1 if a check does not pass. Restores zephyr/CMakeLists.txt. PIO overrides the
# PlatformIO command.
set -uo pipefail

core=$(cd "$(dirname "$0")/.." && pwd)
out=$(mkdir -p "${1:-$core/.pio/api-negative}" && cd "${1:-$core/.pio/api-negative}" && pwd)
pio=${PIO:-pio}

cmake_backup=$(mktemp)
cp "$core/zephyr/CMakeLists.txt" "$cmake_backup"
trap 'cp "$cmake_backup" "$core/zephyr/CMakeLists.txt"; rm -f "$cmake_backup"' EXIT

# env | setting | expected message (grep -F)
checks=(
    "APIC_NEG_UART|CONFIG_OWNTECH_UART_API=y on Twist 1.4.2 (no usart1)|CONFIG_OWNTECH_UART_API=y, but the device tree has no enabled usart1"
    "APIC_NEG_CAN|ThingSet CAN on Twist 1.3.0 (no thingset,can)|CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN=y, but the device tree has no enabled \`thingset,can\` chosen node"
)

{ echo "### API build tier: negative checks"; echo
  echo "Each setting below cannot work on its shield: the build must stop with a message naming it."; echo
  echo "| Setting | Env | Build | Expected message | Check |"; echo "|---|---|---|---|---|"; } >> "$out/summary.md"

fail=0
declare -A built
for c in "${checks[@]}"; do
    IFS='|' read -r env what msg <<< "$c"
    log="$out/$env.log"
    if [ -z "${built[$env]:-}" ]; then
        mkdir -p "$core/.pio/build/$env/Kconfig"   # Zephyr's CMake wants it after a clean
        if (cd "$core" && $pio run -e "$env") > "$log" 2>&1; then built[$env]=succeeded; else built[$env]=failed; fi
    fi
    # CMake wraps its messages: compare with the log's whitespace folded to single spaces
    if [ "${built[$env]}" = failed ] && tr -s ' \n' '  ' < "$log" | grep -qF -- "$msg"; then verdict=PASS; else verdict=FAIL; fail=1; fi
    echo "| $what | \`$env\` | ${built[$env]} | \`\` $msg \`\` | $verdict |" >> "$out/summary.md"
    echo "$verdict  $env: $msg"
    [ "$verdict" = FAIL ] && grep -E -A6 "CMake Error|error:" "$log" | grep -v deprecated | head -20
done
echo >> "$out/summary.md"
exit $fail
