/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-CTK test firmware: task.createCritical() at periods the host asks, on HRTIM or TIM6 (Core#174).
 *
 * The HRTIM is set to 200 kHz and unit A initialised (master timer running, no output started). The host writes
 * req_source and req_period_us, then req_seq, over SWD; the background task calls createCritical(), stores its
 * return code and, when it is 0, starts the task. The critical task counts its runs and toggles PA6. The
 * background task stamps runs and uptime every 10 ms, so the host derives the period on-chip.
 */
#include <stdint.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x43544B31U; /* "CTK1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t PWM_FREQUENCY_HZ = 200000U;

struct ctk_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t req_source;     /* host: 0 = source_hrtim, 1 = source_tim6 */
	uint32_t req_period_us;  /* host */
	uint32_t req_seq;        /* host: incremented after the request is written */
	uint32_t ack_seq;        /* firmware: req_seq once handled */
	int32_t rc;              /* firmware: createCritical() return code */
	uint32_t started;        /* firmware: 1 once a critical task runs */
	uint32_t runs;           /* critical task runs */
	uint32_t stamp_runs;     /* runs at the last stamp */
	uint32_t stamp_ms;       /* k_uptime_get_32() at the last stamp */
};

__attribute__((used)) volatile ctk_report_t g_ctk_report;

void critical_task()
{
	g_ctk_report.runs = g_ctk_report.runs + 1U;
	spin.gpio.togglePin(PA6);
}

void loop_background_task()
{
	if (g_ctk_report.req_seq != g_ctk_report.ack_seq) {
		scheduling_interrupt_source_t src = g_ctk_report.req_source == 1U ? source_tim6 : source_hrtim;
		int8_t rc = task.createCritical(critical_task, g_ctk_report.req_period_us, src);
		g_ctk_report.rc = rc;
		if (rc == 0 && g_ctk_report.started == 0U) {
			task.startCritical(false);
			g_ctk_report.started = 1U;
		}
		g_ctk_report.ack_seq = g_ctk_report.req_seq;
	}
	g_ctk_report.stamp_runs = g_ctk_report.runs;
	g_ctk_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(10);
}

int main(void)
{
	g_ctk_report.layout = REPORT_LAYOUT;
	g_ctk_report.req_seq = 0U;
	g_ctk_report.ack_seq = 0U;
	g_ctk_report.rc = 0;
	g_ctk_report.started = 0U;
	g_ctk_report.runs = 0U;
	spin.gpio.configurePin(PA6, OUTPUT);
	spin.pwm.setFrequency(PWM_FREQUENCY_HZ);
	spin.pwm.initUnit(PWMA);
	g_ctk_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
