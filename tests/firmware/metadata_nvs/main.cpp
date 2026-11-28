/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-NVS test firmware: the MetaData / NVS module of the API regression suite (Core-staging#42, decision #35).
 *
 * A step executor, like tests/firmware/pwm: the host (tests/l0/target_metadata_nvs/run.py) writes a step's
 * arguments into g_nvs_args (byte arguments into g_nvs_in) and then (sequence << 8) | step into g_nvs_cmd, over
 * SWD. The background task calls the API and puts its return values in g_nvs_report.ret[] and the bytes a getter
 * wrote in g_nvs_report.out[] (pre-filled with 0xA5 before each getter, so the host sees how many it wrote), then
 * acknowledges (ack = sequence, busy = 0). The firmware holds no test logic and touches no NVS at boot: whatever
 * it reads comes from the host's steps, after a reset or a reflash.
 *
 * Calls: spin.metaData (15), spin.data store/retrieve of conversion parameters (pin 51 = PA2, ADC1 channel 3, not a
 * Twist sensor), shield.sensors store/retrieve (V_HIGH), nvs_storage_get_current_version / get_version_in_nvs /
 * get_free_space / clear_all_stored_data. Shield config twist (inherited) for shield.sensors; no PWM started, no
 * output driven: safe on a bare Spin.
 */
#include <stdint.h>
#include <string.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "ShieldAPI.h"
#include "TaskAPI.h"
#include "nvs_storage.h"

static constexpr uint32_t REPORT_MAGIC = 0x4E565331U; /* "NVS1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t N_RET = 8U;
static constexpr uint32_t N_BYTES = 32U;
static constexpr uint32_t BOOT_MAGIC = 0xB0074E56U;
static constexpr uint8_t FILL = 0xA5U;

/* Steps: the host's tests/l0/target_metadata_nvs/run.py holds the same numbers (STEP). */
enum step_t : uint32_t {
	S_NONE = 0,
	S_SET_SPIN_SERIAL = 1,    /* a0 size, bytes in g_nvs_in */
	S_GET_SPIN_SERIAL = 2,    /* a0 buffer size: ret0, out[] */
	S_SET_SHIELD_SERIAL = 3,
	S_GET_SHIELD_SERIAL = 4,
	S_SET_SPIN_VERSION = 5,   /* a0 major, a1 minor, a2 rev */
	S_GET_SPIN_VERSION = 6,   /* ret0, ret1..3 major minor rev (0xEE when not written) */
	S_SET_SHIELD_VERSION = 7,
	S_GET_SHIELD_VERSION = 8,
	S_SET_SPIN_PASSWORD = 9,
	S_GET_SPIN_PASSWORD = 10,
	S_SET_SHIELD_PASSWORD = 11,
	S_GET_SHIELD_PASSWORD = 12,
	S_SET_EXTRA = 13,         /* a0 index, a1 size, bytes in g_nvs_in */
	S_GET_EXTRA = 14,         /* a0 index, a1 buffer size */
	S_CLEAR_META = 15,
	S_NVS_INFO = 20,          /* ret0 current version, ret1 version in NVS, ret2 free space */
	S_NVS_CLEAR_ALL = 21,
	S_DATA_ENABLE = 30,       /* a0 pin, a1 ADC */
	S_DATA_SET_LINEAR = 31,   /* a0 pin, a1 gain (float bits), a2 offset (float bits) */
	S_DATA_STORE = 32,        /* a0 pin */
	S_DATA_RETRIEVE = 33,     /* a0 pin */
	S_DATA_GET = 34,          /* a0 pin: ret0 type, ret1 gain bits, ret2 offset bits */
	S_SENS_ENABLE = 40,       /* V_HIGH, a0 ADC */
	S_SENS_SET_LINEAR = 41,   /* a0 gain bits, a1 offset bits */
	S_SENS_STORE = 42,
	S_SENS_RETRIEVE = 43,
	S_SENS_GET = 44,          /* ret0 type, ret1 gain bits, ret2 offset bits */
};

struct nvs_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t boot_count;
	uint32_t ack;             /* sequence of the last step done */
	uint32_t busy;            /* 1 while a step runs */
	uint32_t step;            /* last step run */
	uint32_t err;             /* 1: unknown step or argument out of range */
	int32_t ret[N_RET];       /* return values (sign-extended) */
	uint8_t out[N_BYTES];     /* bytes written by a getter, FILL elsewhere */
	uint32_t stamp_ms;        /* k_uptime_get_32() of the background task (alive) */
};

