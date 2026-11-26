/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-ADD test firmware: ADC data dispatch under a critical task (Core#161, #162, #173, #175).
 *
 * HRTIM at 200 kHz, unit A running (PA8/PA9 toggle: only the SPOC MUX sees them). The host picks a mode over SWD
 * (req_mode, then req_seq); the background task then configures acquisition and starts the critical task, one
 * mode per boot:
 *  - mode 1, dispatch: ADC2 triggered every PWM period (discontinuous, one channel per trigger) on pin 34 (PA6,
 *    ADC2 IN3) and pin 35 (PC4, ADC2 IN5). A 100 us HRTIM task, the scheduler dispatching the data, fetches pin 35
 *    every run (counts: no samples lost) and never fetches pin 34, which the DAC drives with a sawtooth on PA6:
 *    its peek() must follow. Every late_every runs the task busy-waits late_us, so the next dispatch is late.
 *    The background task spins on a counter: what the dispatch leaves of the CPU.
 *  - mode 2, heap: pins 24 (ADC1, software-triggered) and 34 (ADC2, converting every PWM period), a 5 ms TIM6
 *    task: DMA buffers of 1000+ samples per ADC, more than the 4 KB heap holds for both. The board must keep running. TIM6 is a zero-latency interrupt: its task makes no
 *    kernel call, and run_ms is stamped by the background task.
 */
#include <stdint.h>
#include <string.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x41444431U; /* "ADD1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;

struct add_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t req_mode;     /* host: 1 dispatch, 2 heap */
	uint32_t late_every;   /* host: busy-wait every N runs (0: never) */
	uint32_t late_us;      /* host: busy-wait length */
	uint32_t req_seq;      /* host: incremented after the request */
	uint32_t ack_seq;      /* firmware */
	int32_t rc;            /* createCritical() */
	uint32_t runs;         /* critical task runs */
	uint32_t run_ms;       /* k_uptime_get_32() at the last run */
	uint32_t fetched;      /* pin 35 values fetched in total */
	uint32_t empty_calls;  /* runs where pin 35 had no value */
	uint32_t max_per_call; /* most pin 35 values in one fetch */
	uint32_t late_count;   /* busy-waits done */
	uint32_t peek34;       /* float bits of peekLatestValue(34) */
	uint32_t peek24;       /* float bits of peekLatestValue(24) (mode 2) */
	uint32_t saw;          /* DAC level written last */
	uint32_t bg_count;     /* background spins */
	uint32_t bg_ms;        /* k_uptime_get_32() at the last background spin */
};

__attribute__((used)) volatile add_report_t g_add_report;

static uint32_t saw = 300U;

static uint32_t float_bits(float32_t f)
{
	uint32_t u;
	memcpy(&u, &f, sizeof(u));
	return u;
}

void task_dispatch()
{
	uint32_t runs = g_add_report.runs + 1U;
	uint32_t n = 0;
	spin.data.getRawValues(35, n);
	g_add_report.fetched = g_add_report.fetched + n;
	if (n == 0U) {
		g_add_report.empty_calls = g_add_report.empty_calls + 1U;
	}
	if (n > g_add_report.max_per_call) {
		g_add_report.max_per_call = n;
	}
	g_add_report.peek34 = float_bits(spin.data.peekLatestValue(34));
	saw = saw + 37U;
	if (saw > 3700U) {
		saw = 300U;
	}
	spin.dac.setConstValue(2, 1, saw);
	g_add_report.saw = saw;
	g_add_report.runs = runs;
	g_add_report.run_ms = k_uptime_get_32();
	if (g_add_report.late_every != 0U && runs % g_add_report.late_every == 0U) {
		k_busy_wait(g_add_report.late_us);
		g_add_report.late_count = g_add_report.late_count + 1U;
	}
}

/* A TIM6 critical task runs in a zero-latency interrupt: no kernel call here (k_uptime_get_32() races the
 * kernel's timeout list and faults the board). The background task stamps the uptime instead. */
void task_heap()
{
	g_add_report.runs = g_add_report.runs + 1U;
	g_add_report.peek34 = float_bits(spin.data.peekLatestValue(34));
	g_add_report.peek24 = float_bits(spin.data.peekLatestValue(24));
}

static void start_mode(uint32_t mode)
{
	if (mode == 1U) {
		spin.pwm.setAdcDecimation(PWMA, 1);
		spin.pwm.setAdcTrigger(PWMA, ADC_2);
		spin.pwm.enableAdcTrigger(PWMA);
		spin.pwm.setAdcTriggerInstant(PWMA, 0.06);
		spin.data.configureTriggerSource(ADC_2, TRIG_PWM);
		spin.data.configureDiscontinuousMode(ADC_2, 1);
		spin.data.enableAcquisition(34, ADC_2);
		spin.data.enableAcquisition(35, ADC_2);
		spin.dac.initConstValue(2);
		spin.dac.setConstValue(2, 1, saw);
		g_add_report.rc = task.createCritical(task_dispatch, 100);
	} else {
		/* ADC1 (software-triggered) takes the heap first; ADC2 converts every PWM period, so a DMA buffer it
		 * could not get is written through (Core#175). */
		spin.pwm.setAdcDecimation(PWMA, 1);
		spin.pwm.setAdcTrigger(PWMA, ADC_2);
		spin.pwm.enableAdcTrigger(PWMA);
		spin.pwm.setAdcTriggerInstant(PWMA, 0.06);
		spin.data.configureTriggerSource(ADC_1, TRIG_SOFTWARE);
		spin.data.configureTriggerSource(ADC_2, TRIG_PWM);
		spin.data.configureDiscontinuousMode(ADC_2, 1);
		spin.data.enableAcquisition(24, ADC_1);
		spin.data.enableAcquisition(34, ADC_2);
		g_add_report.rc = task.createCritical(task_heap, 5000, source_tim6);
	}
	if (g_add_report.rc == 0) {
		task.startCritical(true);
	}
}

void loop_background_task()
{
	if (g_add_report.req_seq != g_add_report.ack_seq && g_add_report.ack_seq == 0U) {
		start_mode(g_add_report.req_mode);
		g_add_report.ack_seq = g_add_report.req_seq;
	}
	if (g_add_report.ack_seq == 0U) {
		task.suspendBackgroundMs(10);
		return;
	}
	/* Spin: the count measures the CPU the critical task and the dispatch leave. */
	for (uint32_t i = 0; i < 100000U; i++) {
		g_add_report.bg_count = g_add_report.bg_count + 1U;
	}
	g_add_report.bg_ms = k_uptime_get_32();
	if (g_add_report.req_mode == 2U) {
		g_add_report.run_ms = g_add_report.bg_ms; /* with runs, read live: one run of error in hundreds */
	}
	task.suspendBackgroundMs(1);
}

int main(void)
{
	g_add_report.layout = REPORT_LAYOUT;
	spin.pwm.setFrequency(200000);
	spin.pwm.initUnit(PWMA);
	spin.pwm.setDutyCycle(PWMA, 0.5);
	spin.pwm.startDualOutput(PWMA);
	g_add_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
