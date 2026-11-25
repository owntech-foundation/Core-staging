# Core2 `tests/`: what is the framework, and does it run on Core main without the Safety API?

Research for owntech-foundation/Core-staging#3. Date: 2026-11-25.

**Sources.** Core2 clone, branch `safety-hw` at `1bdd0850`, compared with Core main `OTF-github/main` at `8111a0dc` (`git diff --stat OTF-github/main..HEAD`). Branch `apf_quadrature` (`4dd95e9d`, the uSolarVerter application branch) where the history needs it. Core-staging `main` at `7c33d269`. Every claim below points at a file in one of these trees. No hardware was touched. The one thing executed was a host smoke test in a scratch repo (§2.1).

## 1. Answer in brief

- **The framework is `tests/run_all.sh` plus `tests/common/{catalogue.py, make_record.py, review_report.py, interactive_plot.py, control_center.py, vendor/}` and a `tests/catalogue.json`.** Copied alone into an empty git repo with one trivial L0 test, it runs end to end and writes `record.md`, `report.html` and `control-center.html` (§2.1). None of it includes Safety API code.
- **Every L0 test on `safety-hw` is a Safety API test.** All 11 `tests/l0/*` folders build Safety API firmware (`tests/firmware/safety_*`), or run the Safety allocation scripts, or need the Safety devicetree bindings. The exception is **L0-CAL** (`spoc_cal`), which needs only the SPOC bench, its host code and stock Core-CUT/Core-MUX firmware (§3).
- **There is no L1 on `safety-hw`.** `tests/l1/` there is untracked and holds only `__pycache__`/`build`. L1, L2 and **L0-TIM ("L0 timing")** live on `apf_quadrature` and are uSolarVerter-specific (§3.2).
- **L0 timing on Core main:**
  - **L0-TIM** cannot run on Core main as it stands. Its harness is in the application's `src/timing/`. Its `TIMING` env is in `src/app.ini`. Its ADC-path probes (TIM-03) use `owntech_timing_probe.h`, a Core change that exists only on `apf_quadrature`. Its baseline assumes 4 flash wait states, also a Core change that is not on main.
  - **L0-TOV** (Safety API overhead) does have a Core-main build, B0. It is built by patching a worktree of `8111a0dc`: `flash_ws4.patch` plus an env appended to `platformio.ini`. That patching is needed because main lacks the `OWNTECH_APP_DIR` hook (§4).
- **The Core-MUX firmware does not need to come along.** Core2 does not vendor it. It pins a hash: `SPOC_Software` `mux-injector` `fe04fe3`. It copies two host files from `SPOC_Test_Software` `d02cd65`. `SPOC_Software` is a **private** repo, so staging can reference the pin but outside contributors cannot build the MUX (§5).
- **Smallest framework PR:** 11 files plus `.gitignore`, a framework self-test and a Python requirements file, with 4 small edits (§6). On-target plumbing and SPOC host code are separate follow-up PRs.

## 2. Framework inventory

Diff `OTF-github/main..safety-hw` touches `tests/` (101 files), `tools/` (2), `platformio.ini` (+1 line), `zephyr/CMakeLists.txt` and the Safety API modules/devicetree. The `tests/` and `tools/` files, classified:

