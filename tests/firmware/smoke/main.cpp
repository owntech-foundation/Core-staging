/*
 * Copyright (c) 2026 OWNTECH.
 *
 * SPDX-License-Identifier: LGPL-2.1
 */

/**
 * @file main.cpp
 * @brief SMOKE test firmware (tests/l0/target_smoke): proves build -> flash ->
 *        SWD read. Publishes a report struct in RAM that the harness reads
 *        without halting the CPU. Drives no output but the Spin's LED.
 */

#include <stdint.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"

/* Mirrored in tests/l0/target_smoke/run.py. */
static constexpr uint32_t REPORT_MAGIC = 0x534D4B31U; /* "SMK1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t TICK_MS = 10U;

struct smoke_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t ticks;     /* background task runs since boot */
	uint32_t uptime_ms; /* k_uptime_get_32() at the last run */
};

__attribute__((used)) volatile smoke_report_t g_smoke_report;

void loop_background_task()
{
	g_smoke_report.ticks = g_smoke_report.ticks + 1U;
	g_smoke_report.uptime_ms = k_uptime_get_32();
	if (g_smoke_report.ticks % 50U == 0U) {
		spin.led.toggle();
	}
	task.suspendBackgroundMs(TICK_MS);
}

int main(void)
{
	g_smoke_report.layout = REPORT_LAYOUT;
	g_smoke_report.ticks = 0U;
	g_smoke_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
