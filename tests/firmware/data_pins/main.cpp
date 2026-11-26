/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-PAD test firmware: DataAPI pin-to-ADC resolution on every Spin pin (Core-staging#50).
 *
 * The host picks a mode over SWD (req_mode, then req_seq); the background task then configures acquisition, one
 * mode per boot:
 *  - mode 1, default ADC: spin.data.enableAcquisition(pin) on every pin 1..59, bit 0 of accept[pin] set when it
 *    returns 0.
 *  - mode 2, every ADC: spin.data.enableAcquisition(pin, adc) on every pin 1..59 and ADC 1..5 (pin-major order),
 *    bit adc of accept[pin] set when it returns 0.
 * Then spin.data.start(), and every 10 ms each ADC that has channels is software-triggered and
 * spin.data.peekLatestValue(pin) of every pin is stored in peek[pin] (float bits; NO_VALUE when the DataAPI
 * cannot resolve the pin). The host reads which channel each ADC converts from its SQR registers.
 * No PWM, no shield: safe on a bare Spin (the SPOC CUT); the pins are only read.
 */
#include <stdint.h>
#include <string.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"
#include "adc.h"

static constexpr uint32_t REPORT_MAGIC = 0x50414431U; /* "PAD1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint8_t LAST_PIN = 59U;
static constexpr uint8_t ADCS = 5U;

struct pad_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t req_mode;   /* host: 1 default ADC, 2 every ADC */
	uint32_t req_seq;    /* host: incremented after req_mode */
	uint32_t ack_seq;    /* firmware: req_seq once configured */
	int32_t start_rc;    /* spin.data.start() */
	uint32_t triggers;   /* trigger/peek rounds since start */
	uint32_t accept[LAST_PIN + 1]; /* per pin: mode 1 bit 0, mode 2 bit adc */
	uint32_t peek[LAST_PIN + 1];   /* per pin: float bits of peekLatestValue(pin) */
};

__attribute__((used)) volatile pad_report_t g_pad_report;

static uint32_t float_bits(float32_t f)
{
	uint32_t u;
	memcpy(&u, &f, sizeof(u));
	return u;
}

static void configure(uint32_t mode)
{
	for (uint8_t pin = 1; pin <= LAST_PIN; pin++) {
		uint32_t bits = 0U;
		if (mode == 1U) {
			if (spin.data.enableAcquisition(pin) == 0) {
				bits = 1U;
			}
		} else {
			for (uint8_t adc = 1; adc <= ADCS; adc++) {
				if (spin.data.enableAcquisition(pin, (adc_t)adc) == 0) {
					bits |= 1U << adc;
				}
			}
		}
		g_pad_report.accept[pin] = bits;
	}
	g_pad_report.start_rc = spin.data.start();
}

void loop_background_task()
{
	if (g_pad_report.req_seq != g_pad_report.ack_seq && g_pad_report.ack_seq == 0U) {
		configure(g_pad_report.req_mode);
		g_pad_report.ack_seq = g_pad_report.req_seq;
	}
	if (g_pad_report.ack_seq == 0U || g_pad_report.start_rc != 0) {
		task.suspendBackgroundMs(10);
		return;
	}
	for (uint8_t adc = 1; adc <= ADCS; adc++) {
		/* An ADC without channels must not be triggered: its sequence length would be 0 - 1. */
		if (adc_get_enabled_channels_count(adc) > 0U) {
			spin.data.triggerAcquisition((adc_t)adc);
		}
	}
	task.suspendBackgroundMs(5);
	for (uint8_t pin = 1; pin <= LAST_PIN; pin++) {
		g_pad_report.peek[pin] = float_bits(spin.data.peekLatestValue(pin));
	}
	g_pad_report.triggers = g_pad_report.triggers + 1U;
	task.suspendBackgroundMs(5);
}

int main(void)
{
	g_pad_report.layout = REPORT_LAYOUT;
	g_pad_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
