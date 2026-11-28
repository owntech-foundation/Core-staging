# L0-NVS: the MetaData / NVS module of the API regression suite

Module of the API regression suite ([Regression suite module: MetaData and NVS](https://github.com/owntech-foundation/Core-staging/issues/42);
tiers, depth and coverage fields decided in [#35](https://github.com/owntech-foundation/Core-staging/issues/35#issuecomment-7196951762)).
It follows the [PWM module](../target_pwm/README.md) layout: env `NVS` ([`tests/firmware/metadata_nvs/`](../../firmware/metadata_nvs/main.cpp))
is a step executor. The host writes a step and its arguments over SWD, the firmware calls the API and publishes the
return values and the bytes a getter wrote (pre-filled with `0xA5`, so "nothing written" can be seen). The firmware
touches no NVS at boot. It holds no test logic.

**Calls covered** (effect depth: return codes and bytes read back):

- all 15 `spin.metaData` calls;
- `spin.data.storeConversionParametersInMemory` / `retrieveConversionParametersFromMemory` (pin 51 = PA2, ADC1
  channel 3, not a Twist sensor);
- `shield.sensors.storeParametersInMemory` / `retrieveParametersFromMemory` (V_HIGH, Twist config on the bare CUT);
- `nvs_storage_get_current_version`, `nvs_storage_get_version_in_nvs`, `nvs_storage_get_free_space`,
  `nvs_storage_clear_all_stored_data`, `nvs_storage_store_data`, `nvs_storage_retrieve_data` (directly, on 0x7F01, a
  key no category uses, and through the calls above).

**Not covered**: the Safety API's threshold store/retrieve. Safety rows are a gap until the Safety API graduates
(#35: no tests against the old API).

Expected values come from `docs/spin_metaDataAPI.md` (field lengths, return codes, "survives a reset or a firmware
reflash", `clearAllMetaData` leaves other modules' data), `DataAPI.h` / `Sensors.h` (store/retrieve return codes),
`nvs_storage.h`, and the Twist devicetree (V_HIGH default gain). They never come from the driver's own code. Every
value written carries a random per-run tag, so a read-back passes only on what this run wrote.

## Sequence

| Phase | IDs | What |
|---|---|---|
| Backup | 01 | 4 kB of the NVS partition (0x0807F000) read over SWD while the CUT still runs its previous firmware. Nothing is written without it. |
| As found | 15, 16 | a store on the partition as the CUT had it; clear + reset; `nvs_storage_store_data` / `retrieve_data` called directly |
| First boot | 02–07 | argument errors; `clearAllMetaData` then every getter -1; every setter then its getter; overwrite; versions and free space; conversion parameters store → change in RAM → retrieve |
| ST-Link reset | 08 | `reset run` as in `target_harness.py`: every field and both parameter sets read back |
| Reflash | 09 | env `SMOKE` flashed (its report magic read: another firmware really ran), then env `NVS` flashed again: same checks |
| Clears | 10–11 | `clearAllMetaData` keeps the conversion parameters; cleared stays cleared after a reset; `nvs_storage_clear_all_stored_data`, reset, NVS empty, store again, reset, read back |
| Pin error | 12 | store/retrieve on a pin with no ADC channel |
| Write-back | 13 | backup written over the partition (erase, program the non-erased double-words, verify), read back, compared byte for byte |

## Shared NVS: how the CUT's data is kept

NVS on the CUT also holds sensor calibration (`ADC_CALIBRATION` keys), safety thresholds (`MEASURE_THRESHOLD`, read
by the Safety API at init, so L0-SAF depends on it) and any metadata someone wrote. The module **writes keys that
others use** (the metaData fields, the conversion parameters of ADC1 channels 3 and 9) and erases the whole partition
in L0-NVS-11. So it does not rely on private keys: it **restores the previous contents**. The partition is backed up
over SWD before the first write and written back at the end in a `finally` block, whatever happened in between, and
then compared byte for byte (L0-NVS-13). The backup stays in the campaign folder as `nvs_partition_before.bin`. If
L0-NVS-13 fails, the log prints what to write back by hand.

**The write-back is sparse**: erase the 4 kB, then program only the 8-byte double-words of the backup that are not
`0xFF`. Programming `0xFF` over an erased double-word is not neutral on the STM32G4. The double-word still reads
`0xFF`, so Zephyr's "is it erased?" check passes, but it can no longer be programmed, and the next NVS write there
fails with -EIO (`nvs_storage_store_data` -5, metaData setters -1). This was seen on the CUT on 2026-11-28: every store
failed on the partition as found, and a byte-identical sparse write-back fixed it. A plain `flash write_image erase`
of a partition image leaves every free slot in that state, so **never restore an NVS image that way**. L0-NVS-15
stores on the partition as found, so a CUT left in that state shows up as a failure.

The module then clears NVS (`nvs_storage_clear_all_stored_data` and a reset) and runs its checks on an empty
partition. They exercise the API, not whatever the CUT held before.

Reflashes do not touch the partition: the test envs and Core-CUT are programmed into the MCUboot slot at 0x08047800,
and only the sectors the image covers are erased. L0-NVS-09 checks this for the test envs. The first bench run also
compared the partition after `bench_restore.sh` (Core-CUT upload) with the backup: they were equal.

## Tier and duration

`tier: nightly` (and `target_tier: nightly`). The module builds and flashes two firmwares (NVS, SMOKE), flashes NVS
twice, resets the CUT four times, and writes flash on every run. `tests/run_all.sh --l0 --target --nightly` runs it.
Without `--nightly` (CORE_NIGHTLY=1) every criterion reports BLOCKED, like the supervised rows.

Duration on the SPOC bench (L0-NVS-14): **279–289 s** (~4.7 min, 185 steps) with both builds cached; a cold build
adds ~60–75 s per firmware (NVS, SMOKE), so ~7 min on a fresh runner checkout.

```sh
tests/run_all.sh --l0 --target --nightly      # campaign with the nightly modules
CORE_TARGET=1 CORE_NIGHTLY=1 bash tests/l0/target_metadata_nvs/run.sh   # alone, inside a bench_lock'ed script
```

`known_failure` in [`catalogue.json`](../../catalogue.json) marks the checks that fail on `main` today, with the
issue that tracks them. They are reported as known and do not fail the campaign.
