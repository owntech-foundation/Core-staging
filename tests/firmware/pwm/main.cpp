/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-PWM test firmware: the PWM module of the API regression suite (Core-staging#38, decision #35).
 *
 * One firmware per module, host-commanded steps, many checks per flash. The firmware holds no test logic: it is
 * a step executor over spin.pwm (PwmHAL). The host (tests/l0/target_pwm/run.py) writes the step's arguments into
 * g_pwm_args and then (sequence << 8) | step into g_pwm_cmd, over SWD. The background task runs the step, samples
 * the twelve HRTIM output pins (GPIO IDR, 4096 samples with a random gap: the fraction of time each pin is high),
 * then acknowledges (ack = sequence, busy = 0). The host judges each step from the HRTIM registers it reads over
 * SWD itself, the step's return values and pin fractions in g_pwm_report, and the scope through the SPOC MUX.
 *
 * A fresh boot has touched nothing in the HRTIM: steps that must come before initUnit() (setModulation,
 * setDeadTime, setMode, initFixedFrequency, ...) are issued first, and the host resets the CUT between profiles.
 *
 * Starts HRTIM outputs on PA8-PA11, PB12-PB15, PC6-PC9 and drives PA6 as a marker: a bare Spin only (SPOC CUT).
 */
#include <stdint.h>
#include <string.h>
#include <soc.h>
#include <stm32_ll_bus.h>
#include <stm32_ll_gpio.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x50574D31U; /* "PWM1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t N_RET = 16U;
static constexpr uint32_t N_PINS = 12U;
static constexpr uint32_t N_SAMPLES = 4096U;
static constexpr uint32_t BOOT_MAGIC = 0xB0071234U;

/* Steps: the host's tests/l0/target_pwm/run.py holds the same numbers (STEP). */
enum step_t : uint32_t {
	S_NONE = 0,
	S_SET_FREQUENCY = 1,      /* a0 Hz */
	S_INIT_UNIT = 2,          /* a0 unit */
	S_GETTERS = 3,            /* a0 unit: ret[] */
	S_DUTY = 4,               /* a0 unit, a1 float bits */
	S_DUTY_RAW = 5,           /* a0 unit, a1 raw */
	S_START_DUAL = 6,         /* a0 unit */
	S_STOP_DUAL = 7,          /* a0 unit */
	S_START_SINGLE = 8,       /* a0 unit, a1 output (0: OUTPUT1, 1: OUTPUT2) */
	S_STOP_SINGLE = 9,        /* a0 unit, a1 output */
	S_PHASE = 10,             /* a0 unit, a1 degrees (int32) */
	S_MODULATION = 11,        /* a0 unit, a1 0 left-aligned, 1 up-down */
	S_CONVENTION = 12,        /* a0 unit, a1 1 PWMx1, 2 PWMx2 */
	S_DEAD_TIME = 13,         /* a0 unit, a1 rise ns, a2 fall ns */
	S_MODE = 14,              /* a0 unit, a1 0 voltage, 1 current */
	S_EEV = 15,               /* a0 unit, a1 1..9 */
	S_ADC_EDGE = 16,          /* a0 unit, a1 0 up, 1 down, 2 both */
	S_INIT_FIXED = 17,        /* a0 Hz */
	S_INIT_VARIABLE = 18,     /* a0 initial Hz, a1 minimal Hz */
	S_ADC_TRIGGER = 20,       /* a0 unit, a1 ADC 1..5 */
	S_ADC_DECIMATION = 21,    /* a0 unit, a1 n */
	S_ADC_POSTSCALER = 22,    /* a0 unit, a1 ratio */
	S_ADC_ENABLE = 23,        /* a0 unit */
	S_ADC_DISABLE = 24,       /* a0 unit */
	S_ADC_INSTANT = 25,       /* a0 unit, a1 float bits */
	S_PE_CONFIGURE = 30,      /* a0 timer index (0..5 TIMA..TIMF, 6 master), a1 repetition */
	S_PE_ENABLE = 31,         /* a0 timer index */
	S_PE_DISABLE = 32,        /* a0 timer index */
	S_PE_SET_REP = 33,        /* a0 timer index, a1 repetition */
	S_BM_INIT = 40,
	S_BM_SET = 41,            /* a0 idle periods, a1 total periods */
	S_BM_START = 42,
	S_BM_STOP = 43,
	S_BM_DEINIT = 44,
	S_SAMPLE = 50,            /* pin fractions only */
	S_LAT_DUTY = 60,          /* a0 unit, a1 float bits: PA6 high, setDutyCycle, 40 us, PA6 low */
	S_LAT_STOP = 61,          /* a0 unit: PA6 high, stopDualOutput, 40 us, PA6 low */
	S_LAT_START = 62,         /* a0 unit: PA6 high, startDualOutput, 40 us, PA6 low */
};

struct pwm_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t boot_count;
	uint32_t ack;            /* sequence of the last step done */
	uint32_t busy;           /* 1 while a step runs */
	uint32_t step;           /* last step run */
	uint32_t err;            /* 1: unknown step or argument out of range */
	uint32_t cycles;         /* DWT cycles of the API call(s) of the step */
	uint32_t ret[N_RET];     /* S_GETTERS return values; S_PE_CONFIGURE, S_PE_SET_REP: ret[0] getPeriodEvntRep */
	uint32_t high[N_PINS];   /* samples high, PA8 PA9 PA10 PA11 PB12 PB13 PB14 PB15 PC8 PC9 PC6 PC7 */
	uint32_t samples;        /* samples per pin */
	uint32_t cb_count;       /* period-event callbacks */
	uint32_t stamp_ms;       /* k_uptime_get_32() of the background task (alive) */
};

