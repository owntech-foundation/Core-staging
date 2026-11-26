/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file timing_probe.h
 * @brief Cycle-counter probes and the RAM report of the L0-TIM test firmware (tests/l0/target_timing).
 *
 * Ported from Core2 apf_quadrature (ADR 0005: src/timing/timing_probe.h, timing_report.h,
 * owntech_timing_probe.h), but kept inside the test firmware: Core is timed at its API boundary, so Core itself
 * carries no probe and the same firmware builds on any ref (main, pre-zephyr-4.4).
 *
 * The host finds g_tim_report in firmware.elf and reads it over SWD without halting the core
 * (tests/l0/target_timing/run.py). Any layout change bumps TIM_LAYOUT and is mirrored in run.py.
 */
#ifndef TESTS_FIRMWARE_TIMING_PROBE_H
#define TESTS_FIRMWARE_TIMING_PROBE_H

#include <stddef.h>
#include <stdint.h>

/** @brief "TIMC" little-endian: L0-TIM report of the Core API timing firmware. */
#define TIM_MAGIC 0x434D4954UL
#define TIM_LAYOUT 1UL
#define TIM_MAX_ROWS 40U
#define TIM_NAME_LEN 24U

/** @brief Run state. */
enum tim_state_t : uint32_t {
	TIM_WAITING = 0U,    /**< Booted, waiting for the host's req_seq. */
	TIM_TASK_BARE = 1U,  /**< (b0) task timing, no data dispatch, running. */
	TIM_SWITCH = 2U,     /**< (b0) done; the background restarts the task with the dispatch. */
	TIM_CALLS = 3U,      /**< (a) cost per call running. */
	TIM_TASK = 4U,       /**< (b) task timing with the scheduler's dispatch running. */
	TIM_DONE = 5U,       /**< Every row final. */
	TIM_ERROR = 6U,      /**< Stopped; error says why. */
};

/** @brief Statistics of one timed quantity, in core cycles (counts for rows named "*_count"). */
struct tim_row_t {
	char name[TIM_NAME_LEN];
	uint32_t n;       /**< Samples. */
	uint32_t min;
	uint32_t max;     /**< Worst case. */
	uint32_t pad;
	uint64_t sum;     /**< For the mean. */
	uint64_t sumsq;   /**< For sigma. */
};

/** @brief Everything the host reads. */
struct tim_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t state;          /**< tim_state_t */
	uint32_t error;          /**< Non-zero with TIM_ERROR. */
	uint32_t core_hz;        /**< SystemCoreClock. */
	uint32_t req_seq;        /**< Host: 1 starts the run. */
	uint32_t ack_seq;        /**< Firmware: req_seq once started. */
	uint32_t req_repeats;    /**< Host: samples per call in (a); 0 = 100. */
	uint32_t req_task_runs;  /**< Host: critical-task runs timed in (b); 0 = 10000. */
	uint32_t task_period_us; /**< Critical-task period. */
	uint32_t probe_overhead; /**< Cycles of an empty probe pair (minimum), included in every (a) row. */
	uint32_t hrtim_ckpsc;    /**< HRTIM master prescaler (entry latency conversion). */
	uint32_t runs;           /**< Critical-task runs so far (progress). */
	uint32_t row_count;
	int32_t rc;              /**< createCritical() return code. */
	uint32_t pwm_period_cycles; /**< HRTIM master period in core cycles (irqHeldOff is modulo this). */
	tim_row_t rows[TIM_MAX_ROWS];
};

static_assert(sizeof(tim_row_t) == 56, "tim_row_t layout (run.py)");
static_assert(offsetof(tim_report_t, rows) == 64, "tim_report_t header layout (run.py)");

/** @brief DWT cycle counter. */
static inline uint32_t tim_cycles()
{
	return *(volatile const uint32_t *)0xE0001004UL;
}

/** @brief Enables the DWT cycle counter (DEMCR.TRCENA, DWT_CTRL.CYCCNTENA). */
static inline void tim_enable_cycle_counter()
{
	*(volatile uint32_t *)0xE000EDFCUL |= (1UL << 24);
	*(volatile uint32_t *)0xE0001000UL |= 1UL;
}

/** @brief Adds one sample to a row. */
static inline void tim_add(volatile tim_row_t &r, uint32_t v)
{
	if (r.n == 0U || v < r.min) {
		r.min = v;
	}
	if (v > r.max) {
		r.max = v;
	}
	r.n = r.n + 1U;
	r.sum = r.sum + v;
	r.sumsq = r.sumsq + (uint64_t)v * v;
}

#endif /* TESTS_FIRMWARE_TIMING_PROBE_H */