| File(s) | Role | Board-agnostic? | Safety coupling | Verdict |
|---|---|---|---|---|
| `tests/run_all.sh` | Campaign runner: every `tests/l0/*/run.sh`, then `tests/l1/run.sh`, record, review page, control center | Yes | Its `object_code()` table lists safety and uSolarVerter object codes (`SUP-00`, `BST`, `GFL`…). Cosmetic: the default branch upper-cases the folder name. | **Framework** |
| `tests/common/make_record.py` | `@@` lines → `record.md`, findings register | Yes, stdlib only | Stamps a `single_phase_inverter` library commit (`make_record.py:35`). Reads `docs/testing/test-procedure.md`, which is guarded when absent (`:64`, `:102`). | **Framework** |
| `tests/common/catalogue.py` | Loads and checks `catalogue.json`; `--procedure` rewrites §6 of the procedure | Yes | None | **Framework** |
| `tests/common/control_center.py` | `tests/out/control-center.html` | Yes, stdlib only | One label hard-coded for a `timing` tab (`:284`) | **Framework** |
| `tests/common/review_report.py` (1917 lines) | Per-campaign `report.html` | Generic core plus about 900 lines of per-kind renderers | Renderers for timing (`:590–986`), allocation, supervision and every `spoc_*` (`:987–1454`). L1/L2 paths are optional (`tests/l1/review_plots.json`, `tests/l2`). Needs numpy and matplotlib. | **Framework**; split the renderers later |
| `tests/common/interactive_plot.py`, `vendor/uPlot*` | Interactive figures (uPlot 1.6.32, MIT) | Yes | None. Needs numpy. | **Framework** |
| `tests/catalogue.json` | Every test defined once | Format yes, content no | All 100+ entries are `tab: safety` | Ship an empty or self-test catalogue |
| `tests/README.md`, `tests/common/README.md` | Docs | Mostly | Layout table lists the safety tests. common README examples come from `apf_quadrature`. | Rewrite trimmed |
| `tests/common/check.h` | C++ `tc::Criterion` result lines for host L0 tests | Yes | None | Framework (useful with `l0_build.sh`) |
| `tests/common/l0_build.sh`, `shim/*` | Host build of L0 C++ tests | **No** | Include paths `src/boost_converter`, `src/grid_observer`, `owntech/lib/USB/single_phase_inverter` (`l0_build.sh:35–51`). uSolarVerter-specific. | Defer; generalise first |
| `tests/common/design_report.py` | D-GFL design reports | No (uSolarVerter `docs/testing/design/D-GFL-*`) | None | Leave on the app branch |
| `tests/common/target_harness.py`, `target_preflight.sh` | Build/flash an env over ST-Link, SWD RAM read/write, symbol lookup | Yes in code | Default env `SAFETY_SUP` (`target_preflight.sh:17`). `flash()` calls `tools/restore_board_lines.py`. Test envs need the `OWNTECH_APP_DIR` CMake hook (§4). | On-target follow-up PR |
| `tools/restore_board_lines.py` | Undoes `pre_version.py`'s rewrite of `set(BOARD/SHIELD …)` | Yes | None | Goes with the on-target PR |
| `tests/firmware/pio_tests.ini` + `platformio.ini` `extra_configs` line | Test firmware envs | Mechanism yes | All 11 envs are `SAFETY_*` | Mechanism in the on-target PR, envs stay with Safety |
| `tests/common/rigol_mho.py`, `udev/99-rigol-usbtmc.rules` | MHO984 scope driver | Bench-specific, not board-specific | None | SPOC follow-up PR |
| `tests/common/spoc/{boards,thingset,mux_controller,injector,waveform}.py`, `spoc_preflight.sh` | SPOC bench host side | Bench-specific | `boards.py` accepts CUT root `Safety` as well as `Cut` (`ROLES`, line 23). The preflight message mentions "the safety test firmware". | SPOC follow-up PR |
| `tests/common/scope.py` | Tektronix MSO54 (L2 bench) | Bench-specific | None. Its udev rule `99-tektronix-mso54.rules` exists only on `apf_quadrature`. | Leave out |
| `tools/safety_check.py` | Safety allocation check | — | Safety API | Safety |

### 2.1 Standalone smoke test (executed)

The framework was copied into an empty git repo: `tests/common/*`, `tests/run_all.sh`, a one-entry `catalogue.json` (tab `bench`, test `L0-EXA-01`), and a `tests/l0/example/run.sh` that prints one `@@ PASS` line. Results:

