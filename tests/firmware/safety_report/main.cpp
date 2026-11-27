/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-SAF test firmware: what the Safety error thread prints when one channel trips (Core-staging#47).
 *
 * The six Twist channels the error thread reports (I1, V1, I2, V2, Ihigh, Vhigh) are acquired on ADC1/ADC2,
 * software-triggered from a TIM6 critical task (no PWM is started: safe on a bare Spin such as the SPOC CUT), and
 * all six are watched by the Safety API. Thresholds are wide (no trip) except for the channel the host asks: its
 * minimum is set above any reading, so it trips whatever the bare pin reads. A printk hook copies the console
 * output; each complete "SAFETY ERROR" block the thread prints lands in g_saf_report.block for the host to parse.
 *
 * Host protocol (SWD): write cmd (channel index 0..5 in CHANNELS order), then cmd_seq. The firmware clears every
 * error, trips that channel, waits until the Safety API flags it, then copies blocks to blocks_at_trip and cmd_seq
 * to ack_seq. The block still being printed then may have started before the trip; the next one is whole after it:
 * it is copied to block, which then stays frozen, and ack_seq to snap_seq.
 */
#include <stdint.h>
#include <string.h>
#include <zephyr/kernel.h>
extern "C" {
#include <zephyr/sys/printk-hooks.h> /* no extern "C" of its own */
}

#include "SafetyAPI.h"
#include "ShieldAPI.h"
#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x53414631U; /* "SAF1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t BLOCK_SIZE = 256U;
static constexpr uint32_t TASK_PERIOD_US = 1000U;
static constexpr uint32_t TRIP_TIMEOUT_MS = 2000U;

/* The error thread's order (uninterruptible_synchronous_task.cpp). */
static sensor_t CHANNELS[6] = {I1_LOW, V1_LOW, I2_LOW, V2_LOW, I_HIGH, V_HIGH};
static adc_t CHANNEL_ADCS[6] = {ADC_1, ADC_1, ADC_2, ADC_2, ADC_2, ADC_1}; /* Twist default-adc */

struct saf_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t cmd;            /* host: channel to trip, index into CHANNELS */
	uint32_t cmd_seq;        /* host: incremented after cmd is written */
	uint32_t ack_seq;        /* firmware: cmd_seq once the channel is flagged */
	int32_t rc;              /* sensors, data start, createCritical: 0 when all succeeded */
	uint32_t tripped;        /* 1: the Safety API flagged cmd within the timeout */
	uint32_t errors;         /* getChannelError() of CHANNELS, bit i, when acknowledged */
	uint32_t runs;           /* critical task runs */
	uint32_t blocks;         /* complete error blocks captured */
	uint32_t blocks_at_trip; /* blocks when the trip was acknowledged */
	uint32_t chars;          /* printk characters seen by the hook */
	uint32_t snap_seq;       /* ack_seq of the trip whose first whole block is in block */
	uint32_t peek[6];        /* float bits of peekLatestValue(CHANNELS[i]) */
	char block[BLOCK_SIZE];  /* first error block printed whole after the trip, NUL-terminated */
};

__attribute__((used)) volatile saf_report_t g_saf_report;

static char line_buf[BLOCK_SIZE];
static uint32_t line_len;
static bool in_block;
static printk_hook_fn_t console_hook;

static uint32_t snap_armed; /* ack_seq to snapshot for, 0: none */

static void publish_block()
{
	g_saf_report.blocks = g_saf_report.blocks + 1U;
	if (snap_armed == 0U || g_saf_report.blocks < g_saf_report.blocks_at_trip + 2U) {
		return;
	}
	for (uint32_t i = 0; i < line_len; i++) {
		g_saf_report.block[i] = line_buf[i];
	}
	g_saf_report.block[line_len] = '\0';
	g_saf_report.snap_seq = snap_armed;
	snap_armed = 0U;
}