__attribute__((used)) volatile pwm_report_t g_pwm_report;
__attribute__((used)) volatile uint32_t g_pwm_cmd;
__attribute__((used)) volatile uint32_t g_pwm_args[4];
static __noinit uint32_t boot_magic;
static __noinit uint32_t boot_count;

static const hrtim_tu_t TIMERS[7] = {TIMA, TIMB, TIMC, TIMD, TIME, TIMF, MSTR};

/* Pins of units A..F, high side then low side (hrtim.c switch_H_pin / switch_L_pin). */
static GPIO_TypeDef *const PIN_PORT[N_PINS] = {GPIOA, GPIOA, GPIOA, GPIOA, GPIOB, GPIOB,
					       GPIOB, GPIOB, GPIOC, GPIOC, GPIOC, GPIOC};
static const uint16_t PIN_BIT[N_PINS] = {8, 9, 10, 11, 12, 13, 14, 15, 8, 9, 6, 7};


static float32_t u2f(uint32_t u)
{
	float32_t f;
	memcpy(&f, &u, sizeof(f));
	return f;
}

static void period_event_callback()
{
	g_pwm_report.cb_count = g_pwm_report.cb_count + 1U;
}

static inline void marker(bool high)
{
	if (high) {
		LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_6);
	} else {
		LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_6);
	}
}

/* Fraction of time each output pin is high: GPIO IDR sampled with a pseudo-random gap, so the samples do not lock
 * to the PWM period. */
static void sample_pins()
{
	uint32_t high[N_PINS] = {0};
	uint32_t lcg = 12345U;
	for (uint32_t n = 0; n < N_SAMPLES; n++) {
		uint32_t a = GPIOA->IDR;
		uint32_t b = GPIOB->IDR;
		uint32_t c = GPIOC->IDR;
		for (uint32_t p = 0; p < N_PINS; p++) {
			uint32_t idr = PIN_PORT[p] == GPIOA ? a : (PIN_PORT[p] == GPIOB ? b : c);
			high[p] += (idr >> PIN_BIT[p]) & 1U;
		}
		lcg = lcg * 1664525U + 1013904223U;
		for (volatile uint32_t d = (lcg >> 24) & 0x7FU; d > 0; d--) {
		}
	}
	for (uint32_t p = 0; p < N_PINS; p++) {
		g_pwm_report.high[p] = high[p];
	}
	g_pwm_report.samples = N_SAMPLES;
}

static hrtim_output_number_t output_of(uint32_t a)
{
	return a == 0U ? TIMING_OUTPUT1 : TIMING_OUTPUT2;
}

static void getters(hrtim_tu_number_t u)
{
	volatile uint32_t *r = g_pwm_report.ret;
	r[0] = spin.pwm.getPeriod(u);
	r[1] = spin.pwm.getPeriodMax(u);
	r[2] = spin.pwm.getPeriodMin(u);
	r[3] = spin.pwm.getPeriodUs(u);
	r[4] = spin.pwm.getFrequencyMin(u);
	r[5] = spin.pwm.getFrequencyMax(u);
	r[6] = spin.pwm.getResolutionPs(u);
	hrtim_cnt_t m = spin.pwm.getModulation(u);
	r[7] = m == Lft_aligned ? 0U : (m == UpDwn ? 1U : 0xFFU);
	hrtim_switch_convention_t c = spin.pwm.getSwitchConvention(u);
	r[8] = c == PWMx1 ? 1U : (c == PWMx2 ? 2U : 0xFFU);
	hrtim_pwm_mode_t mode = spin.pwm.getMode(u);
	r[9] = mode == VOLTAGE_MODE ? 0U : (mode == CURRENT_MODE ? 1U : 0xFFU);
	static const hrtim_external_trigger_t EEVS[9] = {EEV1, EEV2, EEV3, EEV4, EEV5, EEV6, EEV7, EEV8, EEV9};
	hrtim_external_trigger_t e = spin.pwm.getEev(u);
	r[10] = 0xFFU;
	for (uint32_t k = 0; k < 9U; k++) {
		if (e == EEVS[k]) {
			r[10] = k + 1U;
		}
	}
	r[11] = (uint32_t)(int32_t)spin.pwm.getAdcTrigger(u); /* adc_t: -1 unknown, 1..5 */
	hrtim_adc_edgetrigger_t g = spin.pwm.getAdcEdgeTrigger(u);
	r[12] = g == EdgeTrigger_up ? 0U : (g == EdgeTrigger_down ? 1U : (g == EdgeTrigger_Both ? 2U : 0xFFU));
	r[13] = spin.pwm.getPeriodEvntRep(TIMERS[u]);
}

