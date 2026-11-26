/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-HRI test firmware: an up-down (center-aligned) timing unit initialised again and again (Core#172).
 *
 * PWMA is set up-down at 200 kHz, initialised once at boot and started at duty 0.5 on PA8/PA9. Each time the host
 * increments req_seq over SWD, the background task stops PWMA, calls initUnit(PWMA) again, restores duty 0.5 and
 * starts it again, then records PERxR of timer A, the master period MPER and the driver's period. inits_started is
 * written before initUnit() and inits_done after it, so a re-init that never returns (the period assert of
 * _period_ckpsc() before the fix) shows as inits_started > inits_done. The background task stamps uptime every
 * 10 ms, so the host also sees whether the firmware still runs.
 */
#include <stdint.h>
#include <soc.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x48524931U; /* "HRI1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t PWM_FREQUENCY_HZ = 200000U;

struct hri_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t req_seq;       /* host: incremented to ask for one more initUnit(PWMA) */
	uint32_t ack_seq;       /* firmware: req_seq once handled */
	uint32_t inits_started; /* firmware: initUnit(PWMA) calls entered, boot one included */
	uint32_t inits_done;    /* firmware: initUnit(PWMA) calls returned */
	uint32_t per_a;         /* HRTIM1 timer A PERxR after the last init */
	uint32_t mper;          /* HRTIM1 master MPER after the last init */
	uint32_t period_drv;    /* spin.pwm.getPeriod(PWMA) after the last init */
	uint32_t stamp_ms;      /* k_uptime_get_32() at the last background run */
};

__attribute__((used)) volatile hri_report_t g_hri_report;

static void init_pwma()
{
	g_hri_report.inits_started = g_hri_report.inits_started + 1U;
	spin.pwm.initUnit(PWMA);
	g_hri_report.inits_done = g_hri_report.inits_done + 1U;
	spin.pwm.setDutyCycle(PWMA, 0.5);
	g_hri_report.per_a = HRTIM1->sTimerxRegs[0].PERxR;
	g_hri_report.mper = HRTIM1->sMasterRegs.MPER;
	g_hri_report.period_drv = spin.pwm.getPeriod(PWMA);
}

void loop_background_task()
{
	if (g_hri_report.req_seq != g_hri_report.ack_seq) {
		spin.pwm.stopDualOutput(PWMA);
		init_pwma();
		spin.pwm.startDualOutput(PWMA);
		g_hri_report.ack_seq = g_hri_report.req_seq;
	}
	g_hri_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(10);
}

int main(void)
{
	g_hri_report.layout = REPORT_LAYOUT;
	g_hri_report.req_seq = 0U;
	g_hri_report.ack_seq = 0U;
	g_hri_report.inits_started = 0U;
	g_hri_report.inits_done = 0U;
	spin.pwm.setFrequency(PWM_FREQUENCY_HZ);
	spin.pwm.setModulation(PWMA, UpDwn);
	init_pwma();
	spin.pwm.startDualOutput(PWMA);
	g_hri_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
