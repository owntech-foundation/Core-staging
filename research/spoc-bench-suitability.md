# Is the SPOC bench suited to PWM and timing tests?

Research for [Core-staging#5](https://github.com/owntech-foundation/Core-staging/issues/5). Date: 2026-11-25.
Facts are cited to primary sources: the Rigol MHO900 datasheet and programming guide, TI's CD74HC4067 datasheet, ST's DS12288, the SPOC hardware repo, and Core2's bench notes. Anything marked **unverified** was not confirmed in a primary source. **Estimate** marks a number derived here, not measured.

## Short answer

**Keep the bench, but use it in two modes.** The MUX path (CUT pin → CD74HC4067 → 9 MΩ divider → BNC) is a **presence, frequency, duty and pattern** path. Its edges are tens of ns slow, so it can see pulses of about ≥ 200 ns. It cannot resolve edge position below about 5 ns. Its channels are only "4" while both injection wires are out. Anything at the HRTIM's resolution goes on **direct probes** at the CUT pins: dead time, low duty near 0 and 1, the 184 ps steps, and multi-leg alignment. L0-LAT already does this. The MHO984 itself is good enough for every family: 800 MHz, 4/2/1 GSa/s, 25–100 Mpts, ±1.5 ppm timebase. **ADC dispatch timing** should be judged on-chip, with the DWT cycle counter, sample-coded stimuli and stress injection. The scope only checks it.

Before the Raspberry Pi bench, make five changes, in priority order:
1. Make the two injection wires switchable.
2. Fit the direct probes permanently.
3. Move the scope to LAN and add deep-memory reads.
4. Add the PLA2216 logic probe.
5. Add an on-chip capture service for sequence checks.

## 1. Facts that drive the answer

### Bench as it is (Core2 `docs/safety/spoc_bench.md`, ADR 0009)
- CUT Spin and MUX Spin, both STM32G474. Rigol MHO984 on `/dev/usbtmc0`. ST-Link on the CUT. 30 cm BNC cables: with 1 m cables the scope's own measurements missed edges (spoc_bench.md, 2026-11-19).
- **Two hand-soldered wires short channel pairs:**
  - TP1–TP3 joins CH1 and CH2 for self-injection. "Never route two different CUT outputs to CH1 and CH2 while the wire is in."
  - TP5–TP7 joins CH3 and CH4 for the injector. "CH4 then cannot watch a PWM pin; latency checks use a direct probe."
  - **With both wires in, the MUX gives two independent observation nodes, not four** (spoc_bench.md, decisions 2026-11-20; ADR 0009 wiring table).
- L0-CAL measured the MUX path: channel skew CH3/CH4 lead CH1 by ~4 ns, and CH2 lags by < 1 ns. Gains are 0.990–1.000. The scope's RRDelay read invalid and FFDelay a constant −0.2 ns, so skew is taken from waveforms (spoc_bench.md, L0-CAL).
- The injector's GPIO step at TP5 (mux side, no mux switch in the path) showed a **~20 ns rise** on CH3 through the divider (spoc_bench.md, C5/C6).
- Direct-probe timing already works: L0-LAT measured dead time 100.4 / 100.9 ns and comparator trip latency 256.6–312.1 ns over 295 trips. One run was BLOCKED because the probes were not on the pins (spoc_bench.md, 2026-11-23).
- On-chip timing already exists: L0-TOV and L0-SUP use `DWT->CYCCNT` (Core2 `tests/l0/safety_timing/README.md`, `tests/l0/safety_supervision/README.md`).
- The scope driver reads only `:WAVeform:MODE NORMal` (screen data, ≤ 1000 points) over usbtmc. A ~130 kB screenshot already puts the kernel usbtmc driver out of step. A `VisaTransport` (pyvisa) path exists but is unused (Core2 `tests/common/rigol_mho.py`).

### The MUX path ([SPOC_Hardware README](https://github.com/owntech-foundation/SPOC_Hardware), `KiCad/mux_channel.kicad_sch`; [TI CD74HC4067 datasheet SCHS209D](https://www.ti.com/lit/ds/symlink/cd74hc4067.pdf), copy in `SPOC_Hardware/Datasheets`)
- Each scope channel is built from **three CD74HC4067 16:1 muxes on one shared output node**, one enabled at a time. The node then goes to a test point and through a 9 MΩ (3 × 3 MΩ) divider with a trimmer into the scope's 1 MΩ. The muxes are powered from **3.3 V** (schematic power symbols).
- TI specifies the CD74HC4067 at 2, 4.5 and 6 V, **not at 3.3 V**:
  - RON: 70 Ω typ, 160 Ω max at 4.5 V, 25 °C.
  - In-to-out propagation: 15 ns max at 4.5 V and 75 ns max at 2 V (CL 50 pF).
  - −3 dB bandwidth: 89 MHz at 4.5 V.
  - CI: 5 pF per input. CCOM: 50 pF per common node.
- **Estimate:**
  - The node carries 3 × 50 pF of common capacitance, plus the trimmer and traces: ≥ 150 pF.
  - Behind ≥ 70–100 Ω RON at 3.3 V, τ ≈ 10–15 ns. That gives a 10–90 % rise of **≈ 20–35 ns**, an effective bandwidth of ≈ 10–20 MHz, and a delay of 15–40 ns that depends on the signal level.
  - This matches the ~20 ns seen at TP5 without a switch, and the ADR's "tens of ns". To be measured: see change 2.
- **Every CUT pin is loaded by four mux inputs** (one per channel), whether routed or not: 4 × 5 pF = 20 pF plus traces. ST's very-high-speed GPIO gives Tr/Tf ≤ 3.3 ns at 30 pF ([DS12288 Rev 5](https://www.st.com/resource/en/datasheet/stm32g474re.pdf), Table 56). The pin edge itself stays fast, so a direct probe on the pin sees the real edge.

### Rigol MHO984 ([MHO900 datasheet DSA46102-1110](https://www.rigol.com/dam/global/downloads/brochures/en/data-sheet/oscilloscopes/MHO900-DataSheet.pdf); [MHO900 programming guide](https://www.lambdaphoto.co.uk/pdfs/Rigol/LAMBDA_MHO900_PG.pdf), distributor copy of Rigol's guide; [product page](https://www.rigol.com/intl/products/oscilloscope/MHO900.html))
- **Bandwidth:**
  - 800 MHz with one or two channels on (50 Ω), 400 MHz with three or four.
  - 500 MHz / 400 MHz at 1 MΩ, which is what the SPOC dividers and passive probes use.
  - Rise time 437 ps.
- **Sample rate:** 4 GSa/s with one channel, 2 GSa/s with two, 1 GSa/s with three or four. "Half-channel" means any two channels on. "All-channel" means three or four on.
- **Memory:** standard depth is 100 / 50 / 25 Mpts for one, two, and three or four channels. With the MHO900-RLU-05 option it is 500 / 250 / 125 Mpts.
- **Timing accuracy and resolution:**
  - Timebase accuracy is ±1.5 ppm ±1 ppm/year.
  - Delta-time accuracy is ±(timebase × readout) ± (0.001 × screen width) ± 20 ps.
  - Channel-to-channel delay is ≤ 200 ps typ.
  - Deskew covers ±100 ns in 1 ps steps.
  - High-resolution mode gives 14 or 16 bits. The scope has a frequency counter and totalizer.
- **Triggers and segmented recording:**
  - Trigger types include pulse width, runt, Nth edge, timeout, setup/hold, delay and pattern.
  - Segmented recording holds up to 500,000 frames.
- **Digital channels:**
  - 16 channels with the **optional PLA2216** probe (200 MHz). Minimum detectable pulse 5 ns.
  - Channel-to-channel skew ±5 ns typ. Probe load ≈ 8 pF / 101 kΩ. Thresholds are set per group of 8.
  - The digital sample rate and memory depth are not stated in the datasheet (**unverified**). Whether the bench owns a PLA2216 is **unverified**.
- **Interfaces:** USB 2.0 device, and **LAN 10/100 with LXI-C**.
- **Waveform readout:**
  - `:WAVeform:MODE RAW` reads internal memory, only while the scope is stopped, from 1 point to the full memory depth.
  - `NORMal` returns at most 1000 screen points.
  - `:WAVeform:STARt/STOP` read the memory in chunks (programming guide §3.28).

### STM32G474 ([DS12288 Rev 5](https://www.st.com/resource/en/datasheet/stm32g474re.pdf))
- **HRTIM:** a 170 MHz digital kernel followed by delay lines with closed-loop control, giving "184 ps resolution whatever the voltage, temperature or chip-to-chip manufacturing process deviation". It has 7 timers and 12 outputs.
- **ADC:** five 12-bit ADCs, 4 Msps at full resolution.
- **DAC:**
  - Three buffered external channels at 1 MSPS. Full-scale settling is 1.6 µs typ, 2.9 µs max to ±1 LSB.
  - Four unbuffered internal channels at 15 MSPS.
- At 170 MHz the DWT cycle counter ticks every 5.9 ns. A 32-bit timer input capture (TIM2) has the same resolution. On the MUX Spin, TIM2_CH3 is on free pin PB10 (AF1) (DS12288 alternate-function table; free-pin list in ADR 0009).

### What the Core PRs need
- **#164 DMA duty sequences:** one CMP1 value per period, up to `CONFIG_OWNTECH_POWER_DUTY_SEQ_MAX_LEN` = 1024 (Kconfig range 1–1024, default 64). Multi-leg start/stop is synchronised on the period.
  - 1024 periods last 5.1 ms at 200 kHz and 20.5 ms at 50 kHz.
- **#165 per-unit frequency:** units at their own period. Legs sharing a `leg-frequency` start aligned, and `hrtim_cnt_reset_sync()` restarts several counters.
- **#161/#162 ADC dispatch:** the bugs are lost or overwritten samples when a dispatch tick is late or a channel buffer is full. They are bookkeeping, not analog accuracy.
- **#168 DAC init:** the argument fix does not change behaviour while both defines are 2.

## 2. Verdict per test family

| Family | What must be seen | SPOC MUX path | Direct probe / logic | On-chip | Verdict |
|---|---|---|---|---|---|
| HRTIM 184 ps steps | Pulse width k, k+1, … in 184 ps steps | No: edges of 20–35 ns (estimate) and level-dependent RON | Yes. One or two probes at 4 or 2 GSa/s, interpolation, statistics over ≥ 1000 periods. At 5 ns/div, delta-time accuracy is ≈ ±70 ps, under one step. Judge differences between steps, not absolutes | — | **Suited, direct probes only** |
| Low duty near 0 and 1 | Shortest pulse, missing or extra pulse, 100 % handling | Only for "pulse present / absent" when the pulse is ≥ ~200 ns. Shorter pulses are rounded or lost | Yes: pulse-width and runt triggers, the 5 ns digital minimum | Register read-back | **Suited, direct probes** |
| Dead time, multi-leg alignment (#164 commit 4, #165) | Edge-to-edge times of a few ns between legs | Only at the ≥ 5 ns level, after L0-CAL skew correction | Yes. Analog: ≤ 200 ps channel delay plus the scope's deskew. Digital (PLA2216): ±5 ns, but across all 12 outputs | — | **Suited**: analog for ns verdicts, logic for "all legs at once" |
| Per-unit frequency (#165) | Frequency per leg, ratio, phase | **Yes**: frequency and duty through the MUX were steady to 0.2 Hz and 1e-4 (L0-CAL) | Yes | Counter read-back | **Suited as is** |
| Burst mode | Idle / burst period counts, pattern | **Yes** (counting periods). One scope trigger (Nth edge, timeout) and 25 Mpts cover many bursts | Logic probe for more than two pins | — | **Suited** once the wires are switchable |
| DMA duty sequences (#164) | Every period of a 64–1024 long table, first and last period clean, no skip or repeat | Pattern yes, at 1 ns sampling. 25 Mpts at 1 GSa/s = 25 ms holds 1024 periods at ≥ 50 kHz | Yes. Needs RAW reads of 1–25 Mpts, which the usbtmc link cannot do safely today | Timer input capture on the pin, by DMA: every period at 5.9 ns, no transfer | **Suited with changes 3 and 5**. Scope for the pattern, on-chip capture for exhaustive per-period checks |
| ADC dispatch (#161/#162) | No lost or duplicated samples, order preserved, under late ticks | No timing need beyond µs: a GPIO marker through the MUX is enough | — | **Main tool**: DWT stamps, injected delays (`irq_lock`, logging), a stimulus that codes time into each sample. A DAC ramp via TP1–TP3 self-injection, or the internal DAC | **Suited**, verdict on-chip (L0-TOV pattern) |
| DAC init (#168) | DAC level on its pin | **Yes**. The CUT DAC already measured within 2 mV through the MUX (L0-TRP / L0-ADC) | — | — | **Suited as is** |
| Zephyr bump | No regression | Whole L0 catalogue plus L0-TOV, as for safety | as L0-LAT | as L0-TOV | **Suited as is** |

**ADC injection.**
- The bench has two independent analog sources:
  - the CUT DAC on CH1/CH2 via TP1–TP3;
  - the MUX injector on CH3/CH4 via TP5–TP7.
- That covers the dispatch tests. A sawtooth on one pin gives each sample a unique value; the other pins hold levels.
- More sources are available if a test needs them. ADR 0009 already lists two more OPAMP followers on the MUX Spin: PA8 (OPAMP5 ← DAC4_CH2) and PB11 (OPAMP6 ← DAC3_CH1).
- The ±90 mV spikes at PWM edges seen on the pin nodes are a finding for test design, not a bench limit: sample away from the edges (spoc_bench.md, L0-ADC).

## 3. Bench changes before the dedicated bench, in priority order

1. **Make the injection wires switchable.**
   - Fit jumpers at minimum. Better, add analog switches or reed relays driven by a free MUX Spin GPIO: pins 5, 6, 14, 15, 19, 21, 22, 41 and 58 are free (ADR 0009).
   - A PWM campaign then gets four independent MUX channels, and a safety campaign gets its injection back.
   - The runner already detects TP5–TP7 from the injector level. Extend that check to TP1–TP3, and refuse a campaign whose wire state does not match its catalogue entry.
   - Why first: without it, any test that needs more than two PWM pins at once through the MUX is blocked.
2. **Fit the direct probes permanently, and measure the MUX path once.**
   - Build a small fixture at the CUT: probe sockets or ground-spring pads on two HRTIM legs, for example PA8/PA9 (TIMA) and PA10/PA11 (TIMB) or whichever legs the tests use.
   - This turns "probes off → BLOCKED" (L0-LAT, 2026-11-23) into a fixed setup.
   - Add an L0-CAL case: the same pin on a direct probe and through the MUX. It records the MUX path's rise time and delay, which replaces the estimates above.
   - Set the measured skews in the scope's own deskew (±100 ns, 1 ps steps) rather than correcting afterwards.
3. **Move the scope to LAN and add deep-memory reads.**
   - Use LXI over 100 Mb/s through the existing `VisaTransport` (a TCPIP resource). The usbtmc kernel driver already loses step on a 130 kB transfer.
   - Add `waveform_raw(channel, start, stop)` (`:WAV:MODE RAW`, scope stopped, chunked `STARt/STOP`, `FORMat BYTE`/`WORD`) to `rigol_mho.py`.
   - Size the memory to the test. 1024 periods at 200 kHz at 250 MSa/s is ~1.3 Mpts, not 25 Mpts.
   - Keep usbtmc as a fallback.
4. **Add the PLA2216 logic probe** (Rigol list price ~US$405, from the product page), if the bench does not have one.
   - It gives 16 digital channels across all 12 HRTIM outputs at 5 ns, for multi-leg start/stop, burst and sequence patterns.
   - The analog channels stay free for ns verdicts.
   - Check the digital sample rate and memory depth in the user guide before relying on them (**unverified**).
5. **Add an on-chip capture service** to the test firmware: a timer input capture by DMA on a PWM pin, CUT loopback.
   - Optionally, add an independent one on the MUX Spin: TIM2_CH3 on free pin PB10, wired to a mux test point. It then time-stamps any routed CUT pin with no link to the CUT.
   - This checks every period of a 1024-value sequence at 5.9 ns, with no scope transfer, and runs in seconds.
   - It also gives cross-checks at the DWT level for the ADC dispatch tests.
6. (Low.) **Order the 500 Mpts option (MHO900-RLU-05)** only if long sequences at low frequency, or long burst studies, outgrow 25–50 Mpts. Nothing in the current PRs needs it.

## 4. What the dedicated Raspberry Pi bench should include

- **Host:** a Raspberry Pi with the scope on LAN (LXI), both Spins and the ST-Link on a USB hub with per-port power switching. That allows remote replug and power-cycle, which the usbtmc and USB-drop recoveries need today (spoc_bench.md). OwnTech's [test-bench-spin](https://github.com/owntech-foundation/test-bench-spin) (Spin production tester on a Raspberry Pi) is prior art for the host side.
- **SPOC shield revision:**
  - Switch-controlled injection paths, not wires (change 1).
  - Permanent direct-probe connectors on the HRTIM outputs (change 2).
  - A mux with low RON and low capacitance, at least for the HRTIM output pins. Choose by criteria rather than part: RON ≲ 5 Ω, C_on ≲ 10 pF, bandwidth ≳ 300 MHz, specified at 3.3 V. The CD74HC4067 is specified only at 2/4.5/6 V and shares 3 × 50 pF per node.
  - Optionally, one output-family mux per channel instead of three on a shared node.
  - Fixed 30 cm cables and a fixed probe dress (cable length already broke the scope's measurements once).
- **Instruments:** MHO984 with the PLA2216. The independent capture MCU of change 5 is optional.
- **Calibration:** L0-CAL first in every campaign, as now, plus the MUX-path rise and delay case.
- **Remote control:** CUT reset and boot over SWD from the Pi, an enclosure, and a power switch that a script can drive.