static bool run_step(uint32_t step, const uint32_t *a)
{
	hrtim_tu_number_t u = (hrtim_tu_number_t)a[0];
	bool unit_ok = a[0] <= 5U;
	uint32_t t0 = DWT->CYCCNT;
	switch (step) {
	case S_SET_FREQUENCY:
		spin.pwm.setFrequency(a[0]);
		break;
	case S_INIT_UNIT:
		if (!unit_ok) return false;
		spin.pwm.initUnit(u);
		break;
	case S_GETTERS:
		if (!unit_ok) return false;
		getters(u);
		break;
	case S_DUTY:
		if (!unit_ok) return false;
		spin.pwm.setDutyCycle(u, u2f(a[1]));
		break;
	case S_DUTY_RAW:
		if (!unit_ok) return false;
		spin.pwm.setDutyCycleRaw(u, (uint16_t)a[1]);
		break;
	case S_START_DUAL:
		if (!unit_ok) return false;
		spin.pwm.startDualOutput(u);
		break;
	case S_STOP_DUAL:
		if (!unit_ok) return false;
		spin.pwm.stopDualOutput(u);
		break;
	case S_START_SINGLE:
		if (!unit_ok) return false;
		spin.pwm.startSingleOutput(u, output_of(a[1]));
		break;
	case S_STOP_SINGLE:
		if (!unit_ok) return false;
		spin.pwm.stopSingleOutput(u, output_of(a[1]));
		break;
	case S_PHASE:
		if (!unit_ok) return false;
		spin.pwm.setPhaseShift(u, (int16_t)(int32_t)a[1]);
		break;
	case S_MODULATION:
		if (!unit_ok) return false;
		spin.pwm.setModulation(u, a[1] ? UpDwn : Lft_aligned);
		break;
	case S_CONVENTION:
		if (!unit_ok) return false;
		spin.pwm.setSwitchConvention(u, a[1] == 2U ? PWMx2 : PWMx1);
		break;
	case S_DEAD_TIME:
		if (!unit_ok) return false;
		spin.pwm.setDeadTime(u, (uint16_t)a[1], (uint16_t)a[2]);
		break;
	case S_MODE:
		if (!unit_ok) return false;
		spin.pwm.setMode(u, a[1] ? CURRENT_MODE : VOLTAGE_MODE);
		break;
	case S_EEV: {
		static const hrtim_external_trigger_t EEVS[9] = {EEV1, EEV2, EEV3, EEV4, EEV5,
								 EEV6, EEV7, EEV8, EEV9};
		if (!unit_ok || a[1] < 1U || a[1] > 9U) return false;
		spin.pwm.setEev(u, EEVS[a[1] - 1U]);
		break;
	}
	case S_ADC_EDGE:
		if (!unit_ok) return false;
		spin.pwm.setAdcEdgeTrigger(u, a[1] == 0U ? EdgeTrigger_up
					     : (a[1] == 1U ? EdgeTrigger_down : EdgeTrigger_Both));
		break;
	case S_INIT_FIXED:
		spin.pwm.initFixedFrequency(a[0]);
		break;
	case S_INIT_VARIABLE:
		spin.pwm.initVariableFrequency(a[0], a[1]);
		break;
	case S_ADC_TRIGGER:
		if (!unit_ok || a[1] < 1U || a[1] > 5U) return false;
		spin.pwm.setAdcTrigger(u, (adc_t)a[1]);
		break;
	case S_ADC_DECIMATION:
		if (!unit_ok) return false;
		spin.pwm.setAdcDecimation(u, a[1]);
		break;
	case S_ADC_POSTSCALER:
		if (!unit_ok) return false;
		spin.pwm.setAdcTriggerPostScaler(u, a[1]);
		break;
	case S_ADC_ENABLE:
		if (!unit_ok) return false;
		spin.pwm.enableAdcTrigger(u);
		break;
	case S_ADC_DISABLE:
		if (!unit_ok) return false;
		spin.pwm.disableAdcTrigger(u);
		break;
	case S_ADC_INSTANT:
		if (!unit_ok) return false;
		spin.pwm.setAdcTriggerInstant(u, u2f(a[1]));
		break;
	case S_PE_CONFIGURE:
		if (a[0] > 6U) return false;
		g_pwm_report.cb_count = 0U;
		spin.pwm.configurePeriodEvnt(TIMERS[a[0]], a[1], period_event_callback);
		g_pwm_report.ret[0] = spin.pwm.getPeriodEvntRep(TIMERS[a[0]]);
		break;
	case S_PE_ENABLE:
		if (a[0] > 6U) return false;
		/* Acknowledge first: if the interrupt never clears its flag the background never runs again. */
		g_pwm_report.ack = g_pwm_cmd >> 8;
		g_pwm_report.busy = 0U;
		spin.pwm.enablePeriodEvnt(TIMERS[a[0]]);
		break;
	case S_PE_DISABLE:
		if (a[0] > 6U) return false;
		spin.pwm.disablePeriodEvnt(TIMERS[a[0]]);
		break;
	case S_PE_SET_REP:
		if (a[0] > 6U) return false;
		spin.pwm.setPeriodEvntRep(TIMERS[a[0]], a[1]);
		g_pwm_report.ret[0] = spin.pwm.getPeriodEvntRep(TIMERS[a[0]]);
		break;
	case S_BM_INIT:
		spin.pwm.initBurstMode();
		break;
	case S_BM_SET:
		spin.pwm.setBurstMode((int)a[0], (int)a[1]);
		break;
	case S_BM_START:
		spin.pwm.startBurstMode();
		break;
	case S_BM_STOP:
		spin.pwm.stopBurstMode();
		break;
	case S_BM_DEINIT:
		spin.pwm.deInitBurstMode();
		break;
	case S_SAMPLE:
		break;
	case S_LAT_DUTY:
	case S_LAT_STOP:
	case S_LAT_START: {
		if (!unit_ok) return false;
		unsigned int key = irq_lock();
		marker(true);
		t0 = DWT->CYCCNT;
		if (step == S_LAT_DUTY) {
			spin.pwm.setDutyCycle(u, u2f(a[1]));
		} else if (step == S_LAT_STOP) {
			spin.pwm.stopDualOutput(u);
		} else {
			spin.pwm.startDualOutput(u);
		}
		g_pwm_report.cycles = DWT->CYCCNT - t0;
		irq_unlock(key);
		k_busy_wait(40);
		marker(false);
		return true;
	}
	default:
		return false;
	}
	g_pwm_report.cycles = DWT->CYCCNT - t0;
	return true;
}

