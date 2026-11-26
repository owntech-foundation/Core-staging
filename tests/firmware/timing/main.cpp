/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-TIM test firmware: cost of the Core API hot-path calls and critical-task timing, counted with the DWT
 *        cycle counter into a RAM report read over SWD (tests/l0/target_timing, Core-staging#37).
 *
 * Setup, as a user's control firmware would have it: HRTIM at 200 kHz, PWMA running (PA8/PA9 toggle: a bare Spin
 * only, like env ADD), PWMC initialised (no output: the phase-shift target), a 100 us critical task on the HRTIM
 * master period. Core is timed at its API boundary only: nothing in Core is instrumented, so the same firmware
 * builds on any ref.
 *
 * The host writes req_repeats and req_task_runs (0 = defaults), then req_seq = 1. Phases, in order:
 *  (b0) TIM_TASK_BARE: the critical task runs with startCritical(false) and no acquisition: the interrupt and the
 *       task proxy alone. Rows "task0.*".
 *  (a)  TIM_CALLS: the task is stopped, acquisition configured (ADC2 on pins 34 and 35 every PWM period,
 *       discontinuous, one channel per trigger; ADC1 software-triggered on pin 24) and the task restarted with
 *       startCritical(true): the scheduler dispatches the data before each run, as in a user's firmware. Each run
 *       then times ONE hot-path call (round robin), interrupts masked around it (PRIMASK), the arguments
 *       alternating so no call takes a "nothing changed" shortcut. Five warm-up rounds, then req_repeats rounds.
 *  (b)  TIM_TASK: same as (b0) with the scheduler's dispatch. Rows "task.*".
 * During (b0) and (b) the background task spins on the cycle counter: a gap in its loop is an interruption. For a
 * gap holding a critical run, the gap start to the user function's entry is the entry latency (interrupt
 * wrapper, task proxy, data dispatch), the function's exit to the gap end the exit latency. The HRTIM master
 * counter at entry gives the time since the period event modulo the PWM period; minus the entry latency, it is how
 * long the interrupt was held off (hardware latency plus anything masking it or running above it).
 * PA6 is high from task entry to exit in (b0) and (b): the marker for the scope (not judged yet: needs the MHO984).
 */
#include <stdint.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <soc.h>

#include "SpinAPI.h"
#include "TaskAPI.h"
#include "timing_probe.h"

__attribute__((used)) volatile tim_report_t g_tim_report;

static constexpr uint32_t PWM_FREQUENCY_HZ = 200000U;
static constexpr uint32_t TASK_PERIOD_US = 100U;
static constexpr uint32_t DEFAULT_REPEATS = 100U;
static constexpr uint32_t DEFAULT_TASK_RUNS = 10000U;
static constexpr uint32_t WARMUP_ROUNDS = 5U;
static constexpr uint32_t TASK_SETTLE_RUNS = 1000U;  /* (b0), (b): the background is spinning before rows count */
static constexpr uint32_t BG_GAP_MIN = 40U;          /* cycles: a longer background gap is an interruption */
static constexpr uint8_t PIN_FETCHED = 35U;          /* ADC2, new samples every run */
static constexpr uint8_t PIN_PEEKED = 34U;           /* ADC2 */
static constexpr uint8_t PIN_SOFT = 24U;             /* ADC1, software trigger */

/* (a) rows: the hot-path calls, in round-robin order. Listed and justified in tests/l0/target_timing/README.md. */
enum call_t : uint32_t {
	C_PROBE_EMPTY = 0,
	C_PWM_SET_DUTY,
	C_PWM_SET_DUTY_RAW,
	C_PWM_SET_PHASE,
	C_PWM_SET_ADC_TRIG,
	C_PWM_STOP_DUAL,
	C_PWM_START_DUAL,
	C_PWM_STOP_SINGLE,
	C_PWM_START_SINGLE,
	C_PWM_GET_PERIOD,
	C_DATA_GET_LATEST,
	C_DATA_GET_LATEST_VALID,
	C_DATA_PEEK_LATEST,
	C_DATA_GET_RAW,
	C_DATA_GET_VALUES,
	C_DATA_CONVERT,
	C_DATA_TRIGGER,
	C_COUNT
};
static const char *const call_names[C_COUNT] = {
	"probe.empty",
	"pwm.setDutyCycle", "pwm.setDutyCycleRaw", "pwm.setPhaseShift", "pwm.setAdcTrigInstant",
	"pwm.stopDualOutput", "pwm.startDualOutput", "pwm.stopSingleOutput", "pwm.startSingleOutput",
	"pwm.getPeriod",
	"data.getLatestValue", "data.getLatestValueV", "data.peekLatestValue",
	"data.getRawValues", "data.getValues", "data.convertValue", "data.triggerAcq",
};