- `catalogue.py`: OK.
- `run_all.sh --l0`: `PASS: 1 pass`, `record.md` written.
- `review_report.py` (with Core2's `.venv`): `report.html` written.
- `control_center.py`: `control-center.html` written.

Problems found:

1. **`control_center.py` crashes when the catalogue's `version` is a number.** It calls `html.escape(cat.get("version"))` (`:248`), and Core2's value is the string `"safety-1"`. Either coerce the value with `str()` or validate it in `catalogue.py`.
2. **There is no recipe for the `.venv`.** `run_all.sh` skips the review page without `.venv` and points to `tests/l1/setup.sh`, which exists only on `apf_quadrature`, together with `tests/l1/plant/requirements.txt`. The system matplotlib here failed on the NumPy 2 ABI. The framework needs its own `tests/requirements.txt` (numpy, matplotlib; pyserial for SPOC) and setup line.

## 3. Per-test dependencies

### 3.1 L0 tests on `safety-hw`

Sources: each `tests/l0/<t>/run.sh`, `run.py`, `README.md`, and the firmware that they build.

| Test (ID) | Firmware / env | Safety API | Devicetree / overlay changes | Other Core2-only code | Hardware |
|---|---|---|---|---|---|
| `safety_allocation` (L0-ALC) | `SAFETY_CHECK` per shield | Yes: `owntech_safety_api/scripts/safety_allocation.py`, `protection_manifest.py` | Yes: `safety-*.yaml` bindings and shield overlays (twist, ownverter, o2) | `SAFETY_DRAFT_OVERLAY` CMake hook, `tools/safety_check.py` | None (host) |
| `safety_supervision` (L0-SUP) | `SAFETY_SUP`, `tests/firmware/safety_supervision` | Yes: `safety_supervision.h`, `SAFETY_TEST_HOOKS` | No shield | `OWNTECH_APP_DIR` hook, `target_harness.py`, `restore_board_lines.py` | Bare Spin + ST-Link |
| `safety_comparator` (L0-CMP/AWD/SST/STK) | `SAFETY_COMP` | Yes | Yes: its `app.overlay` uses safety bindings | as above; HRTIM/ADC/NVS driver changes | Bare Spin + ST-Link |
| `safety_api` (L0-API) | `SAFETY_API`, `SAFETY_API_DEV`, `SAFETY_MODE` | Yes: `safety_mode.h`, `disarm()` | Yes: `safety-config` node | `nvs_storage_delete_data()` (new in Core2) | Bare Spin + ST-Link |
| `safety_timing` (L0-TOV) | `SAFETY_TIME{,_PROBE,_DEV}` + `TOV_BEFORE` | Yes, B1/B1p/B2. B0 is Core main (`__has_include("safety_mode.h")` in `tests/firmware/safety_timing/main.cpp:38`). | `idle-band` lines stripped for main (`build_before.sh`) | `flash_ws4.patch` (4 flash wait states, `hardware_auto_configuration.cpp`) | Bare Spin + ST-Link |
| `spoc_cal` (L0-CAL) | **None flashed.** The CUT runs Core-CUT (`/Cut/PwmOut`) or the safety firmware's `/Cal` (`trim.py:45–54`). | **No**, when Core-CUT is used | No | `rigol_mho.py`, `spoc/` (no injector) | SPOC bench: CUT + MUX + MHO984 |
| `spoc_trip` (L0-TRP) | `SAFETY_L0_TWIST/OWNVERTER`, `tests/firmware/safety_l0` | Yes | Yes: `app.overlay`, manifest checked with `tools/safety_check.py` | `spoc/injector.py` → Core-MUX `fe04fe3` | SPOC + ST-Link |
| `spoc_dead` (L0-CPU) | `SAFETY_SUP` then `SAFETY_L0_TWIST` | Yes | Yes | injector | SPOC + ST-Link |
| `spoc_edge` (L0-EDG) | `SAFETY_L0_TWIST` | Yes | Yes | injector pulse mode (`fe04fe3`) | SPOC + ST-Link |
| `spoc_latency` (L0-LAT) | `SAFETY_L0_*` | Yes | Yes | imports `spoc_trip/run.py` | SPOC + ST-Link + direct probe |
| `spoc_adc` (L0-ADC) | `SAFETY_L0_*` | Yes | Yes | imports `spoc_trip/run.py` | SPOC + ST-Link |

The firmware under `tests/firmware/` (`safety_api*`, `safety_comparator`, `safety_l0`, `safety_mode_*`, `safety_supervision`, `safety_timing*`) all includes Safety API headers. None of it is framework.

### 3.2 L0-TIM, L1 and L2 (on `apf_quadrature`, not on `safety-hw`)

- `tests/l0/timing/` on `safety-hw` holds only an untracked `__pycache__`. Its sources (`run.sh`, `read_report.py`, `evaluate.py`, `baseline.json`…) were last committed on `apf_quadrature` (`5b54bbe1`). Those sources require:
  - the application's `src/timing/timing_{probe,report}.h` and `timing_harness.cpp`;
  - `env:TIMING` in `src/app.ini` (`-DTIMING_HARNESS`);
  - for TIM-03, Core probes in `owntech_spin_api/zephyr/public_api/owntech_timing_probe.h` and in `Sensors.cpp` (`OT_TIMING_*`). These are on `apf_quadrature` and are not on Core main.

  Its reader and evaluator are stdlib Python plus OpenOCD, the same pattern as `target_harness.py`.
- **Does L0 timing work on Core main?**
  - The mechanism works (ST-Link flash into the MCUboot slot, `nm` + SWD dump).
  - The test does not: it measures the uSolarVerter `controlStep`, which is not in Core.
  - Its numbers also assume 4 flash wait states. Core main runs at 8 (`flash_ws4.patch` header, `build_before.sh:11`).
  - A Core-level timing test would need a Core-owned harness app in `tests/firmware/`, the `OWNTECH_APP_DIR` hook, and a decision on the wait-state change.
- L1 (`tests/l1/`, 80 files: plant model, runner, studies, `setup.sh`) and L2 (`tests/l2/bench.py`, `session.py`) are uSolarVerter application suites. `tests/README.md` says so: "Application-specific tests (for example the uSolarVerter's L0/L1/L2 suites) live on their application branches".

## 4. What Core main lacks for on-target test firmware

Core-staging `main` equals Core main plus one glossary commit (`git diff --stat 8111a0dc github-OTF-staging/main`: `GLOSSARY.md` only). Main's `zephyr/CMakeLists.txt` hard-codes `${PARENT_DIR}/src` for `app.conf`/`app.overlay` and `../src/main.cpp` (lines 69, 80, 113–114). `safety-hw` adds:

- `OWNTECH_APP_DIR`, which builds an app folder other than `src/`. This part is generic and goes with on-target plumbing.
- `SAFETY_DRAFT_OVERLAY`. This part is Safety-only and must be split off.

`build_before.sh` confirms the gap: "Core main has no OWNTECH_APP_DIR hook and no pio_tests.ini, so the firmware is built in a git worktree of that commit".

## 5. Core-MUX firmware (SPOC_Software)

- **Not vendored.** No MUX firmware source or binary is in Core2. The pin is text:
  - `docs/safety/spoc_bench.md:186`: "branch `mux-injector` (from `Core-MUX` `295a3f6`), **pinned at `fe04fe3…`**";
  - `tests/common/spoc/injector.py:3–4`;
  - `MUX_FW` strings in `spoc_trip/run.py:40` and `spoc_cal/run.py:32`, which are stamped into reports.
- **Host files copied in.** `spoc/thingset.py` and `spoc/mux_controller.py` are copied unchanged from `SPOC_Software` `SPOC_Test_Software` `d02cd65` (`spoc/__init__.py:3–4`).
- **The repository is private.** `gh api repos/owntech-foundation/SPOC_Software` → `private: true`. Commits `fe04fe3`, `295a3f6` and `d02cd65` all exist.
- **Which tests need which MUX firmware.** Only the injector cases (TRP, CPU, EDG, LAT, ADC) need the `mux-injector` branch. L0-CAL routes only, so `Core-MUX` `295a3f6` is enough. The bench log of 2026-11-19 records that the MUX's previous unknown firmware (2fe3:0102) drove no pins and was lost.
- **Inconsistency to fix when porting.** `tests/l0/spoc_cal/README.md:17` still says `b500a4e`, while the code says `fe04fe3`.

Recommendation: keep the firmware out of Core. Carry the pin as data, one constant in `spoc/__init__.py` that the run scripts read instead of three string copies. Either make `SPOC_Software` (or its `Core-MUX` branch) public, or publish a released MUX binary, before SPOC tests become a staging gate that others must reproduce.

## 6. Smallest framework PR to staging

**PR 1: "test framework: campaign runner, record, review page, control center"**. No firmware, no hardware.

| File | Change from Core2 |
|---|---|
| `tests/run_all.sh` | Trim `object_code()` to the default branch plus `l1) INT-00`. Change the `.venv` hint to point at the new requirements file. |
| `tests/catalogue.json` | New: one tab (e.g. `framework`) and one entry for the self-test; `version` a string |
| `tests/README.md` | Trimmed: Run, Layout (framework only), Adding a test |
| `tests/common/README.md` | Trimmed to the files shipped. Drop the `l0_build`/`shim` sections and the uSolarVerter examples, or mark them as an example. |
| `tests/common/catalogue.py` | As is (the procedure path is optional) |
| `tests/common/make_record.py` | As is. Optionally guard or drop the `single_phase_inverter` stamp. |
| `tests/common/control_center.py` | `str()` the catalogue version (bug in §2.1). Drop the `timing` tab label. |
| `tests/common/review_report.py` | Ship as is (verified to run), or strip the per-kind renderers (`timing_*`, `allocation_*`, `supervision_*`, `spoc_*`, about 900 lines) and bring each back with its test. Stripping is cleaner but riskier; prefer as-is plus a follow-up issue. |
| `tests/common/interactive_plot.py`, `tests/common/vendor/{uPlot.iife.min.js, uPlot.min.css, uPlot-LICENSE}` | As is |
| `tests/common/check.h` | As is (optional; stand-alone header) |
| `tests/l0/framework_selftest/run.sh` | New: emits PASS, INFO and BLOCKED lines so a campaign proves the pipeline without hardware |
| `tests/requirements.txt` | New: numpy, matplotlib (review page) |
| `.gitignore` | `tests/out/` (as in Core2; campaigns and the control center stay local) |

**Stub or split for PR 1:** the catalogue content (empty), `object_code()` entries, the README layout rows, the `control_center` version bug, and the `.venv` recipe. Nothing in the zephyr tree or `platformio.ini` changes.

**Follow-ups, in order:**

1. **On-target plumbing:**
   - `zephyr/CMakeLists.txt` `OWNTECH_APP_DIR` only, without `SAFETY_DRAFT_OVERLAY`;
   - the `platformio.ini` `extra_configs` line;
   - `tests/firmware/pio_tests.ini`, containing one non-safety env;
   - `tests/common/target_harness.py`, `target_preflight.sh` (default env no longer `SAFETY_SUP`), `tools/restore_board_lines.py`;
   - a minimal smoke firmware that proves build → flash → SWD read on a bare Spin. This is the first bench-gated PR.
2. **SPOC host side:**
   - `rigol_mho.py`, `udev/99-rigol-usbtmc.rules`, `spoc/`, `spoc_preflight.sh`;
   - L0-CAL with Core-CUT as its first SPOC test, which needs no Safety API. Fix the pin inconsistency there.
3. **Host L0 build:** generalise `l0_build.sh` and `shim/` (no `src/boost_converter` or `single_phase_inverter` paths).
4. **Safety API tests:** come with the Safety API itself: every `tests/l0/safety_*` and `spoc_{trip,dead,edge,latency,adc}`, every `tests/firmware/safety_*`, `tools/safety_check.py`, and the catalogue entries.

## 7. Open points

- Whether a Core-owned L0 timing test is wanted. It needs a Core harness app and a decision on `flash_ws4` (4 vs 8 wait states), which is itself a Core behaviour change that sits in `safety-hw` (`hardware_auto_configuration.cpp` +52 lines).
- `docs/testing/test-procedure.md` (findings register, §6) exists only on the application branch. The framework tolerates its absence. Decide whether staging gets a Core-level procedure.
- Making `SPOC_Software` public, or releasing a MUX binary.