void loop_background_task()
{
	uint32_t cmd = g_pwm_cmd;
	uint32_t seq = cmd >> 8;
	if (seq != 0U && seq != g_pwm_report.ack) {
		uint32_t step = cmd & 0xFFU;
		uint32_t a[4] = {g_pwm_args[0], g_pwm_args[1], g_pwm_args[2], g_pwm_args[3]};
		g_pwm_report.busy = 1U;
		g_pwm_report.step = step;
		bool ok = run_step(step, a);
		g_pwm_report.err = ok ? 0U : 1U;
		sample_pins();
		g_pwm_report.ack = seq;
		g_pwm_report.busy = 0U;
	}
	g_pwm_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(5);
}

int main(void)
{
	if (boot_magic != BOOT_MAGIC) {
		boot_magic = BOOT_MAGIC;
		boot_count = 0U;
	}
	boot_count = boot_count + 1U;
	g_pwm_report.layout = REPORT_LAYOUT;
	g_pwm_report.boot_count = boot_count;

	/* DWT cycle counter: cost of each step's call(s). */
	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0U;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	/* PA6: latency marker, low. The HRTIM pins stay untouched until a step initialises their unit. */
	LL_AHB2_GRP1_EnableClock(LL_AHB2_GRP1_PERIPH_GPIOA | LL_AHB2_GRP1_PERIPH_GPIOB | LL_AHB2_GRP1_PERIPH_GPIOC);
	LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_6);
	LL_GPIO_SetPinOutputType(GPIOA, LL_GPIO_PIN_6, LL_GPIO_OUTPUT_PUSHPULL);
	LL_GPIO_SetPinSpeed(GPIOA, LL_GPIO_PIN_6, LL_GPIO_SPEED_FREQ_VERY_HIGH);
	LL_GPIO_SetPinMode(GPIOA, LL_GPIO_PIN_6, LL_GPIO_MODE_OUTPUT);

	g_pwm_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