/* (b0) and (b) rows, after the call rows: one block per task phase. */
enum task_row_t : uint32_t {
	T_PERIOD = 0,   /* entry to entry */
	T_ENTRY,        /* gap start (interrupt taken) to user function entry */
	T_EXIT,         /* user function exit to gap end (background resumes) */
	T_OVERHEAD,     /* gap minus user function */
	T_HELD_OFF,     /* period event to interrupt taken (mod PWM period) */
	T_BODY,         /* user function */
	T_OTHER,        /* background gaps holding no critical run */
	T_COUNT
};
static const char *const task_row_names[T_COUNT] = {
	"period", "entryLatency", "exitLatency", "isrOverhead", "irqHeldOff", "body", "otherIrq",
};
static constexpr uint32_t ROWS_BARE = C_COUNT;
static constexpr uint32_t ROWS_TASK = (uint32_t)C_COUNT + (uint32_t)T_COUNT;
static constexpr uint32_t ROW_COUNT = (uint32_t)C_COUNT + 2U * (uint32_t)T_COUNT;
static_assert(ROW_COUNT <= TIM_MAX_ROWS, "report rows");

static volatile uint32_t sink_u32;
static volatile float32_t sink_f32;

/* What the background needs from the last critical run. */
static volatile uint32_t last_entry;
static volatile uint32_t last_body;
static volatile uint32_t last_mcntr;  /* HRTIM master counter at entry, in cycles */
static volatile uint32_t bg_record;   /* row block the background records into, 0 = none */

static uint32_t repeats, task_runs, pwm_period_cycles;
static uint32_t calls_runs, task_phase_runs, prev_entry;
static uint16_t raw_sample = 2048U;

static inline uint32_t primask_save()
{
	uint32_t pm;
	__asm volatile("mrs %0, primask\n cpsid i" : "=r"(pm)::"memory");
	return pm;
}

static inline void primask_restore(uint32_t pm)
{
	__asm volatile("msr primask, %0" ::"r"(pm) : "memory");
}

/** @brief HRTIM master counter ticks to core cycles (HRTIM clocked at the core clock). */
static inline uint32_t hrtim_to_cycles(uint32_t cnt)
{
	uint32_t psc = g_tim_report.hrtim_ckpsc;
	/* CKPSC 0..4: the counter runs at f x 32 / 2^CKPSC; 5..7: f / 2^(CKPSC - 5). */
	return psc < 5U ? (cnt << psc) >> 5 : cnt << (psc - 5U);
}

/** @brief One hot-path call, timed; round r alternates the arguments. @return cycles, probe pair included. */
static uint32_t time_call(uint32_t c, uint32_t r)
{
	const bool odd = (r & 1U) != 0U;
	uint32_t n = 0;
	uint32_t pm = primask_save();
	uint32_t t0 = tim_cycles();
	switch (c) {
	case C_PROBE_EMPTY: break;
	case C_PWM_SET_DUTY: spin.pwm.setDutyCycle(PWMA, odd ? 0.45f : 0.55f); break;
	case C_PWM_SET_DUTY_RAW: spin.pwm.setDutyCycleRaw(PWMA, odd ? 12000U : 15000U); break;
	case C_PWM_SET_PHASE: spin.pwm.setPhaseShift(PWMC, odd ? 90 : 180); break;
	case C_PWM_SET_ADC_TRIG: spin.pwm.setAdcTriggerInstant(PWMA, odd ? 0.06f : 0.07f); break;
	case C_PWM_STOP_DUAL: spin.pwm.stopDualOutput(PWMA); break;
	case C_PWM_START_DUAL: spin.pwm.startDualOutput(PWMA); break;
	case C_PWM_STOP_SINGLE: spin.pwm.stopSingleOutput(PWMA, TIMING_OUTPUT1); break;
	case C_PWM_START_SINGLE: spin.pwm.startSingleOutput(PWMA, TIMING_OUTPUT1); break;
	case C_PWM_GET_PERIOD: sink_u32 = spin.pwm.getPeriod(PWMA); break;
	case C_DATA_GET_LATEST: sink_f32 = spin.data.getLatestValue(PIN_FETCHED); break;
	case C_DATA_GET_LATEST_VALID: {
		uint8_t valid = 0;
		sink_f32 = spin.data.getLatestValue(PIN_FETCHED, &valid);
		sink_u32 = valid;
		break;
	}
	case C_DATA_PEEK_LATEST: sink_f32 = spin.data.peekLatestValue(PIN_PEEKED); break;
	case C_DATA_GET_RAW: {
		uint16_t *p = spin.data.getRawValues(PIN_FETCHED, n);
		if (n != 0U && p != nullptr) {
			raw_sample = p[n - 1U];
		}
		break;
	}
	case C_DATA_GET_VALUES: {
		float32_t *p = spin.data.getValues(PIN_FETCHED, n);
		if (n != 0U && p != nullptr) {
			sink_f32 = p[n - 1U];
		}
		break;
	}
	case C_DATA_CONVERT: sink_f32 = spin.data.convertValue(PIN_FETCHED, raw_sample); break;
	case C_DATA_TRIGGER: spin.data.triggerAcquisition(ADC_1); break;
	default: break;
	}
	uint32_t t1 = tim_cycles();
	primask_restore(pm);
	sink_u32 = n;
	return t1 - t0;
}

