/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-DAC test firmware: the analog communication bus's DAC (DAC2 channel 1, PA6) at levels the host asks.
 *
 * communication.analog.init() sets up the bus as an application would (Core#168 fixed its DAC argument). The host
 * writes g_dac_report.cmd and then cmd_seq over SWD; the background task applies cmd with setAnalogCommValue() and
 * copies cmd_seq to ack_seq. No PWM is started: safe on a bare Spin (the SPOC CUT).
 */
#include <stdint.h>
#include <zephyr/kernel.h>

#include "CommunicationAPI.h"
#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x44414331U; /* "DAC1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t TICK_MS = 5U;

struct dac_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t cmd;      /* host: level to apply, 0..4095 */
	uint32_t cmd_seq;  /* host: incremented after cmd is written */
	uint32_t ack_seq;  /* firmware: cmd_seq once cmd is applied */
	uint32_t applied;  /* firmware: the level last applied */
	uint32_t ticks;    /* background task runs since boot */
};

__attribute__((used)) volatile dac_report_t g_dac_report;

void loop_background_task()
{
	if (g_dac_report.cmd_seq != g_dac_report.ack_seq) {
		uint32_t level = g_dac_report.cmd;
		communication.analog.setAnalogCommValue(level);
		g_dac_report.applied = level;
		g_dac_report.ack_seq = g_dac_report.cmd_seq;
	}
	g_dac_report.ticks = g_dac_report.ticks + 1U;
	task.suspendBackgroundMs(TICK_MS);
}

int main(void)
{
	g_dac_report.layout = REPORT_LAYOUT;
	g_dac_report.cmd = 0U;
	g_dac_report.cmd_seq = 0U;
	g_dac_report.ack_seq = 0U;
	g_dac_report.applied = 0U;
	g_dac_report.ticks = 0U;
	communication.analog.init();
	g_dac_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