__attribute__((used)) volatile nvs_report_t g_nvs_report;
__attribute__((used)) volatile uint32_t g_nvs_cmd;
__attribute__((used)) volatile uint32_t g_nvs_args[4];
__attribute__((used)) volatile uint8_t g_nvs_in[N_BYTES];
static __noinit uint32_t boot_magic;
static __noinit uint32_t boot_count;

static float32_t u2f(uint32_t u)
{
	float32_t f;
	memcpy(&f, &u, sizeof(f));
	return f;
}

static int32_t f2i(float32_t f)
{
	int32_t u;
	memcpy(&u, &f, sizeof(u));
	return u;
}

static void put_ret(uint32_t i, int32_t v)
{
	g_nvs_report.ret[i] = v;
}

static void publish(const uint8_t *buf)
{
	for (uint32_t i = 0; i < N_BYTES; i++) {
		g_nvs_report.out[i] = buf[i];
	}
}

static bool run_step(uint32_t step, const uint32_t *a)
{
	uint8_t in[N_BYTES];
	uint8_t buf[N_BYTES];
	for (uint32_t i = 0; i < N_BYTES; i++) {
		in[i] = g_nvs_in[i];
		buf[i] = FILL;
	}
	for (uint32_t i = 0; i < N_RET; i++) {
		put_ret(i, 0);
	}
	const char *s = reinterpret_cast<const char *>(in);
	char *b = reinterpret_cast<char *>(buf);

	switch (step) {
	case S_SET_SPIN_SERIAL:
	case S_SET_SHIELD_SERIAL:
	case S_SET_SPIN_PASSWORD:
	case S_SET_SHIELD_PASSWORD: {
		if (a[0] > N_BYTES) return false;
		uint8_t n = (uint8_t)a[0];
		int8_t rc = step == S_SET_SPIN_SERIAL     ? spin.metaData.setSpinSerialNumber(s, n)
			  : step == S_SET_SHIELD_SERIAL   ? spin.metaData.setShieldSerialNumber(s, n)
			  : step == S_SET_SPIN_PASSWORD   ? spin.metaData.setSpinPassword(s, n)
							  : spin.metaData.setShieldPassword(s, n);
		put_ret(0, rc);
		break;
	}
	case S_GET_SPIN_SERIAL:
	case S_GET_SHIELD_SERIAL:
	case S_GET_SPIN_PASSWORD:
	case S_GET_SHIELD_PASSWORD: {
		if (a[0] > N_BYTES) return false;
		uint8_t n = (uint8_t)a[0];
		int8_t rc = step == S_GET_SPIN_SERIAL     ? spin.metaData.getSpinSerialNumber(b, n)
			  : step == S_GET_SHIELD_SERIAL   ? spin.metaData.getShieldSerialNumber(b, n)
			  : step == S_GET_SPIN_PASSWORD   ? spin.metaData.getSpinPassword(b, n)
							  : spin.metaData.getShieldPassword(b, n);
		put_ret(0, rc);
		break;
	}
	case S_SET_SPIN_VERSION:
		put_ret(0, spin.metaData.setSpinVersion((uint8_t)a[0], (uint8_t)a[1], (uint8_t)a[2]));
		break;
	case S_SET_SHIELD_VERSION:
		put_ret(0, spin.metaData.setShieldVersion((uint8_t)a[0], (uint8_t)a[1], (uint8_t)a[2]));
		break;
	case S_GET_SPIN_VERSION:
	case S_GET_SHIELD_VERSION: {
		uint8_t v[3] = {0xEE, 0xEE, 0xEE};
		int8_t rc = step == S_GET_SPIN_VERSION ? spin.metaData.getSpinVersion(&v[0], &v[1], &v[2])
						       : spin.metaData.getShieldVersion(&v[0], &v[1], &v[2]);
		put_ret(0, rc);
		put_ret(1, v[0]);
		put_ret(2, v[1]);
		put_ret(3, v[2]);
		break;
	}
	case S_SET_EXTRA:
		if (a[1] > N_BYTES) return false;
		put_ret(0, spin.metaData.setExtraData((uint8_t)a[0], in, (uint8_t)a[1]));
		break;
	case S_GET_EXTRA:
		if (a[1] > N_BYTES) return false;
		put_ret(0, spin.metaData.getExtraData((uint8_t)a[0], buf, (uint8_t)a[1]));
		break;
	case S_CLEAR_META:
		put_ret(0, spin.metaData.clearAllMetaData());
		break;
	case S_NVS_INFO:
		put_ret(0, nvs_storage_get_current_version());
		put_ret(1, nvs_storage_get_version_in_nvs());
		put_ret(2, nvs_storage_get_free_space());
		break;
	case S_NVS_CLEAR_ALL:
		put_ret(0, nvs_storage_clear_all_stored_data());
		break;
	case S_DATA_ENABLE:
		put_ret(0, spin.data.enableAcquisition((uint8_t)a[0], (adc_t)a[1]));
		break;
	case S_DATA_SET_LINEAR:
		spin.data.setConversionParametersLinear((uint8_t)a[0], u2f(a[1]), u2f(a[2]));
		break;
	case S_DATA_STORE:
		put_ret(0, spin.data.storeConversionParametersInMemory((uint8_t)a[0]));
		break;
	case S_DATA_RETRIEVE:
		put_ret(0, spin.data.retrieveConversionParametersFromMemory((uint8_t)a[0]));
		break;
	case S_DATA_GET:
		put_ret(0, spin.data.getConversionParameterType((uint8_t)a[0]));
		put_ret(1, f2i(spin.data.getConversionParameterValue((uint8_t)a[0], gain)));
		put_ret(2, f2i(spin.data.getConversionParameterValue((uint8_t)a[0], offset)));
		break;
	case S_SENS_ENABLE:
		put_ret(0, shield.sensors.enableSensor(V_HIGH, (adc_t)a[0]));
		break;
	case S_SENS_SET_LINEAR:
		shield.sensors.setConversionParametersLinear(V_HIGH, u2f(a[0]), u2f(a[1]));
		break;
	case S_SENS_STORE:
		put_ret(0, shield.sensors.storeParametersInMemory(V_HIGH));
		break;
	case S_SENS_RETRIEVE:
		put_ret(0, shield.sensors.retrieveParametersFromMemory(V_HIGH));
		break;
	case S_SENS_GET:
		put_ret(0, shield.sensors.retrieveStoredConversionType(V_HIGH));
		put_ret(1, f2i(shield.sensors.retrieveStoredParameterValue(V_HIGH, gain)));
		put_ret(2, f2i(shield.sensors.retrieveStoredParameterValue(V_HIGH, offset)));
		break;
	default:
		return false;
	}
	publish(buf);
	return true;
}

void loop_background_task()
{
	uint32_t cmd = g_nvs_cmd;
	uint32_t seq = cmd >> 8;
	if (seq != 0U && seq != g_nvs_report.ack) {
		uint32_t step = cmd & 0xFFU;
		uint32_t a[4] = {g_nvs_args[0], g_nvs_args[1], g_nvs_args[2], g_nvs_args[3]};
		g_nvs_report.busy = 1U;
		g_nvs_report.step = step;
		bool ok = run_step(step, a);
		g_nvs_report.err = ok ? 0U : 1U;
		g_nvs_report.ack = seq;
		g_nvs_report.busy = 0U;
	}
	g_nvs_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(5);
}

int main(void)
{
	if (boot_magic != BOOT_MAGIC) {
		boot_magic = BOOT_MAGIC;
		boot_count = 0U;
	}
	boot_count = boot_count + 1U;
	g_nvs_report.layout = REPORT_LAYOUT;
	g_nvs_report.boot_count = boot_count;
	g_nvs_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