/** @brief (a): one call per run, round robin. */
static void run_calls()
{
	uint32_t c = calls_runs % C_COUNT;
	uint32_t round = calls_runs / C_COUNT;
	uint32_t cyc = time_call(c, round);
	if (round >= WARMUP_ROUNDS) {
		tim_add(g_tim_report.rows[c], cyc);
	}
	calls_runs++;
	if (round + 1U >= WARMUP_ROUNDS + repeats && c == C_COUNT - 1U) {
		/* Leave the PWM as it started: both outputs on, initial duty. */
		spin.pwm.startDualOutput(PWMA);
		spin.pwm.setDutyCycle(PWMA, 0.5f);
		task_phase_runs = 0U;
		g_tim_report.state = TIM_TASK;
	}
}

/** @brief (b0), (b): the period between entries; the background records the rest. */
static void run_task(uint32_t entry, uint32_t block, uint32_t next_state)
{
	if (task_phase_runs == TASK_SETTLE_RUNS) {
		bg_record = block;
	}
	if (task_phase_runs > TASK_SETTLE_RUNS) {
		tim_add(g_tim_report.rows[block + T_PERIOD], entry - prev_entry);
	}
	task_phase_runs++;
	if (task_phase_runs > TASK_SETTLE_RUNS + task_runs) {
		bg_record = 0U;
		g_tim_report.state = next_state;
	}
}

void critical_task()
{
	uint32_t entry = tim_cycles();
	uint32_t mcntr = hrtim_to_cycles(HRTIM1->sMasterRegs.MCNTR & 0xFFFFU);
	uint32_t state = g_tim_report.state;
	uint32_t block = 0U;
	if (state == TIM_TASK_BARE || state == TIM_TASK) {
		GPIOA->BSRR = GPIO_BSRR_BS6;  /* marker: task entry (scope, not judged yet) */
		block = state == TIM_TASK ? ROWS_TASK : ROWS_BARE;
		run_task(entry, block, state == TIM_TASK ? TIM_DONE : TIM_SWITCH);
		GPIOA->BSRR = GPIO_BSRR_BR6;
	} else if (state == TIM_CALLS) {
		run_calls();
	}
	g_tim_report.runs = g_tim_report.runs + 1U;
	prev_entry = entry;
	uint32_t body = tim_cycles() - entry;
	if (block != 0U && bg_record == block) {
		tim_add(g_tim_report.rows[block + T_BODY], body);
	}
	last_mcntr = mcntr;
	last_body = body;
	last_entry = entry;
}

/** @brief (b0), (b): spins on the cycle counter; every gap is an interruption of the background. */
static void background_spin(uint32_t state)
{
	uint32_t prev = tim_cycles();
	while (g_tim_report.state == state) {
		uint32_t now = tim_cycles();
		uint32_t gap = now - prev;
		uint32_t block = bg_record;
		if (gap > BG_GAP_MIN && block != 0U) {
			uint32_t entry = last_entry;
			uint32_t to_entry = entry - prev;
			if (to_entry < gap) {
				/* A critical run started inside the gap. Anything else in the same gap (e.g. the kernel tick
				 * taken just before) counts in the entry latency: the worst case shows it, the mean barely. */
				uint32_t body = last_body;
				uint32_t from_exit = gap - to_entry > body ? gap - to_entry - body : 0U;
				tim_add(g_tim_report.rows[block + T_ENTRY], to_entry);
				tim_add(g_tim_report.rows[block + T_EXIT], from_exit);
				tim_add(g_tim_report.rows[block + T_OVERHEAD], to_entry + from_exit);
				if (pwm_period_cycles != 0U) {
					uint32_t m = last_mcntr + pwm_period_cycles * (to_entry / pwm_period_cycles + 1U);
					tim_add(g_tim_report.rows[block + T_HELD_OFF], (m - to_entry) % pwm_period_cycles);
				}
			} else {
				tim_add(g_tim_report.rows[block + T_OTHER], gap);
			}
		}
		prev = now;
	}
}

