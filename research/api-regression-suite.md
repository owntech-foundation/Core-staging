# API regression suite: inventory, test sources, testability, cost

Research for [Core-staging#35](https://github.com/owntech-foundation/Core-staging/issues/35). Read on 2026-11-26 against staging `main` at `ec6461e9` (after the Zephyr 4.4.2 merge, PR #33). Every claim cites the file, ticket, or run it comes from. Lines marked **Assumption** are not verified and are for the grilling.

## Summary

- **The user-facing API has 196 public calls**: Spin 99, Shield 48, Communication 25, Safety 16, Task 8. The C drivers under it export another **86** (hrtim 57, adc 9, dac 7, nvs_storage 6, timer 4, comparator 2, ngnd 1). Most driver calls are reached through a HAL. About 15 are not, and nothing exercises them.
- **Examples use 64 of the 196 calls** (owntech-foundation/examples `main` 345c8a0, 27 apps). **132 calls appear in no example.** The whole Safety API, MetaData, UART, comparator, every getter of PwmHAL/PowerAPI and every NVS store/retrieve are among them. Example builds are a good compile check, but they cover a third of the API.
- **The staging tests exercise 25 distinct calls**, all through the four fix tests (SMOKE, CTK, DAC, ADD). No test checks what any of those calls does except the fixed bug.
- **Testability (primary method, draft)**: 121 on-chip (SWD read of registers / a report struct), 37 scope through the MUX, 2 direct probe, 17 loopback, 16 second Spin, 1 power stage, 2 build-only. **About 80 % can be judged on a bare Spin with no wiring change.**
- **The Shield API is mostly testable without a power stage.** On a bare Spin built with `SHIELD=twist`, `shield.power` drives HRTIM and GPIO pins, and `shield.sensors` reads Spin ADC pins. Only real conversion (buck/boost behaviour, calibration against real sensors) needs a power stage.
- **Cost, from real campaigns**: build + flash takes **53–59 s per firmware**. Every on-target env does a full Zephyr build. Checks take 11–35 s per firmware, and L0-CAL takes 108–111 s. The PR runner's full campaign takes **9.6 min** for 5 firmwares, and the job **11.6 min**.
- **Draft suites**: a **per-PR tier of about 15–20 min** (L0-CAL, SMOKE, STLink, plus 5 module firmwares). A **nightly tier of about 40 min per ref**, run on `main` and on `pre-zephyr-4.4`, so **about 80 min**. A supervised tier for the power stage and the second Spin. A one-binary design ("regression firmware", see Cost) could cut the per-PR tier to about 8–10 min.
- **Surprises** (details in [Surprises](#surprises)):
  - 8 call names in `docs/*.md` do not exist in the API.
  - `thread_error` prints I2's error flag for V2.
  - `communication.analog.getAnalogCommValue` reads PC4 while the DAC drives PA6, so a bare Spin needs a loopback.
  - Core2's Safety API tests target a redesigned API that only shares names with staging's.

## Contents

1. [API inventory](#1-api-inventory)
2. [Relied-on behaviour that is not a call](#2-relied-on-behaviour-that-is-not-a-call)
3. [Sources of tests](#3-sources-of-tests)
4. [Testability per call and group](#4-testability-per-call-and-group)
5. [Cost](#5-cost)
6. [Draft coverage matrix](#6-draft-coverage-matrix)
7. [Build tickets this spawns](#7-build-tickets-this-spawns)
8. [Surprises](#surprises)
9. [Sources](#sources)

## 1. API inventory

**Method.**
- Public member functions were extracted from the headers that `SpinAPI.h`, `ShieldAPI.h`, `TaskAPI.h`, `CommunicationAPI.h` and `SafetyAPI.h` expose. These are `zephyr/modules/owntech_*/zephyr/src/*.h` and `public_api/*.h`, public sections only.
- Each count was cross-checked with a regex over the same headers.
- Calls under `#ifdef CONFIG_SHIELD_*` are counted once.
- Overloads count separately: `safety.initShield` ×2.
- Friend-only members are not counted: `DataAPI::setDispatchMethod` and `doFullDispatch` (`DataAPI.h:104-107, 712-719`).

| Module | Object | Calls |
|---|---|---|
| Spin | `spin.pwm` (PwmHAL) | 46 |
| Spin | `spin.data` (DataAPI) | 18 |
| Spin | `spin.metaData` | 15 |
| Spin | `spin.gpio` | 6 |
| Spin | `spin.dac` | 4 |
| Spin | `spin.uart` (`CONFIG_OWNTECH_UART_API`) | 4 |
| Spin | `spin.led` | 3 |
| Spin | `spin.timer` (encoder) | 2 |
| Spin | `spin.comp` | 1 |
| **Spin** | | **99** |
| Shield | `shield.power` (PowerAPI) | 28 |
| Shield | `shield.sensors` | 18 |
| Shield | `shield.ngnd` (`CONFIG_OWNTECH_NGND_DRIVER`) | 2 |
| **Shield** | | **48** |
| Task | `task` | **8** |
| Communication | `communication.can` | 15 |
| Communication | `communication.rs485` | 5 |
| Communication | `communication.analog` | 3 |
| Communication | `communication.sync` | 2 |
| **Communication** | | **25** |
| Safety | `safety` | **16** |
| **User-facing total** | | **196** |
| Drivers | hrtim 57, adc 9, dac 7, nvs_storage 6, timer 4, comparator 2, ngnd 1 | **86** |

There is **no Spin "version" API**. Version information lives only in `spin.metaData` (`get/setSpinVersion`, `get/setShieldVersion`) and in `nvs_storage_get_current_version` / `nvs_storage_get_version_in_nvs`.

**Pending on feature branches (not on staging `main`):** the DMA duty sequences (PwmHAL wrappers, PowerAPI duty sequences, multi-leg start/stop) on `feature/hrtim-dma-sequences`. They will add calls to `spin.pwm` and `shield.power` and must join this matrix when they merge.

The tables below give, for each call:
- what a user can observe from it;
- the proposed primary test method and suite (draft, see §4 and §6);
- how many examples call it;
- whether `docs/*.md` shows it;
- whether a test firmware calls it: staging `tests/firmware`, or Core2 `safety-hw` `tests/firmware`.

#### `spin.pwm` (46)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initUnit` | TIMx registers (PER, CMP, DT, OUTxR) set to defaults; returns void | on-chip | per-PR | 5 | yes | staging, Core2 |
| `startDualOutput` | both pins of the unit switch (OENR bits; complementary pair on pins) | scope (MUX) | per-PR | 5 | yes | staging, Core2 |
| `stopDualOutput` | pins go to idle level (ODISR / OENR) | scope (MUX) | per-PR | 1 | – | Core2 |
| `startSingleOutput` | only one pin of the pair switches | scope (MUX) | per-PR | – | yes | – |
| `stopSingleOutput` | one pin idles, the other keeps switching | scope (MUX) | per-PR | – | – | – |
| `setModulation` | left-aligned vs up-down (centre-aligned): pulse centre moves; TIMxCR UDM bit | on-chip | per-PR | 3 | yes | Core2 |
| `setSwitchConvention` | which pin (PWMx1/PWMx2) carries the duty: pin duty swaps | scope (MUX) | per-PR | 1 | yes | – |
| `initFixedFrequency` | HRTIM master/unit PER for the frequency; frequency on pin | scope (MUX) | per-PR | 3 | yes | Core2 |
| `initVariableFrequency` | prescaler chosen for the minimal frequency; frequency on pin | scope (MUX) | nightly | – | – | – |
| `setDeadTime` | dead time on pins (DTxR register on-chip; ns edge gap needs direct probe) | direct probe | nightly | – | yes | Core2 |
| `setDutyCycle` | CMP1 register; duty on pin (MUX for duty, direct probe near 0/1, Core#166) | scope (MUX) | per-PR | 4 | yes | staging, Core2 |
| `setDutyCycleRaw` | CMP1 = raw value (register) | on-chip | per-PR | – | – | – |
| `setPhaseShift` | reset/phase offset between units on pins (MUX: us-level; probe: ns) | scope (MUX) | per-PR | 3 | yes | – |
| `setMode` | voltage vs current mode (EEV reset source register) | on-chip | per-PR | 1 | yes | – |
| `getMode` | return value equals what was set | on-chip | per-PR | – | – | – |
| `setEev` | external event assignment register | on-chip | per-PR | – | – | – |
| `getEev` | return value equals what was set | on-chip | per-PR | – | – | – |
| `getModulation` | return value | on-chip | per-PR | – | – | – |
| `getSwitchConvention` | return value | on-chip | per-PR | – | – | – |
| `getPeriod` | return value = PERxR | on-chip | per-PR | – | – | – |
| `getPeriodMax` | return value vs datasheet | on-chip | per-PR | – | – | – |
| `getPeriodMin` | return value | on-chip | per-PR | – | – | – |
| `setAdcTriggerPostScaler` | ADC trigger postscaler register; samples per period on-chip | on-chip | per-PR | – | yes | – |
| `setAdcTrigger` | ADCxR routing register; ADC sample count follows PWM | on-chip | per-PR | 1 | yes | staging, Core2 |
| `getAdcTrigger` | return value | on-chip | per-PR | – | – | – |
| `enableAdcTrigger` | ADC conversions start (sample count on-chip) | on-chip | per-PR | 1 | yes | staging, Core2 |
| `disableAdcTrigger` | ADC conversions stop | on-chip | per-PR | – | – | – |
| `setAdcTriggerInstant` | CMP3/CMP4 register; sample instant vs sawtooth (on-chip, L0-ADD pattern) | on-chip | per-PR | 1 | yes | staging, Core2 |
| `setAdcEdgeTrigger` | ADC trigger edge register (up-down only) | on-chip | per-PR | 2 | yes | Core2 |
| `getAdcEdgeTrigger` | return value | on-chip | per-PR | – | – | – |
| `setAdcDecimation` | ADC rollover / decimation register; samples per period | on-chip | per-PR | – | yes | staging, Core2 |
| `disablePeriodEvnt` | repetition interrupt off: callback count stops | on-chip | per-PR | – | – | – |
| `setPeriodEvntRep` | REPxR register; callback rate | on-chip | per-PR | – | – | – |
| `getPeriodEvntRep` | return value | on-chip | per-PR | – | – | – |
| `configurePeriodEvnt` | callback runs every N periods (count vs uptime, L0-CTK pattern) | on-chip | per-PR | – | – | – |
| `enablePeriodEvnt` | callback starts | on-chip | per-PR | – | – | – |
| `getPeriodUs` | return value | on-chip | per-PR | – | – | – |
| `setFrequency` | frequency on pin; PERxR (Core#165, #172: per-unit, re-init) | scope (MUX) | per-PR | 1 | – | staging |
| `getFrequencyMin` | return value | on-chip | per-PR | – | – | – |
| `getFrequencyMax` | return value | on-chip | per-PR | – | – | – |
| `getResolutionPs` | return value (184 ps at 170 MHz, DLL x32) | on-chip | per-PR | – | – | – |
| `initBurstMode` | BMCR register | on-chip | per-PR | 1 | – | – |
| `setBurstMode` | BMCMPR/BMPER; idle/run period pattern on pin | scope (MUX) | nightly | 1 | – | – |
| `startBurstMode` | burst pattern on pin (count periods) | scope (MUX) | nightly | 1 | – | – |
| `stopBurstMode` | continuous switching resumes | scope (MUX) | nightly | – | – | – |
| `deInitBurstMode` | BMCR cleared | on-chip | per-PR | – | – | – |

#### `spin.data` (18)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `enableAcquisition` | return 0/-1; ADC SQR registers hold the channel | on-chip | per-PR | 2 | yes | staging |
| `start` | return value; DMA + ADC running (sample counts) | on-chip | per-PR | – | yes | – |
| `started` | return value | on-chip | per-PR | – | – | Core2 |
| `stop` | return value; sample counts freeze | on-chip | per-PR | – | – | Core2 |
| `triggerAcquisition` | software trigger: one new sample per call | on-chip | per-PR | 1 | yes | – |
| `getRawValues` | count and values of samples since last call (no loss: L0-ADD-02) | on-chip | per-PR | – | yes | staging |
| `getValues` | converted samples = conversion(raw) | on-chip | per-PR | – | yes | – |
| `peekLatestValue` | latest value without consuming (L0-ADD-03) | on-chip | per-PR | – | – | staging |
| `getLatestValue` | latest value + dataValid flag | on-chip | per-PR | 2 | yes | – |
| `convertValue` | pure conversion of a raw value (host-testable) | on-chip | per-PR | – | yes | – |
| `setConversionParametersLinear` | getValues/convertValue follow gain/offset | on-chip | per-PR | – | yes | – |
| `setConversionParametersNtcThermistor` | convertValue follows the NTC formula | on-chip | per-PR | – | – | – |
| `getConversionParameterValue` | return value | on-chip | per-PR | – | – | – |
| `getConversionParameterType` | return value | on-chip | per-PR | – | – | – |
| `storeConversionParametersInMemory` | NVS entry written; survives reset | on-chip | nightly | – | – | – |
| `retrieveConversionParametersFromMemory` | parameters restored after reset | on-chip | nightly | – | – | – |
| `configureDiscontinuousMode` | ADC CFGR DISCNUM; samples per trigger | on-chip | per-PR | – | yes | staging |
| `configureTriggerSource` | ADC EXTSEL register; sampling follows the source | on-chip | per-PR | 1 | yes | staging, Core2 |

#### `spin.metaData` (15)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `setSpinSerialNumber` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getSpinSerialNumber` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `setShieldSerialNumber` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getShieldSerialNumber` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | – | – |
| `setSpinVersion` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getSpinVersion` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `setShieldVersion` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getShieldVersion` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | – | – |
| `setSpinPassword` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getSpinPassword` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | – | – |
| `setShieldPassword` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getShieldPassword` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `setExtraData` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `getExtraData` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |
| `clearAllMetaData` | return code; value read back, and after a reset (NVS) | on-chip | nightly | – | yes | – |

#### `spin.gpio` (6)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `configurePin` | MODER/PUPDR registers | on-chip | per-PR | 2 | yes | staging |
| `setPin` | pin high (ODR; level on scope) | scope (MUX) | per-PR | – | yes | – |
| `resetPin` | pin low | scope (MUX) | per-PR | – | yes | – |
| `togglePin` | pin toggles (L0-CTK uses it as a marker) | scope (MUX) | per-PR | – | – | staging |
| `writePin` | pin level = value | scope (MUX) | per-PR | – | – | – |
| `readPin` | returns a level driven from outside (MUX injector or self-injection wire) | loopback | per-PR | 2 | yes | – |

#### `spin.led` (3)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `turnOn` | LED pin high (ODR over SWD; L0-TGT uses toggle) | on-chip | per-PR | 15 | – | – |
| `turnOff` | LED pin low | on-chip | per-PR | 17 | – | – |
| `toggle` | LED pin toggles | on-chip | per-PR | 4 | – | staging |

#### `spin.dac` (4)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initConstValue` | DAC MCR/CR registers; pin in analog | on-chip | per-PR | 1 | yes | staging |
| `setConstValue` | DOR register; voltage on pin (L0-DAC pattern, PA6) | scope (MUX) | per-PR | 1 | yes | staging |
| `slopeCompensation` | sawtooth generator registers (STR/STMODR); ramp on pin, synced to HRTIM | scope (MUX) | nightly | – | – | – |
| `currentModeInit` | DAC + comparator chain for current mode (registers) | on-chip | per-PR | – | – | – |

#### `spin.comp` (1)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initialize` | COMPx CSR register; output flips when the injected input crosses the DAC threshold | loopback | nightly | – | – | – |

#### `spin.timer` (2)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `startLogIncrementalEncoder` | TIM3/TIM4 encoder mode registers | on-chip | per-PR | 1 | – | – |
| `getIncrementalEncoderValue` | count follows quadrature edges (needs two driven inputs) | loopback | nightly | 1 | – | – |

#### `spin.uart` (4)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `usart1Init` | LPUART1 registers | on-chip | per-PR | – | – | – |
| `usart1ReadChar` | returns a char sent to RX (TX->RX loopback) | loopback | nightly | – | – | – |
| `usart1WriteChar` | frame on TX pin (scope decode or loopback) | loopback | nightly | – | – | – |
| `usart1SwapRxTx` | SWAP bit; TX appears on the other pin | loopback | nightly | – | – | – |

#### `shield.power` (28)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initMode` | leg unit configured: convention + mode registers | on-chip | per-PR | – | – | – |
| `setDutyCycle` | CMP of the leg unit; duty on leg pins (clamped by min/max) | scope (MUX) | per-PR | 15 | yes | – |
| `setDutyCycleRaw` | CMP register | on-chip | per-PR | – | – | – |
| `start` | leg pins switch (multi-leg start, PR feature/hrtim-dma-sequences) | scope (MUX) | per-PR | 16 | yes | – |
| `stop` | leg pins idle | scope (MUX) | per-PR | 16 | – | – |
| `connectCapacitor` | capacitor GPIO pin level (Twist) | scope (MUX) | per-PR | – | – | – |
| `disconnectCapacitor` | capacitor GPIO pin level | scope (MUX) | per-PR | 2 | – | – |
| `connectDriver` | driver-enable GPIO pin level | scope (MUX) | per-PR | – | – | – |
| `disconnectDriver` | driver-enable GPIO pin level | scope (MUX) | per-PR | – | – | – |
| `setTriggerValue` | ADC trigger compare register of the leg | on-chip | per-PR | 1 | – | – |
| `setPhaseShift` | phase between legs on pins | scope (MUX) | per-PR | 1 | yes | – |
| `setSlopeCompensation` | DAC sawtooth registers for the leg (current mode) | on-chip | nightly | 1 | yes | – |
| `setDeadTime` | DTxR register; ns gap on pins needs direct probe | direct probe | nightly | – | yes | – |
| `setFrequency` | frequency on all legs | scope (MUX) | per-PR | – | – | – |
| `getFrequency` | return value | on-chip | per-PR | – | – | – |
| `getFrequencyMin` | return value | on-chip | per-PR | – | – | – |
| `setDutyCycleMin` | clamp applied: setDutyCycle below min gives min (CMP register) | on-chip | per-PR | – | – | – |
| `setDutyCycleMinRaw` | clamp register value | on-chip | per-PR | – | – | – |
| `setDutyCycleMaxRaw` | clamp register value | on-chip | per-PR | – | – | – |
| `setDutyCycleMax` | clamp applied | on-chip | per-PR | – | – | – |
| `getDutyCycleMax` | return value | on-chip | per-PR | – | – | – |
| `getDutyCycleMaxRaw` | return value | on-chip | per-PR | – | – | – |
| `getDutyCycleMin` | return value | on-chip | per-PR | – | – | – |
| `getDutyCycleMinRaw` | return value | on-chip | per-PR | – | – | – |
| `getPeriod` | return value | on-chip | per-PR | – | – | – |
| `setAdcDecim` | decimation register of the leg | on-chip | per-PR | – | yes | – |
| `initBuck` | leg configured as buck (registers); converter behaviour needs a power stage | on-chip | per-PR | 17 | yes | – |
| `initBoost` | leg configured as boost (registers); converter behaviour needs a power stage | on-chip | per-PR | 5 | yes | – |

#### `shield.sensors` (18)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `enableSensor` | ADC channel of the sensor enabled (registers) | on-chip | per-PR | – | – | – |
| `getRawValues` | samples of the sensor pin (inject a level on the bare Spin) | loopback | per-PR | – | – | – |
| `getValues` | converted samples with the shield gain/offset | loopback | per-PR | – | – | – |
| `peekLatestValue` | latest converted value | loopback | per-PR | – | – | – |
| `getLatestValue` | latest converted value + dataValid | loopback | per-PR | 18 | – | – |
| `convertRawValue` | pure conversion with the stored parameters | on-chip | per-PR | – | – | – |
| `setConversionParametersLinear` | conversion follows gain/offset | on-chip | per-PR | – | – | – |
| `setConversionParametersNtcThermistor` | conversion follows the NTC formula | on-chip | per-PR | – | – | – |
| `retrieveStoredParameterValue` | return value | on-chip | per-PR | – | – | – |
| `retrieveStoredConversionType` | return value | on-chip | per-PR | – | – | – |
| `storeParametersInMemory` | NVS write; survives reset | on-chip | nightly | – | – | – |
| `retrieveParametersFromMemory` | parameters restored after reset | on-chip | nightly | – | – | – |
| `setOwnverterTempMeas` | (Ownverter build) temperature mux GPIOs | build-only | build | 1 | – | – |
| `enableDefaultOwnverterSensors` | (Ownverter build, deprecated alias) channel set | build-only | build | 3 | yes | – |
| `enableDefaultSensors` | default channel set from the devicetree (registers) | on-chip | per-PR | – | yes | Core2 |
| `enableDefaultTwistSensors` | (deprecated alias) same channel set | on-chip | per-PR | 16 | yes | Core2 |
| `setTwistSensorsUserCalibrationFactors` | interactive console calibration; values stored in NVS | power stage | supervised | – | – | – |
| `triggerTwistTempMeas` | temperature mux GPIO levels + software ADC trigger | scope (MUX) | nightly | 3 | – | – |

#### `shield.ngnd` (2)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `turnOn` | NGND switch GPIO level | scope (MUX) | per-PR | – | – | – |
| `turnOff` | NGND switch GPIO level | scope (MUX) | per-PR | – | – | – |

#### `task` (8)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `createCritical` | return 0/-1 per source and range (L0-CTK-02/03) | on-chip | per-PR | 27 | yes | staging, Core2 |
| `startCritical` | task runs at its period (count vs uptime; GPIO marker on scope, L0-CTK-04/05) | scope (MUX) | per-PR | 27 | yes | staging, Core2 |
| `stopCritical` | task count stops; restart possible | on-chip | per-PR | – | – | Core2 |
| `createBackground` | return task number / -1 beyond CONFIG max | on-chip | per-PR | 27 | yes | staging, Core2 |
| `startBackground` | thread runs (tick count, L0-TGT-03) | on-chip | per-PR | 27 | – | staging, Core2 |
| `stopBackground` | tick count stops | on-chip | per-PR | – | – | – |
| `suspendBackgroundMs` | loop period (tick rate vs uptime) | on-chip | per-PR | 27 | – | staging, Core2 |
| `suspendBackgroundUs` | loop period | on-chip | per-PR | 7 | – | – |

#### `communication.analog` (3)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `init` | DAC2 ch1 on PA6 + ADC on PC4 configured (registers) | on-chip | per-PR | 1 | – | staging |
| `getAnalogCommValue` | reads PC4: needs PA6->PC4 loopback on a bare Spin (Twist joins them) | loopback | per-PR | 1 | – | – |
| `setAnalogCommValue` | DAC2 DOR + PA6 voltage (L0-DAC-03/04) | scope (MUX) | per-PR | 1 | – | staging |

#### `communication.can` (15)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `getCanNodeAddr` | return value / value received from another node | second Spin | nightly | – | – | – |
| `setCanNodeAddr` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | – | – | – |
| `getCtrlEnable` | return value / value received from another node | second Spin | nightly | – | – | – |
| `getCtrlReference` | return value / value received from another node | second Spin | nightly | 1 | – | – |
| `getStartStopState` | return value / value received from another node | second Spin | nightly | 1 | – | – |
| `getControlPeriod` | return value / value received from another node | second Spin | nightly | – | – | – |
| `setCtrlEnable` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | 1 | – | – |
| `setCtrlReference` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | 1 | – | – |
| `startSlaveDevice` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | 1 | – | – |
| `stopSlaveDevice` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | 1 | – | – |
| `setControlPeriod` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | – | – | – |
| `getBroadcastEnable` | return value / value received from another node | second Spin | nightly | – | – | – |
| `getBroadcastPeriod` | return value / value received from another node | second Spin | nightly | – | – | – |
| `setBroadcastEnable` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | 1 | – | – |
| `setBroadcastPeriod` | ThingSet CAN frames seen by another node (setters/getters alone: return value on-chip) | second Spin | nightly | – | – | – |

#### `communication.rs485` (5)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `configure` | USART3 + DMA registers | on-chip | per-PR | 3 | – | – |
| `configureCustom` | baud rate register | on-chip | per-PR | – | – | – |
| `startTransmission` | frame on TX; with TX->RX loopback the RX buffer gets it and the user callback runs | loopback | nightly | 3 | – | – |
| `turnOnCommunication` | DE/transceiver enable GPIO | scope (MUX) | nightly | – | – | – |
| `turnOffCommunication` | DE/transceiver enable GPIO | scope (MUX) | nightly | – | – | – |

#### `communication.sync` (2)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initMaster` | sync pulse on PB1 (HRTIM SCOUT) at each master period | scope (MUX) | nightly | 4 | – | – |
| `initSlave` | HRTIM reset on SCIN: phase-locks to another board master | second Spin | nightly | 3 | – | – |

#### `safety` (16)

| Call | What a user can observe | Test method | Suite | Examples | Docs | Tests |
|---|---|---|---|---|---|---|
| `initShield` | watch list + thresholds from defaults (getters) | on-chip | per-PR | – | – | Core2 |
| `initShield(sensors,n)` | watch list = given sensors | on-chip | per-PR | – | – | Core2 |
| `setChannelWatch` | return code; getChannelWatch | on-chip | per-PR | – | – | Core2 |
| `unsetChannelWatch` | return code; getChannelWatch | on-chip | per-PR | – | – | Core2 |
| `getChannelWatch` | return value | on-chip | per-PR | – | – | Core2 |
| `setChannelReaction` | on a fault: outputs open-circuit vs short-circuit on pins | scope (MUX) | nightly | – | – | Core2 |
| `getChannelReaction` | return value | on-chip | per-PR | – | – | Core2 |
| `setChannelThresholdMax` | return code; trip when the injected level crosses it | loopback | nightly | – | – | Core2 |
| `setChannelThresholdMin` | return code; trip when the injected level crosses it | loopback | nightly | – | – | Core2 |
| `getChannelThresholdMax` | return value | on-chip | per-PR | – | – | Core2 |
| `getChannelThresholdMin` | return value | on-chip | per-PR | – | – | Core2 |
| `getChannelError` | true after an injected over-threshold reading | loopback | nightly | – | – | – |
| `enableSafetyApi` | safety_task runs in the critical task; outputs stop on fault | loopback | nightly | – | – | Core2 |
| `disableSafetyApi` | no trip on an over-threshold reading | loopback | nightly | – | – | Core2 |
| `storeThreshold` | NVS write; survives reset | on-chip | nightly | – | – | – |
| `retrieveThreshold` | threshold restored after reset | on-chip | nightly | – | – | Core2 |

#### Drivers (86, C, `public_api/`)

| Driver | Calls | How it is reached, what is observable | Method |
|---|---|---|---|
| `hrtim.h` | 57 | Mostly through PwmHAL/PowerAPI. **No HAL wrapper**: `hrtim_output_hot_swap`, `hrtim_master_cmp_set`, `hrtim_cmpl_pwm_out`, `hrtim_rst_evt_en/dis`, `hrtim_cnt_en/dis`, `DualDAC_init`, `hrtim_get_status`, `hrtim_get_apb2_clock`, `hrtim_period_Master_get(_us)` (the last is used by the Task API, `uninterruptible_synchronous_task.cpp:182`). Registers over SWD; outputs on the scope. | on-chip, through the PWM firmware |
| `adc.h` | 9 | Through DataAPI. `adc_remove_channel` and `adc_get_enabled_channels_count` have no wrapper. Registers and sample counts. | on-chip |
| `dac.h` | 7 (static inline) | Through DacHAL and AnalogCommunication. `dac_function_update_reset/step` are reached by `slopeCompensation`. DOR register, pin voltage. | on-chip + scope |
| `nvs_storage.h` | 6 | Through metaData, conversion parameters and safety thresholds. `get_current_version`, `get_version_in_nvs` and `get_free_space` are direct-only. Read-back after a reset. | on-chip |
| `timer.h` | 4 (static inline) | `timer_config/start/stop/get_count`: the TIM6 critical-task source, TIM3/TIM4 encoder. | on-chip |
| `comparator.h` | 2 | `comparator1_init`, `comparator3_init` through `CompHAL::initialize`. CSR register. | on-chip |
| `ngnd.h` | 1 | `ngnd_set` through `shield.ngnd`. GPIO level. | scope |

Rule proposed for the drivers: **test them through the HAL**, and add direct checks only for the calls with no wrapper that a user can reach (`#include "hrtim.h"` is public). That is about 15 calls, all on-chip register checks in the PWM and Data firmwares.

## 2. Relied-on behaviour that is not a call

These are guarantees users build on that no single call owns. Grill question 1 decides whether they are "the API". This draft says **yes**, because a Zephyr or HAL bump breaks them first.

| # | Behaviour | Source | How to observe | Method |
|---|---|---|---|---|
| R1 | VREFBUF enabled at 2.048 V (scale 0) before the kernel starts (`PRE_KERNEL_1`): every ADC/DAC scale relies on it | `hardware_auto_configuration.cpp:58-64, 215` | VREFBUF CSR over SWD; DAC level × 2.048/4096 on PA6 (L0-DAC-04 already relies on it) | on-chip + scope |
| R2 | DAC2 ch1 initialised at boot (`PRE_KERNEL_2`) with a constant 2048 and an external pin | `hardware_auto_configuration.cpp:76-90, 221` | DAC2 registers right after boot, before `main()` touches anything | on-chip |
| R3 | Console at `APPLICATION` priority 89; MCUboot confirms the image (`_img_validation`); a 1200-baud CDC open reboots into the bootloader | `hardware_auto_configuration.cpp:226-249` | The image stays confirmed after a reset (no rollback). The 1200-baud trick needs the USB host: supervised | on-chip / supervised |
| R4 | O2 shield only: LPUART1 TX/RX swapped at `PRE_KERNEL_1` | `hardware_auto_configuration.cpp:252` | SWAP bit; build-only for the O2 config | build + on-chip |
| R5 | **Critical task with `source_tim6`**: TIM6 ISR with `IRQ_ZERO_LATENCY`, period 1–6553 µs. **No kernel calls are allowed inside it** (zero-latency IRQs are above the kernel's masking) | `uninterruptible_synchronous_task.cpp:134, 148-175`, `stm32_timer_driver.c:102-113`, `TaskAPI.h:78-88` | Period vs uptime and on PA6 (L0-CTK-04/05). The no-kernel-call rule is a usage contract: a negative test (call `printk`, see the hang) is supervised only | on-chip + scope |
| R6 | **Critical task with `source_hrtim`**: the HRTIM master period event, with repetition = period / PWM period. The period must be a multiple of the PWM period, with at most 256 repetitions | `uninterruptible_synchronous_task.cpp:135, 177-210` | L0-CTK-02/03 (return codes), L0-CTK-04/05 (period) | on-chip + scope |
| R7 | In each critical-task run, `safety_task()` runs first (when the Safety API is built in), then the data dispatch (when `startCritical(true)`), then the user task | `uninterruptible_synchronous_task.cpp:107-118` | DWT stamps per stage in a report struct | on-chip |
| R8 | `startCritical(true)` puts data acquisition in "externally triggered" dispatch, and the ADC sees every PWM period without loss | `uninterruptible_synchronous_task.cpp:230-250`; L0-ADD-02 | sample counts vs elapsed periods (L0-ADD-02) | on-chip |
| R9 | Background tasks run at priority 14, at most `CONFIG_OWNTECH_TASK_MAX_ASYNCHRONOUS_TASKS` of them; the safety error thread runs at priority 0 and prints every 200 ms | `asynchronous_tasks.cpp:33-64`, `uninterruptible_synchronous_task.cpp:37-46, 76-104` | `createBackground` returns -1 past the max; thread priority from the kernel's thread list over SWD | on-chip |
| R10 | PWM defaults: 200 kHz, 100 ns dead time on both edges (`TU_DEFAULT_FREQ`, `TU_DEFAULT_DT`), period 27200 at 184 ps | `hrtim.h:36-38`, `hrtim.c:123, 559-561` | registers right after `initUnit`; frequency on pin | on-chip + scope |
| R11 | Default sensor conversion parameters come from the shield devicetree; NVS-stored ones override them after `retrieve…` | `Sensors.h`, `DataAPI.h` | conversion parameter getters before/after store+reset | on-chip |
| R12 | NVS content (metaData, conversion parameters, thresholds) survives a reset and a firmware update; `nvs_storage_get_version_in_nvs` tags it | `nvs_storage.h` | write, reflash, read back | on-chip (nightly: costs a reflash) |
| R13 | ThingSet CAN starts at `SYS_INIT(APPLICATION)` without a user call (twist builds) | `thingset_can.c:98` | CAN controller registers after boot; frames need a second node | on-chip / second Spin |
| R14 | Shield selection (none / twist / ownverter / O2) through Kconfig and devicetree: every config must build | `zephyr/CMakeLists.txt`, `examples.yml` | build | build-only |

## 3. Sources of tests

### 3.1 Staging `tests/` (catalogue `framework-3`, 50 entries)

| Suite | Firmware | Calls exercised | What is actually checked |
|---|---|---|---|
| L0-FWK | none | none | framework self-test |
| L0-CAL | none (CUT on Core-CUT drives PA8) | none | scope trim, gain, skew |
| L0-TGT (`target_smoke`) | `tests/firmware/smoke` | `task.createBackground/startBackground/suspendBackgroundMs`, `spin.led.toggle` | build, flash, SWD read; ≥ 50 background ticks in 2 s; the users' `STLink` upload |
| L0-CTK (`target_critical_task`) | `critical_task` | `task.createCritical/startCritical/createBackground/startBackground/suspendBackgroundMs`, `spin.pwm.initUnit/setFrequency`, `spin.gpio.configurePin/togglePin` | createCritical range per source (Core#174); period on-chip and on PA6 |
| L0-DAC (`target_dac`) | `dac` | `communication.analog.init/setAnalogCommValue`, task background calls | DAC2 DOR and PA6 voltage at 5 levels (Core#168) |
| L0-ADD (`target_data_dispatch`) | `data_dispatch` | `spin.data.enableAcquisition/configureTriggerSource/configureDiscontinuousMode/getRawValues/peekLatestValue`, `spin.pwm.initUnit/setFrequency/setDutyCycle/startDualOutput/setAdcTrigger/setAdcTriggerInstant/setAdcDecimation/enableAdcTrigger`, `spin.dac.initConstValue/setConstValue`, task calls | no lost samples, peek follows, no overrun, heap-short behaviour (Core#161/162/173/175) |

**25 distinct calls exercised**, all as set-up for one bug's check. Sources: `tests/catalogue.json`, `tests/firmware/*/main.cpp`, `tests/firmware/pio_tests.ini`.

**Reusable patterns** for the regression suite:
- **Host-commanded steps through SWD.** CTK sets periods from the host.
- **A report struct with a magic number and layout**, read without halting (L0-TGT-02).
- **On-chip counts against uptime** (CTK-04, ADD-02).
- **A GPIO marker on PA6 through the MUX** (CTK-05).
- **A DAC sawtooth as a stimulus** that makes every sample unique (ADD-03).

These are the building blocks of every on-chip check proposed below.

### 3.2 Core2 `tests/` (luizvilla/Core `safety-hw`, 348f0c2)

There are 93 catalogued checks in 14 families. They test a **redesigned Safety API**, not staging's:
- they use `getFault`, `clearFault`, `disarm`, `setTripThreshold`, `setSafeState`, `getFaultDescription`, `isDevelopmentMode` and `rebootRequired`, which staging does not have (`git show github-Luiz/safety-hw:zephyr/modules/owntech_safety_api/zephyr/public_api/SafetyAPI.h`);
- they also call Core2-only DataAPI calls: `spin.data.peekAdcRaw`, `getAdcChannelRank`, `peekAdcChannel`.

So they **do not port as-is** to staging's API (as #3 found for the framework). Several **families carry over as methods for PWM and Data**:

| Core2 family | Staging-API calls it exercises | Reusable for |
|---|---|---|
| L0-LAT (`spoc_dead`, `spoc_latency`) | `spin.pwm.setDeadTime`, `initUnit`, `startDualOutput`, `setDutyCycle` | dead time on direct probes (100.4/100.9 ns measured; #5) → `setDeadTime`, `shield.power.setDeadTime` |
| L0-ADC (`spoc_adc`) | `spin.data.configureTriggerSource`, `spin.pwm.setAdcTrigger/setAdcTriggerInstant/setAdcEdgeTrigger/enableAdcTrigger` | ADC accuracy against an injected level (CUT DAC or MUX injector) → `getLatestValue`, conversion, `shield.sensors.*` |
| L0-TOV (`safety_timing`) | `task.createCritical/startCritical/stopCritical` | task-start jitter, CPU cost, interrupt-off time → R5–R7 |
| L0-EDG-07 | `spin.data.started/stop` | PWM before data acquisition |
| L0-ALC (`safety_allocation`, host Python + build) | none (devicetree) | the "build-only + host" pattern: protection tables checked at build time |
| L0-SUP, L0-CMP, L0-AWD, L0-SST, L0-STK, L0-TRP, L0-CPU, L0-API, L0-EDG | Core2 Safety API | the Safety module of this suite **once Core2's API lands on staging** (it waits on the Zephyr bump, see #35's decision). Until then, staging's 16-call Safety API gets its own small set |

Core2 also has the `tests/common/shim/` headers (`arm_math.h`, `zephyr/logging/log.h`). They compile Core code on the host. That supports **host unit tests** for pure code such as `data_conversion.cpp`.

### 3.3 Core docs (`docs/*.md`, 15 files)

Code snippets in `spin_dataAPI.md` (26 distinct calls), `spin_pwm.md` (16), `spin_metaDataAPI.md` (12), `shield_introduction.md` (9), `shield_sensors.md` (5), `spin_gpio.md` (4), `spin_dac.md` (3) and `task_introduction.md` (3) name 66 distinct call strings.

`powerAPI.md`, `communication_introduction.md` and `safety_introduction.md` show **no call snippet**.

**8 snippet names do not exist** in the API; see [Surprises](#surprises). The docs are a source of *intended* behaviour (e.g. `spin_dataAPI.md` on acquisition order and conversion), not of tests.

### 3.4 owntech-foundation/examples (`main` 345c8a0, 2026-05-14, 27 apps)

The local `~/code/examples` checkout is on a feature branch. The `OTF-github/main` ref there equals upstream `main` (`gh api repos/owntech-foundation/examples/commits/main`).

Calls per example family (count of apps using each call is in the tables of §1):

| Family | Apps | Calls used |
|---|---|---|
| `SPIN/` (ADC, DAC, LED, PWM, TIMER) | 9 | `spin.pwm.*` (17 distinct, incl. burst, phase shift, ADC trigger), `spin.data.enableAcquisition/configureTriggerSource/triggerAcquisition/getLatestValue`, `spin.dac.*`, `spin.led.*`, `spin.timer.*`, `spin.gpio.configurePin/readPin` |
| `TWIST/DC_DC`, `DC_AC`, `Microgrid` | 13 | `shield.power.initBuck/initBoost/setDutyCycle/start/stop/setPhaseShift/setSlopeCompensation/setTriggerValue/disconnectCapacitor`, `shield.sensors.enableDefaultTwistSensors/getLatestValue/triggerTwistTempMeas`, `communication.sync.*`, `communication.analog.*`, task calls |
| `TWIST/Communication` | 3 | `communication.can.*` (7 of 15), `communication.rs485.configure/startTransmission` |
| `OWNVERTER/` | 2 | `shield.sensors.enableDefaultOwnverterSensors/setOwnverterTempMeas`, power, task |

**64 of 196 calls are used by at least one example; 132 by none.** No example uses:
- Safety (0 of 16);
- MetaData (0 of 15);
- UART (0 of 4);
- comparator (0 of 1);
- NGND (0 of 2);
- 29 of 46 PwmHAL calls;
- 14 of 18 DataAPI calls;
- 20 of 28 PowerAPI calls;
- 13 of 18 Sensors calls.

The **build tier already exists**:
- `.github/workflows/examples.yml` runs `tools/build_examples.sh`, which builds every example against the PR (env USB).
- Run 45796802919: 27/27 PASS in 5 min 49 s on a hosted runner.
- The first build takes 103 s. After that, each example takes **3–4 s**, or **21–24 s** when the shield changes.

## 4. Testability per call and group

### 4.1 What the bench can see (#5 resolution, #16 open)

- **On-chip**: SWD read without halting, of a report struct (magic + layout) or of peripheral registers. This holds for any register-level state, return values, counts against uptime, and DWT time stamps at 5.9 ns (#5 findings, "ADC dispatch is judged on-chip").
- **Scope through the MUX**: three CD74HC4067 per channel. Every CUT pin is reachable (each pin is loaded by four mux inputs, one per channel). The edges take ~20–35 ns (estimated), so this path gives **presence, frequency, duty, levels and patterns, for pulses of about 200 ns and longer**. With both injection wires in, the MUX gives **two independent nodes, not four** (#5).
- **Direct probe**: needed for ns timing (dead time, low duty near 0/1, 184 ps steps, multi-leg alignment). Permanent probes on two legs are **#16, still open**, so direct-probe checks are blocked for unattended campaigns today.
- **Injection / loopback**: TP1–TP3 joins CH1 and CH2. The CUT DAC can then feed another CUT pin, or two CUT pins routed to CH1 and CH2 are wired together. TP5–TP7 joins CH3 and CH4 to the MUX injector (#5).
  - **Assumption**: routing two CUT pins to CH1 and CH2 with TP1–TP3 in gives a usable pin-to-pin loopback. This covers UART TX→RX, PA6→PC4 for the analog bus, and GPIO out→in. It would be slow edges through 2 × 3 mux switches, fine at kHz to low-MHz.
  - #5 warns "never route two different CUT outputs to CH1 and CH2 while the wire is in". A loopback must route **one output and one input**.
  - Switchable wires (#16) make this a runner-controlled state.
- **Second Spin**: the MUX Spin runs pinned SPOC_Software firmware (#15) and has no CAN/RS485 link to the CUT. CAN and HRTIM sync slave need a **second CUT-like Spin**, with transceivers for CAN (a Twist or a CAN breakout). Nothing on the bench provides one today.
- **Power stage**: CUT is a bare Spin; the test envs refuse power boards (`pio_tests.ini` header).

### 4.2 Split (primary method per call, 196 user-facing calls)

| Method | Spin | Shield | Task | Comm | Safety | Total |
|---|---|---|---|---|---|---|
| on-chip | 73 | 28 | 7 | 3 | 10 | **121** |
| scope (MUX) | 19 | 12 | 1 | 4 | 1 | **37** |
| direct probe (#16) | 1 | 1 | 0 | 0 | 0 | **2** |
| loopback / injection | 6 | 4 | 0 | 2 | 5 | **17** |
| second Spin | 0 | 0 | 0 | 16 | 0 | **16** |
| power stage | 0 | 1 | 0 | 0 | 0 | **1** |
| build-only | 0 | 2 | 0 | 0 | 0 | **2** |

**Reading it:**
- "Primary" means the cheapest method that shows the call works. Many on-chip calls also get a scope or probe check at deeper levels (grill question 2):
  - `setDutyCycle` is a register check per-PR, plus a low-duty sweep on a direct probe nightly (#8);
  - `setDeadTime` is a register check per-PR, plus ns on the probe.
- **The 2 build-only calls** are Ownverter-only sensors calls. On a bare Spin they can be compiled but not run, unless a second test firmware is built with `SHIELD=ownverter`, which drives Spin pins too. **Assumption**: they are runnable on a bare Spin like the Twist ones, so build-only is a floor, not a ceiling.
- **Power-stage depth** is wider than the single call: `initBuck`/`initBoost` are register-checked on-chip, but "the converter regulates" is an application result, not an API check. Grill question 4.

### 4.3 Per group

| Group | Calls | Testable on a bare Spin today? | Notes |
|---|---|---|---|
| PwmHAL getters / config registers | ~30 | yes, on-chip | cheap, many checks per flash |
| PwmHAL outputs (start/stop, duty, frequency, phase, convention) | ~10 | yes, scope (2 nodes today, 4 with #16) | frequency and duty through the MUX are steady to 0.2 Hz / 1e-4 (L0-CAL, #5) |
| PwmHAL ns timing (dead time, low duty, 184 ps, alignment) | 2 + depth | **blocked on #16** | L0-LAT on Core2 did it with probes held by hand |
| Burst mode | 5 | scope | #5: "suited once the wires are switchable" |
| DataAPI | 18 | yes, on-chip (+ DAC stimulus) | L0-ADD is the template |
| Data accuracy (conversion vs real volts) | depth | injection (CUT DAC via TP1–TP3, or MUX injector) | Core2 L0-ADC is the template |
| MetaData, NVS, store/retrieve | 15 + 6 + 2 + 2 | yes, on-chip, needs resets | flash wear: nightly, and restore the CUT's NVS state after |
| GPIO / LED / NGND | 11 | yes: ODR on-chip, level on scope; `readPin` needs injection | |
| DAC HAL | 4 | yes: DOR on-chip, level on scope; `slopeCompensation` ramp on scope | |
| Comparator | 1 | injection | output flips across the DAC threshold |
| Encoder | 2 | **needs two driven inputs**: both injection wires as two loopbacks, or a second Spin | registers alone per-PR |
| UART | 4 | loopback | |
| Task | 8 | yes (CTK/TGT templates) | |
| Shield power (twist on bare Spin) | 28 | yes: legs are HRTIM pins, capacitor/driver are GPIOs | **Assumption**: building `SHIELD=twist` and running it on the bare CUT is safe (L0-DAC already does: env DAC "shield config twist… safe on a bare Spin") |
| Shield sensors | 18 | injection on the sensor's ADC pin | readings depend on the injected level, not a real sensor |
| Analog comm | 3 | PA6 on scope; `getAnalogCommValue` reads PC4 → loopback | |
| RS485 | 5 | registers on-chip; loopback at reduced baud (`configureCustom`) | **Assumption**: 10 Mbit/s default is too fast for the MUX path's edges |
| CAN | 15 | getters/setters return on-chip; frames need a second node | |
| Sync | 2 | master pulse on PB1 on scope; slave needs a second Spin | |
| Safety (staging API) | 16 | getters on-chip; trips by injecting a level above threshold | |

## 5. Cost

### 5.1 Measured durations

| Item | Duration | Source |
|---|---|---|
| Build + flash one test firmware (full Zephyr build, ~317 compile units) | **53.5–56.2 s** (SMOKE 53.5, CTK 54.3, DAC 56.1) | `[SUCCESS] Took` in `tests/out/2026-11-26_f9dc7b2_012241_fTVs1g/*_flash.log` (Core-staging-runner) |
| Users' `STLink` env build + upload (L0-TGT-05) | 59–70 s | same campaign, `target_smoke_stlink_upload.log` |
| L0-CAL (8 checks, 4 channels) | 108–111 s | 12 campaigns, 2026-11-26 00:38–01:55 |
| Checks after the flash | SMOKE ~11 s, DAC 25–27 s (5 levels × 5 scope readings), CTK ~30 s, ADD 34–35 s (two boots × 4 s windows) | file mtimes in the same campaigns |
| Full PR campaign on the runner (FWK, CAL, SMOKE, CTK, DAC, ADD, STLink: 5 builds) | **9.6 min** (Campaign step 09:29:01 → 09:38:39) | run 45792204255 |
| Runner job overhead (bench to rest before and after, checkout) | ~80 s | same run, steps |
| Example builds on a hosted runner, incremental | 3–4 s per app, 21–24 s on a shield change, 103 s for the first | run 45796802919 |

**Takeaway:** the ~55 s per firmware is almost all **build**, not flash. Every env has its own `.pio/build/<env>` and the runner starts from a fresh merge. An incremental app build costs 3–24 s (examples run), so the flash itself is a few seconds.

### 5.2 Grouping: one firmware per module, many checks per flash

Proposed test firmwares, each with a host-commanded step mailbox (the CTK pattern) and one report struct:

| Firmware | Config | Covers | Checks (est.) | Check time (est.) | Tier |
|---|---|---|---|---|---|
| `REG-PWM` | bare | `spin.pwm` 46 + no-wrapper hrtim calls; R10 | ~70 on-chip, ~12 scope | ~90 s | per-PR |
| `REG-DATA` | bare | `spin.data` 18 + adc driver; R8 (extends ADD) | ~40 on-chip | ~60 s | per-PR |
| `REG-TASK` | bare | `task` 8; R5–R7, R9 (extends CTK) | ~20 on-chip, 2 scope | ~40 s | per-PR |
| `REG-IO` | bare | gpio, led, dac HAL, comp/timer/uart registers | ~25 on-chip, ~8 scope | ~45 s | per-PR |
| `REG-TWIST` | twist on bare Spin | `shield.power` 28, `shield.sensors` (registers + injected level), `shield.ngnd`, `communication.analog`, staging `safety` getters/setters | ~60 on-chip, ~10 scope | ~90 s | per-PR |
| `REG-NVS` | twist | metaData 15, store/retrieve (data, sensors, safety), R11–R12; reset cycles | ~35 | ~60 s | nightly |
| `REG-LOOP` | bare/twist | `readPin`, UART, encoder, comparator, RS485 loopback, `getAnalogCommValue`, safety trips by injection | ~25 | ~90 s | nightly (needs #16 switchable wires) |
| `REG-PWM-DEEP` | bare | dead time, low-duty sweep (#8), burst patterns, variable frequency, slope compensation, ns phase | ~40 scope/probe | ~300 s | nightly (probe items need #16) |
| `REG-OWNVERTER` | ownverter | build + register checks of the Ownverter-only paths | ~10 | ~30 s | nightly |
| soak | bare | critical task + dispatch 10 min, jitter and no lost sample (Core2 L0-TOV style) | ~5 | 600 s | nightly |

The bug-fix checks (CTK, DAC, ADD) fold into `REG-TASK`, `REG-TWIST` and `REG-DATA` as named sub-checks. They keep their IDs, so their history continues.

### 5.3 Campaign length estimates

**Per-PR tier**:

| Step | Duration |
|---|---|
| Job overhead | 80 s |
| L0-FWK | 2 s |
| L0-CAL | 110 s |
| SMOKE (55 + 11) | 66 s |
| STLink | 65 s |
| 5 module firmwares × 55 s | 275 s |
| Checks (90 + 60 + 40 + 45 + 90) | 325 s |
| **Total** | **≈ 920 s ≈ 15 min**, so **15–20 min** with margin |

Today's PR campaign takes 11.6 min for 4 fix tests. The per-PR suite is ~1.5× that, for about 196-call breadth instead of 25.

**Nightly tier, per ref:**

| Step | Duration |
|---|---|
| Per-PR tier | 15 min |
| `REG-NVS` (55 + 60) | 2 min |
| `REG-LOOP` (55 + 90) | 2.5 min |
| `REG-PWM-DEEP` (55 + 300) | 6 min |
| `REG-OWNVERTER` (55 + 30) | 1.5 min |
| Soak | 10 min |
| Margin | 3 min |
| **Total** | **≈ 40 min** |

Per #35's decision, nightly runs on `main` **and** on the `pre-zephyr-4.4` tag, so **≈ 80 min**. On a failure, bisection costs about one module firmware per step (≈ 2–3 min), times log₂(commits since the last good night). Bisect only the failing module.

**Supervised tier**: power stage (calibration, buck/boost on a low-voltage setup), second-Spin (CAN 15 calls, sync slave), USB DFU 1200-baud path (R3). By hand, before a graduation. **Not estimated**: it depends on #16, on a second Spin and on grill question 4.

**Build tier**: examples (≈ 6 min hosted, already running), plus two additions:
- an **API compile test**: one translation unit that calls every public call in the `none`, `twist` and `ownverter` configs, about 1–2 min with a warm cache;
- a **docs-snippet compile** check, which would have caught the 8 stale names in §Surprises.

### 5.4 Two cost levers to grill

1. **One "regression firmware" per shield config instead of one per module.**
   - Compile every module's test steps into one binary; the host selects the module through the SWD mailbox.
   - Isolate modules with an SWD reset (~1–2 s) instead of a reflash (~55 s).
   - Per-PR flashes drop from 5 to 2 (bare and twist), saving ~3 min. The per-PR tier would be **≈ 10–12 min**.
   - Risk: one module's test code can affect another's (heap, ISRs, Kconfig like `CONFIG_OWNTECH_UART_API`, the Safety API in the critical task). Reset-between-modules mitigates the runtime state, not the build config.
2. **Keep the build cache between campaigns.** The runner rebuilds every env from scratch. Keeping `.pio/build/<env>` between runs (keyed by the merged SHA's Zephyr/HAL hashes, like `examples.yml` caches `~/.platformio`) would cut each 55 s build to ~5–25 s.
   - **Assumption**: the runner's workspace layout allows a persistent `.pio` without leaking state between PRs. #12 territory.

## 6. Draft coverage matrix

**Draft for grilling.** Suite codes:
- **B**: build tier (hosted, every firmware PR);
- **PR**: per-PR bench tier;
- **N**: nightly on `main` + `pre-zephyr-4.4`;
- **S**: supervised.

The full per-call matrix is in §1's tables (method + suite per call). Compact form by group:

| Call / group | n | B | PR (method) | N (method) | S |
|---|---|---|---|---|---|
| All 196 calls compile in none/twist/ownverter | 196 | ✔ API compile test + examples | | | |
| `spin.pwm` config/getters/ADC trigger/period event | 36 | ✔ | on-chip registers + returns | | |
| `spin.pwm` outputs, frequency, phase, convention | 8 | ✔ | scope (MUX), 2 nodes | ns phase on probe (#16) | |
| `spin.pwm` dead time, low duty (#8), 184 ps | 2 + depth | ✔ | DTxR/CMP registers | direct probe (#16) | until #16 |
| `spin.pwm` burst, variable frequency | 5 | ✔ | BMCR registers | scope patterns | |
| `spin.data` acquisition, dispatch, conversion | 16 | ✔ | on-chip (ADD pattern) | accuracy vs injected level | |
| `spin.data` store/retrieve | 2 | ✔ | | on-chip + reset | |
| `spin.metaData` | 15 | ✔ | | on-chip + reset | |
| `spin.gpio` out, `spin.led` | 8 | ✔ | ODR on-chip + scope level | | |
| `spin.gpio.readPin` | 1 | ✔ | | loopback (#16) | |
| `spin.dac` | 4 | ✔ | DOR + PA6 level | slope ramp on scope | |
| `spin.comp`, `spin.timer` encoder count, `spin.uart` I/O | 7 | ✔ | registers only | loopback / injection (#16) | |
| `task` | 8 | ✔ | on-chip + PA6 marker (CTK/TGT) | 10-min soak, jitter | no-kernel-call misuse (R5) |
| `shield.power` (twist on bare Spin) | 28 | ✔ | registers + leg pins on scope | dead time on probe | buck/boost on a power stage |
| `shield.sensors` | 16 | ✔ | registers, conversion | injected level, store/retrieve + reset | real sensors, `setTwistSensorsUserCalibrationFactors` |
| `shield.sensors` Ownverter-only | 2 | ✔ | | Ownverter build on bare Spin (assumption) | |
| `shield.ngnd` | 2 | ✔ | GPIO level | | |
| `communication.analog` | 3 | ✔ | DOR + PA6 (L0-DAC) | `getAnalogCommValue` via PA6→PC4 loopback | |
| `communication.rs485` | 5 | ✔ | USART/DMA registers | loopback at reduced baud | full 10–20 Mbit/s with transceivers |
| `communication.can` | 15 | ✔ | getter/setter returns | | second Spin + transceivers |
| `communication.sync` | 2 | ✔ | | master pulse on PB1 (scope) | slave lock with a second Spin |
| `safety` (staging API) | 16 | ✔ | getters/setters on-chip | trips by injection, reactions on pins, store/retrieve | |
| Relied-on R1–R14 | 14 | R4, R14 | R1, R2, R5–R10 | R11–R13 (on-chip part) | R3 (USB), R13 (frames) |
| Drivers with no wrapper | ~15 | ✔ | on-chip in REG-PWM / REG-DATA | | |

## 7. Build tickets this spawns

Draft, in order, PWM first per #35's decision:
1. `REG-PWM` (per-PR part), catalogued as `L0-PWM-*`. It depends on #16 only for its nightly probe items.
2. `REG-DATA` (absorbs L0-ADD) and `REG-TASK` (absorbs L0-CTK).
3. `REG-TWIST`: Shield power, sensors and NGND, analog comm (absorbs L0-DAC), staging Safety getters.
4. `REG-IO`.
5. The build tier: an API compile test + a docs-snippet compile (hosted).
6. The nightly runner: a schedule, two refs, a module-level bisection, `REG-NVS`, `REG-LOOP` (needs #16), `REG-PWM-DEEP` (needs #16), soak.
7. Supervised: the second-Spin rig (CAN, sync) and the power-stage procedure (grill question 4; #17 calibration).
8. A coverage check (grill question 5): a script that lists every public call in the headers and fails when one has no matrix row.

## Surprises

1. **Docs name calls that do not exist.** These snippets would not compile:
   - `shield.sensors.enableAcquisition` (`shield_sensors.md:17`);
   - `spin.pwm.setFixedFrequency` (`spin_pwm.md:135, 144`);
   - `spin.pwm.setDeadtime` (lowercase t, `spin_pwm.md:230-252`);
   - `spin.dac.initConsValue` (`spin_dac.md:7`);
   - `spin.adc.enableAcquisition` and `spin.adc.enableChannel` (`spin_dataAPI.md:140-152`);
   - `data.getLatest` and `spin.data.getLatest` (`spin_dataAPI.md:192, 453`);
   - `spin.data.setParameters` (`spin_dataAPI.md:464`).
2. **Copy-paste bug in the safety error thread.** `thread_error` prints `getChannelError(I2_LOW)` for "problem with V2" (`uninterruptible_synchronous_task.cpp:93-94`). V2's flag is never shown. It is observable on the console only.
3. **The analog bus reads a different pin from the one it drives.** `AnalogCommunication.cpp:45` sets PC4 as the analog input, while the DAC drives PA6 (DAC2 ch1, L0-DAC). The Twist joins them. A bare Spin needs a PA6→PC4 loopback to test `getAnalogCommValue`.
4. **132 of 196 calls have no example**, including the entire Safety API, MetaData and UART. Example builds (the build tier) therefore do not even compile-check them in context.
5. **Core2's Safety tests target a different API.** They test 23 safety calls, of which 8 do not exist on staging. They also use 3 Core2-only DataAPI calls. So "port Core2's tests" for Safety means "after Core2's API lands", not now.
6. **Calls with no HAL wrapper.** About 15 hrtim/adc/nvs driver calls are public (`public_api/*.h` is on the include path), but neither the HALs nor any test reaches them, e.g. `hrtim_output_hot_swap`, `hrtim_master_cmp_set` and `adc_remove_channel`. Whether they are "the API" is grill question 1.
7. **The build tier already exists**: `examples.yml` (PR #36), 27/27 builds in under 6 min. The examples' incremental builds take 3 s, against the bench's 55 s per test firmware. Build caching on the bench runner is the cheapest speed-up available.
8. **No `version` API in Spin.** The only version information is `metaData` (user-written NVS) and `nvs_storage` versions. A Core version a test could assert does not exist. `pre_version.py` runs at build time only. **Assumption**: no runtime symbol carries it.

## Sources

- Staging `main` `ec6461e9`:
  - `zephyr/modules/owntech_*/zephyr/{public_api,src}/*.h|*.cpp|*.c`;
  - `tests/` (`catalogue.json`, `README.md`, `firmware/*`, `firmware/pio_tests.ini`);
  - `docs/*.md`;
  - `.github/workflows/{examples,spoc}.yml`;
  - `tools/build_examples.sh`.
- Core2 `github-Luiz/safety-hw` `348f0c2`: `tests/catalogue.json`, `tests/firmware/*/main.cpp`, `zephyr/modules/owntech_safety_api/zephyr/public_api/SafetyAPI.h`.
- owntech-foundation/examples `main` `345c8a0` (= `OTF-github/main` in `~/code/examples`).
- Tickets: Core-staging #3, #5 (resolution comment and `research/spoc-bench-suitability.md` on branch `research/spoc-bench-suitability`), #8, #13, #16, #35 (decision comment 2026-11-26).
- Campaigns: `~/code/Core-staging-runner/tests/out/2026-11-2*` (record.md, `*_flash.log`, mtimes); runs 45792204255 (SPOC bench) and 45796802919 (Build examples) via `gh run view` / `gh api`.
- Extraction scripts (not committed): a header parser for public members, plus greps of `(spin|shield|task|communication|safety).x.y(` over examples, docs and test firmware.