/* Every printk character: a block starts at "SAFETY ERROR" and ends where the next one starts. */
static int capture(int c)
{
	static const char HEADER[] = "SAFETY ERROR";
	g_saf_report.chars = g_saf_report.chars + 1U;
	if (line_len < BLOCK_SIZE - 1U) {
		line_buf[line_len++] = (char)c;
	}
	/* The header just completed: the previous block, without this header, is whole. */
	if (line_len >= sizeof(HEADER) - 1U &&
	    memcmp(&line_buf[line_len - (sizeof(HEADER) - 1U)], HEADER, sizeof(HEADER) - 1U) == 0) {
		uint32_t start = line_len - (sizeof(HEADER) - 1U);
		if (in_block) {
			line_len = start;
			publish_block();
		}
		in_block = true;
		memcpy(line_buf, HEADER, sizeof(HEADER) - 1U);
		line_len = sizeof(HEADER) - 1U;
	} else if (!in_block && c == '\n') {
		line_len = 0;
	}
	return console_hook != nullptr ? console_hook(c) : c;
}

static void set_thresholds(sensor_t sensor, float32_t min, float32_t max)
{
	safety.setChannelThresholdMin(&sensor, &min, 1);
	safety.setChannelThresholdMax(&sensor, &max, 1);
}

static uint32_t error_bits()
{
	uint32_t bits = 0;
	for (uint32_t i = 0; i < 6U; i++) {
		if (safety.getChannelError(CHANNELS[i])) {
			bits |= 1U << i;
		}
	}
	return bits;
}

void task_acquire()
{
	spin.data.triggerAcquisition(ADC_1);
	spin.data.triggerAcquisition(ADC_2);
	g_saf_report.runs = g_saf_report.runs + 1U;
}

static void trip(uint32_t index)
{
	/* Wide thresholds clear every flag on the next watch of each channel. */
	for (uint32_t i = 0; i < 6U; i++) {
		set_thresholds(CHANNELS[i], -1.0e9f, 1.0e9f);
	}
	uint32_t t0 = k_uptime_get_32();
	while (error_bits() != 0U && k_uptime_get_32() - t0 < TRIP_TIMEOUT_MS) {
		k_msleep(10);
	}
	/* No reading is above 1e9: the channel is out of range whatever its pin reads. */
	set_thresholds(CHANNELS[index], 1.0e9f, 2.0e9f);
	t0 = k_uptime_get_32();
	while (error_bits() != (1U << index) && k_uptime_get_32() - t0 < TRIP_TIMEOUT_MS) {
		k_msleep(10);
	}
	g_saf_report.errors = error_bits();
	g_saf_report.tripped = g_saf_report.errors == (1U << index) ? 1U : 0U;
	g_saf_report.blocks_at_trip = g_saf_report.blocks;
}

void loop_background_task()
{
	if (g_saf_report.cmd_seq != g_saf_report.ack_seq) {
		uint32_t index = g_saf_report.cmd;
		uint32_t seq = g_saf_report.cmd_seq;
		if (index < 6U) {
			trip(index);
		}
		g_saf_report.ack_seq = seq;
		snap_armed = index < 6U ? seq : 0U;
	}
	for (uint32_t i = 0; i < 6U; i++) {
		float32_t v = shield.sensors.peekLatestValue(CHANNELS[i]);
		uint32_t u;
		memcpy(&u, &v, sizeof(u));
		g_saf_report.peek[i] = u;
	}
	task.suspendBackgroundMs(10);
}

int main(void)
{
	g_saf_report.layout = REPORT_LAYOUT;
	console_hook = __printk_get_hook();
	__printk_hook_install(capture);

	int32_t rc = 0;
	spin.data.configureTriggerSource(ADC_1, TRIG_SOFTWARE);
	spin.data.configureTriggerSource(ADC_2, TRIG_SOFTWARE);
	for (uint32_t i = 0; i < 6U; i++) {
		rc |= shield.sensors.enableSensor(CHANNELS[i], CHANNEL_ADCS[i]) != 0 ? 1 : 0;
		set_thresholds(CHANNELS[i], -1.0e9f, 1.0e9f);
	}
	safety.setChannelWatch(CHANNELS, 6);
	rc |= spin.data.start() != 0 ? 2 : 0;
	rc |= task.createCritical(task_acquire, TASK_PERIOD_US, source_tim6) != 0 ? 4 : 0;
	g_saf_report.rc = rc;
	if (rc == 0) {
		task.startCritical(false);
	}

	g_saf_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