static void configure_acquisition()
{
	spin.pwm.setAdcDecimation(PWMA, 1);
	spin.pwm.setAdcTrigger(PWMA, ADC_2);
	spin.pwm.enableAdcTrigger(PWMA);
	spin.pwm.setAdcTriggerInstant(PWMA, 0.06f);
	spin.data.configureTriggerSource(ADC_1, TRIG_SOFTWARE);
	spin.data.configureTriggerSource(ADC_2, TRIG_PWM);
	spin.data.configureDiscontinuousMode(ADC_2, 1);
	spin.data.enableAcquisition(PIN_SOFT, ADC_1);
	spin.data.enableAcquisition(PIN_PEEKED, ADC_2);
	spin.data.enableAcquisition(PIN_FETCHED, ADC_2);
}

static void start_run()
{
	repeats = g_tim_report.req_repeats ? g_tim_report.req_repeats : DEFAULT_REPEATS;
	task_runs = g_tim_report.req_task_runs ? g_tim_report.req_task_runs : DEFAULT_TASK_RUNS;
	g_tim_report.req_repeats = repeats;
	g_tim_report.req_task_runs = task_runs;
	g_tim_report.hrtim_ckpsc = HRTIM1->sMasterRegs.MCR & HRTIM_MCR_CK_PSC;
	pwm_period_cycles = hrtim_to_cycles(HRTIM1->sMasterRegs.MPER & 0xFFFFU);
	g_tim_report.pwm_period_cycles = pwm_period_cycles;

	/* Probe overhead: minimum of an empty probe pair, interrupts masked. */
	uint32_t best = UINT32_MAX;
	for (int i = 0; i < 100; i++) {
		uint32_t c = time_call(C_PROBE_EMPTY, 0);
		best = c < best ? c : best;
	}
	g_tim_report.probe_overhead = best;

	task_phase_runs = 0U;
	g_tim_report.state = TIM_TASK_BARE;
	g_tim_report.rc = task.createCritical(critical_task, TASK_PERIOD_US, source_hrtim);
	if (g_tim_report.rc != 0) {
		g_tim_report.error = 1U;
		g_tim_report.state = TIM_ERROR;
		return;
	}
	task.startCritical(false);
}

/** @brief After (b0): stop the task, configure acquisition, restart with the scheduler managing the data. */
static void switch_to_dispatch()
{
	task.stopCritical();
	configure_acquisition();
	calls_runs = 0U;
	g_tim_report.state = TIM_CALLS;
	task.startCritical(true);
}

void loop_background_task()
{
	if (g_tim_report.req_seq != 0U && g_tim_report.ack_seq == 0U) {
		start_run();
		g_tim_report.ack_seq = g_tim_report.req_seq;
	}
	uint32_t state = g_tim_report.state;
	if (state == TIM_TASK_BARE || state == TIM_TASK) {
		background_spin(state);
	} else if (state == TIM_SWITCH) {
		switch_to_dispatch();
	}
	task.suspendBackgroundMs(1);
}

int main(void)
{
	tim_enable_cycle_counter();
	g_tim_report.layout = TIM_LAYOUT;
	g_tim_report.core_hz = SystemCoreClock;
	g_tim_report.task_period_us = TASK_PERIOD_US;
	for (uint32_t i = 0; i < C_COUNT; i++) {
		strncpy((char *)g_tim_report.rows[i].name, call_names[i], TIM_NAME_LEN - 1U);
	}
	for (uint32_t b = 0; b < 2U; b++) {
		for (uint32_t i = 0; i < T_COUNT; i++) {
			char *dst = (char *)g_tim_report.rows[C_COUNT + b * T_COUNT + i].name;
			strcpy(dst, b == 0U ? "task0." : "task.");
			strncat(dst, task_row_names[i], TIM_NAME_LEN - 1U - strlen(dst));
		}
	}
	g_tim_report.row_count = ROW_COUNT;

	spin.gpio.configurePin(PA6, OUTPUT);
	spin.pwm.setFrequency(PWM_FREQUENCY_HZ);
	spin.pwm.initUnit(PWMA);
	spin.pwm.initUnit(PWMC);
	spin.pwm.setDutyCycle(PWMA, 0.5f);
	spin.pwm.startDualOutput(PWMA);
	g_tim_report.magic = TIM_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
