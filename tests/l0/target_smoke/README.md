# L0-TGT: on-target smoke test

Proves the on-target plumbing every on-target test uses: env `SMOKE` ([`tests/firmware/smoke`](../../firmware/smoke/main.cpp)) builds from its own app folder (`OWNTECH_APP_DIR`), flashes over the ST-Link into the MCUboot slot, and its report struct is read over SWD without halting the CPU.

```bash
bash tests/common/target_preflight.sh   # what the test needs, and how to fix what is missing
tests/run_all.sh --l0 --target          # REFLASHES the Spin on the ST-Link
```

Without `--target` every criterion reports BLOCKED. The firmware drives no output but the Spin's LED, so it is safe on a Spin mounted on a power stage. Flash the board's usual firmware again afterwards.

| ID | Criterion |
|---|---|
| L0-TGT-01 | `pio run -e SMOKE -t upload` exits 0 (log `target_smoke_flash.log` in the campaign) |
| L0-TGT-02 | `g_smoke_report` read over SWD within 30 s of the upload (MCUboot and Zephyr start take about 2 s): magic `0x534d4b31`, layout 1 |
| L0-TGT-03 | The background task advances: at least 50 ticks in 2 s (10 ms task) |
| L0-TGT-04 | INFO: the MCU's `DBGMCU_IDCODE` |
