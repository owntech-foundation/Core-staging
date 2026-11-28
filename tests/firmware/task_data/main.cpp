/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-TKD test firmware: the TASK + DATA module of the API regression suite (Core-staging#39, decision #35).
 *
 * One firmware for the module, host-commanded steps, many checks per flash (the PWM module's pattern). The firmware
 * holds no test logic: it is a step executor over `task` (TaskAPI) and `spin.data` (DataAPI). The host
 * (tests/l0/target_task_data/run.py) writes a step's arguments into g_tkd_args, then (sequence << 8) | step into
 * g_tkd_cmd, over SWD. Background task 0 (this executor) runs the step, puts the return values in g_tkd_report.ret[]
 * and acknowledges (ack = sequence, busy = 0). The host judges each step from the returns and from the ADC and NVIC
 * registers it reads over SWD itself.
 *
 * The critical tasks of this firmware make NO kernel call: the critical task runs in an interrupt above the kernel
 * (source_tim6 is a zero-latency interrupt, IRQ_ZERO_LATENCY), where a kernel call races the kernel's own data
 * (k_uptime_get_32() there faulted the board, L0-ADD). The executor stamps the run counter against the DWT cycle
 * counter instead.
 *
 * Background tasks 1 and 2 (created by steps, up to CONFIG_OWNTECH_TASK_MAX_ASYNCHRONOUS_TASKS) count their loops,
 * record their period (DWT) and priority, and suspend themselves with suspendBackgroundMs or suspendBackgroundUs.
 *
 * Pins: 34 (PA6, ADC2 IN3) is driven by DAC2 channel 1, the stimulus; 35 (PC4, ADC2 IN5) floats; 51 (PA2, ADC1
 * IN3) and 52 (PA3, ADC1 IN4) carry the NVS round trip (no shield uses them). The HRTIM runs at 200 kHz with unit A
 * started when a step asks for it (PA8/PA9 toggle): a bare Spin only (SPOC CUT).
 */
#include <stdint.h>
#include <string.h>
#include <soc.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/sys_heap.h>

#include "SpinAPI.h"
#include "TaskAPI.h"
#include "nvs_storage.h"

static constexpr uint32_t REPORT_MAGIC = 0x544B4431U; /* "TKD1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t N_RET = 16U;
static constexpr uint32_t N_BG = 3U;
static constexpr uint32_t BOOT_MAGIC = 0xB0071234U;

/* Steps: the host's tests/l0/target_task_data/run.py holds the same numbers (STEP). */
enum step_t : uint32_t {
	S_NONE = 0,
	/* Task */
	S_CREATE_CRIT = 1,   /* a0 source (0 hrtim, 1 tim6), a1 period us, a2 function (0 counter, 1 nullptr, 2 data) */
	S_START_CRIT = 2,    /* a0 manage data acquisition (0/1): ret0 spin.data.started() after */
	S_STOP_CRIT = 3,
	S_CREATE_BG = 4,     /* a0 routine (1, 2): ret0 createBackground() (int8) */
	S_START_BG = 5,      /* a0 task number */
	S_STOP_BG = 6,       /* a0 task number */
	S_BG_CONFIG = 7,     /* a0 routine (1, 2), a1 0 suspendBackgroundMs / 1 suspendBackgroundUs, a2 duration */
	S_BG_RESET = 8,      /* zero the routines' period statistics */
	S_MASK = 9,          /* a0 ms with the kernel's irq_lock() held: ret0 critical runs meanwhile, ret1 BASEPRI */
	S_PWM = 10,          /* a0 0: setFrequency(200 kHz) + initUnit(PWMA); 1: + ADC trigger on ADC2, start PWMA */
	/* Data */
	S_ENABLE = 20,       /* a0 pin, a1 adc (0 default): ret0 enableAcquisition() */
	S_TRIG_SRC = 21,     /* a0 adc, a1 0 TRIG_SOFTWARE / 1 TRIG_PWM */
	S_DISC = 22,         /* a0 adc, a1 count */
	S_START = 23,        /* ret0 start() */
	S_STOP = 24,         /* ret0 stop() */
	S_STARTED = 25,      /* ret0 started() */
	S_TRIGGER = 26,      /* a0 adc, a1 times (50 us apart) */
	S_RAW = 27,          /* a0 pin: ret0 n, ret1 first, ret2 last, ret3 min, ret4 max, ret5 pointer != nullptr */
	S_VALUES = 28,       /* a0 pin: ret0 n, ret1 first, ret2 last (float bits), ret3 values not gain*k+offset, ret4 ptr */
	S_PEEK = 29,         /* a0 pin: ret0 float bits */
	S_LATEST = 30,       /* a0 pin, a1 pass dataValid (0/1): ret0 float bits, ret1 dataValid (0xFF: not passed) */
	S_CONVERT = 31,      /* a0 pin, a1 raw: ret0 float bits */
	S_SET_LIN = 32,      /* a0 pin, a1 gain bits, a2 offset bits */
	S_SET_NTC = 33,      /* a0 pin, a1 r0, a2 b, a3 rdiv, a4 t0 (float bits) */
	S_GET_PARAM = 34,    /* a0 pin: ret0..5 getConversionParameterValue(gain, offset, r0, b, rdiv, t0), ret6 type */
	S_STORE = 35,        /* a0 pin: ret0 storeConversionParametersInMemory() (int8) */
	S_RETRIEVE = 36,     /* a0 pin: ret0 retrieveConversionParametersFromMemory() (int8) */
	S_NVS_DELETE = 37,   /* a0 adc, a1 channel: delete the ADC_CALIBRATION entry (clean-up), ret0 rc */
	S_DAC = 38,          /* a0 level: DAC2 channel 1 on PA6 (pin 34) */
	S_HEAP_PROBE = 39,   /* a0 repeats: ret0 heap bytes allocated before, ret1 after the hot-path calls, ret2 calls */
	S_CRIT_DATA = 40,    /* ret[] what the data critical task saw (see crit_data_task) */
	S_NVS_INFO = 41,     /* ret0 version in NVS, ret1 current version, ret2 free space (int32) */
};

struct bg_stats_t {
	uint32_t loops;      /* loops since start */
	uint32_t n;          /* periods since S_BG_RESET */
	uint32_t sum_us;
	uint32_t min_us;
	uint32_t max_us;
	int32_t priority;    /* k_thread_priority_get() of the routine's thread */
};

struct tkd_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t boot_count;
	uint32_t ack;            /* sequence of the last step done */
	uint32_t busy;           /* 1 while a step runs */
	uint32_t step;           /* last step run */
	uint32_t err;            /* 1: unknown step or argument out of range */
	uint32_t cycles;         /* DWT cycles of the step's call(s) */
	uint32_t ret[N_RET];
	uint32_t crit_runs;      /* critical task runs (counter or data task) */
	uint32_t stamp_runs;     /* crit_runs at the executor's last stamp */
	uint32_t stamp_cyc;      /* DWT at the executor's last stamp */
	uint32_t stamp_ms;       /* k_uptime_get_32() at the executor's last stamp (alive) */
	uint32_t core_hz;        /* SystemCoreClock: DWT cycles per second */
	bg_stats_t bg[N_BG];     /* [1], [2]: the routines; [0] unused */
	/* Data critical task (S_CREATE_CRIT a2 = 2), written in the interrupt */
	uint32_t cd_values_n;    /* getValues(35) values in total */
	uint32_t cd_raw_n;       /* unused (getRawValues in the critical task: L0-ADD) */
	uint32_t cd_latest;      /* float bits of getLatestValue(34, &dv) */
	uint32_t cd_dv_ok;       /* runs with dataValid == DATA_IS_OK */
	uint32_t cd_peek;        /* float bits of peekLatestValue(34) */
	uint32_t cd_convert;     /* float bits of convertValue(35, 1000) */
	uint32_t cd_last_value;  /* float bits of the last getValues(35) value */
};

__attribute__((used)) volatile tkd_report_t g_tkd_report;
__attribute__((used)) volatile uint32_t g_tkd_cmd;
__attribute__((used)) volatile uint32_t g_tkd_args[6];
static __noinit uint32_t boot_magic;
static __noinit uint32_t boot_count;

extern struct k_heap _system_heap;

static volatile uint32_t bg_mode[N_BG];      /* 0 Ms, 1 Us */
static volatile uint32_t bg_duration[N_BG];
static volatile uint32_t bg_reset[N_BG];
static bool dac_ready = false;

static float32_t u2f(uint32_t u)
{
	float32_t f;
	memcpy(&f, &u, sizeof(f));
	return f;
}

static uint32_t f2u(float32_t f)
{
	uint32_t u;
	memcpy(&u, &f, sizeof(u));
	return u;
}

/* ---------------------------------------------------------------- critical tasks: no kernel call in here */

static void crit_counter_task()
{
	g_tkd_report.crit_runs = g_tkd_report.crit_runs + 1U;
}

/* The DataAPI hot-path calls a control task makes, in the critical task (Core-staging#56: none may allocate). */
static void crit_data_task()
{
	uint32_t n = 0;
	float32_t *v = spin.data.getValues(35, n);
	g_tkd_report.cd_values_n = g_tkd_report.cd_values_n + n;
	if (n > 0U && v != nullptr) {
		g_tkd_report.cd_last_value = f2u(v[n - 1U]);
	}
	/* getRawValues() in the critical task is L0-ADD's; here pin 34 is read with getLatestValue(), which
	 * consumes its buffer (DataAPI.h: do not mix the two on one pin). */
	g_tkd_report.cd_peek = f2u(spin.data.peekLatestValue(34));
	uint8_t dv = 0xFF;
	g_tkd_report.cd_latest = f2u(spin.data.getLatestValue(34, &dv));
	if (dv == DATA_IS_OK) {
		g_tkd_report.cd_dv_ok = g_tkd_report.cd_dv_ok + 1U;
	}
	g_tkd_report.cd_convert = f2u(spin.data.convertValue(35, 1000));
	g_tkd_report.crit_runs = g_tkd_report.crit_runs + 1U;
}

/* ---------------------------------------------------------------- background routines 1 and 2 */

static void bg_routine(uint32_t i)
{
	static uint32_t last[N_BG];
	uint32_t now = DWT->CYCCNT;
	volatile bg_stats_t *s = &g_tkd_report.bg[i];
	if (bg_reset[i] != 0U) {
		bg_reset[i] = 0U;
		s->n = 0U;
		s->sum_us = 0U;
		s->min_us = 0xFFFFFFFFU;
		s->max_us = 0U;
	} else if (s->loops > 0U) {
		uint32_t us = (now - last[i]) / (SystemCoreClock / 1000000U);
		s->n = s->n + 1U;
		s->sum_us = s->sum_us + us;
		if (us < s->min_us) {
			s->min_us = us;
		}
		if (us > s->max_us) {
			s->max_us = us;
		}
	}
	last[i] = now;
	s->loops = s->loops + 1U;
	s->priority = k_thread_priority_get(k_current_get());
	if (bg_mode[i] == 1U) {
		task.suspendBackgroundUs(bg_duration[i]);
	} else {
		task.suspendBackgroundMs(bg_duration[i]);
	}
}

static void bg_routine_1()
{
	bg_routine(1);
}

static void bg_routine_2()
{
	bg_routine(2);
}

/* ---------------------------------------------------------------- steps */

static void hot_path_calls(uint32_t repeats)
{
	for (uint32_t k = 0; k < repeats; k++) {
		uint32_t n = 0;
		uint8_t dv = 0;
		spin.data.triggerAcquisition(ADC_2);
		k_busy_wait(20);
		(void)spin.data.getRawValues(34, n);
		(void)spin.data.getValues(35, n);
		(void)spin.data.peekLatestValue(34);
		(void)spin.data.getLatestValue(34, &dv);
		(void)spin.data.getLatestValue(35);
		(void)spin.data.convertValue(35, (uint16_t)k);
	}
}

static uint32_t heap_allocated()
{
	struct sys_memory_stats st = {};
	sys_heap_runtime_stats_get(&_system_heap.heap, &st);
	return (uint32_t)st.allocated_bytes;
}

static bool run_step(uint32_t step, const uint32_t *a)
{
	volatile uint32_t *r = g_tkd_report.ret;
	for (uint32_t k = 0; k < N_RET; k++) {
		r[k] = 0U;
	}
	uint32_t cyc0 = DWT->CYCCNT;
	switch (step) {
	case S_CREATE_CRIT: {
		if (a[0] > 1U || a[2] > 2U) return false;
		task_function_t fn = a[2] == 0U ? crit_counter_task : (a[2] == 1U ? nullptr : crit_data_task);
		r[0] = (uint32_t)(int32_t)task.createCritical(fn, a[1], a[0] == 1U ? source_tim6 : source_hrtim);
		break;
	}
	case S_START_CRIT:
		task.startCritical(a[0] != 0U);
		r[0] = spin.data.started() ? 1U : 0U;
		break;
	case S_STOP_CRIT:
		task.stopCritical();
		break;
	case S_CREATE_BG:
		if (a[0] < 1U || a[0] > 2U) return false;
		r[0] = (uint32_t)(int32_t)task.createBackground(a[0] == 1U ? bg_routine_1 : bg_routine_2);
		break;
	case S_START_BG:
		task.startBackground((uint8_t)a[0]);
		break;
	case S_STOP_BG:
		task.stopBackground((uint8_t)a[0]);
		break;
	case S_BG_CONFIG:
		if (a[0] < 1U || a[0] > 2U) return false;
		bg_mode[a[0]] = a[1];
		bg_duration[a[0]] = a[2];
		bg_reset[a[0]] = 1U;
		break;
	case S_BG_RESET:
		for (uint32_t i = 1; i < N_BG; i++) {
			bg_reset[i] = 1U;
		}
		break;
	case S_MASK: {
		unsigned int key = irq_lock();
		uint32_t basepri = __get_BASEPRI();
		uint32_t runs0 = g_tkd_report.crit_runs;
		uint32_t start = DWT->CYCCNT;
		uint32_t span = a[0] * (SystemCoreClock / 1000U);
		while (DWT->CYCCNT - start < span) {
		}
		r[0] = g_tkd_report.crit_runs - runs0;
		irq_unlock(key);
		r[1] = basepri;
		break;
	}
	case S_PWM:
		spin.pwm.setFrequency(200000);
		spin.pwm.initUnit(PWMA);
		if (a[0] == 1U) {
			spin.pwm.setDutyCycle(PWMA, 0.5);
			spin.pwm.setAdcDecimation(PWMA, 1);
			spin.pwm.setAdcTrigger(PWMA, ADC_2);
			spin.pwm.enableAdcTrigger(PWMA);
			spin.pwm.setAdcTriggerInstant(PWMA, 0.06);
			spin.pwm.startDualOutput(PWMA);
		}
		break;
	case S_ENABLE:
		if (a[1] > 5U) return false;
		r[0] = (uint32_t)(int32_t)spin.data.enableAcquisition((uint8_t)a[0], (adc_t)a[1]);
		break;
	case S_TRIG_SRC:
		spin.data.configureTriggerSource((adc_t)a[0], a[1] ? TRIG_PWM : TRIG_SOFTWARE);
		break;
	case S_DISC:
		spin.data.configureDiscontinuousMode((adc_t)a[0], a[1]);
		break;
	case S_START:
		r[0] = (uint32_t)(int32_t)spin.data.start();
		break;
	case S_STOP:
		r[0] = (uint32_t)(int32_t)spin.data.stop();
		break;
	case S_STARTED:
		r[0] = spin.data.started() ? 1U : 0U;
		break;
	case S_TRIGGER:
		for (uint32_t k = 0; k < a[1]; k++) {
			spin.data.triggerAcquisition((adc_t)a[0]);
			k_busy_wait(50);
		}
		break;
	case S_RAW: {
		uint32_t n = 0;
		uint16_t *p = spin.data.getRawValues((uint8_t)a[0], n);
		r[0] = n;
		r[5] = p != nullptr ? 1U : 0U;
		if (n > 0U && p != nullptr) {
			uint32_t mn = 0xFFFFU, mx = 0U;
			for (uint32_t k = 0; k < n; k++) {
				mn = p[k] < mn ? p[k] : mn;
				mx = p[k] > mx ? p[k] : mx;
			}
			r[1] = p[0];
			r[2] = p[n - 1U];
			r[3] = mn;
			r[4] = mx;
		}
		break;
	}
	case S_VALUES: {
		uint32_t n = 0;
		float32_t *p = spin.data.getValues((uint8_t)a[0], n);
		r[0] = n;
		r[4] = p != nullptr ? 1U : 0U;
		if (n > 0U && p != nullptr) {
			float32_t g = spin.data.getConversionParameterValue((uint8_t)a[0], gain);
			float32_t o = spin.data.getConversionParameterValue((uint8_t)a[0], offset);
			uint32_t bad = 0U;
			for (uint32_t k = 0; k < n; k++) {
				/* A converted value must be gain * raw + offset for an integer raw in 0..4095. */
				float32_t raw = (p[k] - o) / g;
				int32_t ri = (int32_t)(raw + 0.5f);
				if (ri < 0 || ri > 4095 || (raw - (float32_t)ri) > 1e-3f || ((float32_t)ri - raw) > 1e-3f) {
					bad++;
				}
			}
			r[1] = f2u(p[0]);
			r[2] = f2u(p[n - 1U]);
			r[3] = bad;
		}
		break;
	}
	case S_PEEK:
		r[0] = f2u(spin.data.peekLatestValue((uint8_t)a[0]));
		break;
	case S_LATEST:
		if (a[1] != 0U) {
			uint8_t dv = 0xEE;
			r[0] = f2u(spin.data.getLatestValue((uint8_t)a[0], &dv));
			r[1] = dv;
		} else {
			r[0] = f2u(spin.data.getLatestValue((uint8_t)a[0]));
			r[1] = 0xFFU;
		}
		break;
	case S_CONVERT:
		r[0] = f2u(spin.data.convertValue((uint8_t)a[0], (uint16_t)a[1]));
		break;
	case S_SET_LIN:
		spin.data.setConversionParametersLinear((uint8_t)a[0], u2f(a[1]), u2f(a[2]));
		break;
	case S_SET_NTC:
		spin.data.setConversionParametersNtcThermistor((uint8_t)a[0], u2f(a[1]), u2f(a[2]), u2f(a[3]),
								u2f(a[4]));
		break;
	case S_GET_PARAM: {
		static const parameter_t P[6] = {gain, offset, r0, b, rdiv, t0};
		for (uint32_t k = 0; k < 6U; k++) {
			r[k] = f2u(spin.data.getConversionParameterValue((uint8_t)a[0], P[k]));
		}
		r[6] = (uint32_t)(int32_t)spin.data.getConversionParameterType((uint8_t)a[0]);
		break;
	}
	case S_STORE:
		r[0] = (uint32_t)(int32_t)spin.data.storeConversionParametersInMemory((uint8_t)a[0]);
		break;
	case S_RETRIEVE:
		r[0] = (uint32_t)(int32_t)spin.data.retrieveConversionParametersFromMemory((uint8_t)a[0]);
		break;
	case S_NVS_DELETE: {
		/* Clean-up only: the entry storeConversionParametersInMemory() wrote (data_conversion.cpp channel_ID).
		 * A zero-length write deletes an NVS entry. */
		uint16_t id = ADC_CALIBRATION | (uint16_t)((a[0] & 0x0FU) << 4) | (uint16_t)(a[1] & 0x0FU);
		r[0] = (uint32_t)(int32_t)nvs_storage_store_data(id, nullptr, 0);
		break;
	}
	case S_DAC:
		if (!dac_ready) {
			spin.dac.initConstValue(2);
			dac_ready = true;
		}
		spin.dac.setConstValue(2, 1, a[0]);
		break;
	case S_HEAP_PROBE:
		r[0] = heap_allocated();
		hot_path_calls(a[0]);
		r[1] = heap_allocated();
		r[2] = a[0] * 6U;
		break;
	case S_CRIT_DATA:
		r[0] = g_tkd_report.cd_values_n;
		r[1] = g_tkd_report.cd_raw_n;
		r[2] = g_tkd_report.cd_latest;
		r[3] = g_tkd_report.cd_dv_ok;
		r[4] = g_tkd_report.cd_peek;
		r[5] = g_tkd_report.cd_convert;
		r[6] = g_tkd_report.cd_last_value;
		r[7] = g_tkd_report.crit_runs;
		break;
	case S_NVS_INFO:
		r[0] = nvs_storage_get_version_in_nvs();
		r[1] = nvs_storage_get_current_version();
		r[2] = (uint32_t)nvs_storage_get_free_space();
		{
			/* Raw driver write of a 32-byte entry (the size of a linear conversion entry) under an ID no
			 * caller uses, then its deletion: tells a DataAPI store failure from an NVS failure. */
			static uint8_t probe[32] = {1, 2, 3, 4};
			r[3] = (uint32_t)(int32_t)nvs_storage_store_data(ADC_CALIBRATION | 0xFFU, probe, sizeof(probe));
			r[4] = (uint32_t)(int32_t)nvs_storage_store_data(ADC_CALIBRATION | 0xFFU, nullptr, 0);
		}
		break;
	default:
		return false;
	}
	g_tkd_report.cycles = DWT->CYCCNT - cyc0;
	return true;
}

void loop_background_task()
{
	uint32_t cmd = g_tkd_cmd;
	uint32_t seq = cmd >> 8;
	if (seq != 0U && seq != g_tkd_report.ack) {
		uint32_t step = cmd & 0xFFU;
		uint32_t a[6] = {g_tkd_args[0], g_tkd_args[1], g_tkd_args[2], g_tkd_args[3], g_tkd_args[4],
				 g_tkd_args[5]};
		g_tkd_report.busy = 1U;
		g_tkd_report.step = step;
		bool ok = run_step(step, a);
		g_tkd_report.err = ok ? 0U : 1U;
		g_tkd_report.ack = seq;
		g_tkd_report.busy = 0U;
	}
	unsigned int key = irq_lock();
	g_tkd_report.stamp_runs = g_tkd_report.crit_runs;
	g_tkd_report.stamp_cyc = DWT->CYCCNT;
	irq_unlock(key);
	g_tkd_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(5);
}

int main(void)
{
	if (boot_magic != BOOT_MAGIC) {
		boot_magic = BOOT_MAGIC;
		boot_count = 0U;
	}
	boot_count = boot_count + 1U;
	g_tkd_report.layout = REPORT_LAYOUT;
	g_tkd_report.boot_count = boot_count;
	g_tkd_report.core_hz = SystemCoreClock;
	for (uint32_t i = 0; i < N_BG; i++) {
		bg_duration[i] = 10U;
		g_tkd_report.bg[i].min_us = 0xFFFFFFFFU;
	}

	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0U;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	g_tkd_report.magic = REPORT_MAGIC;
	int8_t background = task.createBackground(loop_background_task);
	task.startBackground((uint8_t)background);
	return 0;
}
