# tests/common

Shared code for every test. Nothing here is compiled into the firmware.

| File | Role |
|---|---|
| [`check.h`](check.h) | `tc::Criterion`, `tc::info`, `tc::blocked`, `tc::finish`: per-criterion reporting |
| [`make_record.py`](make_record.py) | Turns result lines into `record.md` |
| [`review_report.py`](review_report.py) | Writes a campaign's or study's `report.html` |
| [`interactive_plot.py`](interactive_plot.py) | Interactive review figures: zoom in time, read values (uPlot) |
| [`vendor/`](vendor/) | uPlot 1.6.32 (MIT, `uPlot-LICENSE`), inlined into the review pages so they work offline |
| [`catalogue.py`](catalogue.py) | Loads and checks [`../catalogue.json`](../catalogue.json) |
| [`control_center.py`](control_center.py) | Writes `tests/out/control-center.html`: where the validation stands, one tab per stage |
| [`target_harness.py`](target_harness.py) | On-target tests: build and flash a `tests/firmware` env over the ST-Link (then `tools/restore_board_lines.py`), find a symbol, read and write RAM over SWD without halting, command/acknowledge protocol, result lines |
| [`target_preflight.sh`](target_preflight.sh) | What on-target tests need (PlatformIO, OpenOCD, ST-Link, USB access, MCUboot), one line each, with the fix; `--install` runs the fixes that need no sudo |
| [`rigol_mho.py`](rigol_mho.py) | Rigol MHO984 over Linux usbtmc (SPOC bench): setup, single acquisition, the scope's own measurements, screen waveforms. Standalone: `python3 tests/common/rigol_mho.py` prints `*IDN?` and CH1..CH4 measurements |
| [`spoc/`](spoc/) | SPOC bench host side: `boards.py` finds the CUT and MUX shell ports by their ThingSet root; `thingset.py`, `mux_controller.py` copied from SPOC_Software (`MuxController.route('ch1', 'PA8')`); `waveform.py` metrics; firmware pins `CUT_FW`, `MUX_FW` in `__init__.py` |
| [`spoc_preflight.sh`](spoc_preflight.sh) | What the SPOC bench needs: both Spins, scope access, scope answering `*IDN?` |
| [`udev/`](udev/) | `99-rigol-usbtmc.rules`: MHO984 access without root (`sudo cp tests/common/udev/99-rigol-usbtmc.rules /etc/udev/rules.d/ && sudo udevadm control --reload`, then replug the scope) |

## check.h

A `tc::Criterion` collects observations and prints **one** result line when it goes out of scope. A failing observation does not stop the test, so every criterion gets a verdict.

```cpp
{
    tc::Criterion c("L0-ABC-02.a", "Leg 1 alone below and at its current limit");
    c.check("leg 2 off", !leg2_enabled);                    // boolean
    c.near("current ref", i_ref, 2.0, 1e-5);                // |measured - expected| <= tol
    c.range("duty", duty, 0.0, 0.95);                      // lo <= measured <= hi
}
tc::info("L0-ABC-03", "PLL lock time", t, "s");            // INFO: no limit stated
tc::blocked("L1-ABC-01", "IDLE on a healthy grid", "plant model missing");
return tc::finish();                                       // 1 if any criterion failed
```

Result line format, one per criterion:

```
@@<TAB>id<TAB>verdict<TAB>description<TAB>limit<TAB>measured
```

| Verdict | Produced by |
|---|---|
| `PASS` | `Criterion` with at least one observation and none failed |
| `FAIL` | `Criterion` with a failed observation, or with no observation at all |
| `INFO` | `tc::info` |
| `BLOCKED` | `tc::blocked` |

## make_record.py

```bash
python3 tests/common/make_record.py --out DIR --levels L0,L1 LOG [LOG ...]
```

Reads every `@@` line from the logs and, when the branch has one, the findings register of `docs/testing/test-procedure.md`, and writes `DIR/record.md`: front-matter (commits, toolchain, counts, verdict), setup table, verdict chart, test-case table, results table (`BLOCKED` first, then `FAIL`), raw-data list and, on failure, the follow-up reminder. A `FAIL` on a criterion listed in an open finding is marked *known* with its issue, any other `FAIL` **new**; an open finding whose criteria all pass is listed under `findings_to_close`. Exit code 1 if any criterion is `FAIL` (known or new) or none was reported. Standard library only.

Application branches may set `SINGLE_PHASE_INVERTER_DIR` to the library checkout under test; the record then stamps its commit.
