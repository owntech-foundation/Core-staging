/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief L0-IOM test firmware: the IO module of the API regression suite (Core-staging#40, decision #35):
 *        spin.gpio, spin.led, spin.dac, spin.comp, spin.timer (encoder), spin.uart, and the analog
 *        communication bus's DAC (communication.analog, L0-DAC folded in).
 *
 * One firmware per module, host-commanded steps, many checks per flash (the pattern of tests/firmware/pwm). The
 * firmware holds no test logic: it is a step executor over the API. The host (tests/l0/target_io/run.py) writes the
 * step's arguments into g_io_args and then (sequence << 8) | step into g_io_cmd, over SWD. The background task runs
 * the step, puts return values in g_io_report.ret and acknowledges (ack = sequence, busy = 0). The host judges each
 * step from the peripheral registers it reads over SWD itself (GPIO, DAC, COMP, TIM, USART, VREFBUF), the return
 * values, and the scope through the SPOC MUX.
 *
 * S_GPIO_SWEEP is the one step that snapshots registers on the chip: for every Spin pin number 1..58 it calls
 * configurePin(n, OUTPUT), setPin(n), resetPin(n), configurePin(n, INPUT) and records the GPIO MODER/ODR words
 * between the calls (g_io_sweep), so that 58 pins cost one step. Spin pins 46 and 47 (PA13/PA14, SWD) are skipped:
 * driving them would cut the probe this test talks through.
 *
 * Drives GPIO outputs on many pins, the DACs on PA4/PA6 and USART1 on PB6/PB7: a bare Spin only (SPOC CUT), never
 * mounted on a power stage.
 */
#include <stdint.h>
#include <string.h>
#include <soc.h>
#include <zephyr/kernel.h>

#include "CommunicationAPI.h"
#include "SpinAPI.h"
#include "TaskAPI.h"

static constexpr uint32_t REPORT_MAGIC = 0x494F4D31U; /* "IOM1" */
static constexpr uint32_t REPORT_LAYOUT = 1U;
static constexpr uint32_t N_RET = 8U;
static constexpr uint32_t N_SPIN_PINS = 59U; /* index 0 unused, Spin pins 1..58 */
static constexpr uint32_t SWEEP_WORDS = 8U;
static constexpr uint32_t BOOT_MAGIC = 0xB0071234U;

/* Steps: the host's tests/l0/target_io/run.py holds the same numbers (STEP). */
enum step_t : uint32_t {
	S_NONE = 0,
	S_GPIO_CONFIG = 1,   /* a0 pin, a1 0 INPUT, 1 INPUT_PULLUP, 2 OUTPUT */
	S_GPIO_SET = 2,      /* a0 pin */
	S_GPIO_RESET = 3,    /* a0 pin */
	S_GPIO_TOGGLE = 4,   /* a0 pin */
	S_GPIO_WRITE = 5,    /* a0 pin, a1 value */
	S_GPIO_READ = 6,     /* a0 pin: ret[0] readPin */
	S_GPIO_SWEEP = 7,    /* every Spin pin number: g_io_sweep */
	S_LED_ON = 10,
	S_LED_OFF = 11,
	S_LED_TOGGLE = 12,
	S_DAC_INIT = 20,     /* a0 dac */
	S_DAC_SET = 21,      /* a0 dac, a1 channel, a2 value */
	S_DAC_CURRENT = 22,  /* a0 dac, a1 HRTIM timer 0..5 (TIMA..TIMF) */
	S_DAC_SLOPE = 23,    /* a0 dac, a1 peak volts (float bits), a2 low volts (float bits) */
	S_COMP_INIT = 30,    /* a0 comparator */
	S_TIMER_START = 40,  /* a0 3 or 4 */
	S_TIMER_GET = 41,    /* a0 3 or 4: ret[0] getIncrementalEncoderValue */
	S_QUAD = 42,         /* a0 pin A, a1 pin B, a2 cycles, a3 0 A leads, 1 B leads: quadrature on two GPIO outputs */
	S_UART_INIT = 50,
	S_UART_WRITE = 51,   /* a0 char, a1 count */
	S_UART_READ = 52,    /* ret[0] usart1ReadChar */
	S_UART_SWAP = 53,
	S_ANALOG_INIT = 60,
	S_ANALOG_SET = 61,   /* a0 level */
};

struct io_report_t {
	uint32_t magic;
	uint32_t layout;
	uint32_t boot_count;
	uint32_t ack;        /* sequence of the last step done */
	uint32_t busy;       /* 1 while a step runs */
	uint32_t step;       /* last step run */
	uint32_t err;        /* 1: unknown step or argument out of range */
	uint32_t cycles;     /* DWT cycles of the API call(s) of the step */
	uint32_t ret[N_RET];
	uint32_t stamp_ms;   /* k_uptime_get_32() of the background task (alive) */
};

__attribute__((used)) volatile io_report_t g_io_report;
__attribute__((used)) volatile uint32_t g_io_cmd;
__attribute__((used)) volatile uint32_t g_io_args[4];
/* Per Spin pin: MODER A, B, C, D after configurePin(OUTPUT); ODR A | B << 16 and C | D << 16 after setPin; the same
 * after resetPin. */
__attribute__((used)) volatile uint32_t g_io_sweep[N_SPIN_PINS][SWEEP_WORDS];
static __noinit uint32_t boot_magic;
static __noinit uint32_t boot_count;

static const hrtim_tu_t TIMERS[6] = {TIMA, TIMB, TIMC, TIMD, TIME, TIMF};

static float32_t u2f(uint32_t u)
{
	float32_t f;
	memcpy(&f, &u, sizeof(f));
	return f;
}

static gpio_flags_t flags_of(uint32_t a)
{
	return a == 2U ? OUTPUT : (a == 1U ? INPUT_PULLUP : INPUT);
}

static uint32_t odr_ab()
{
	return (GPIOA->ODR & 0xFFFFU) | (GPIOB->ODR << 16);
}

static uint32_t odr_cd()
{
	return (GPIOC->ODR & 0xFFFFU) | (GPIOD->ODR << 16);
}

static void gpio_sweep()
{
	for (uint32_t n = 1U; n < N_SPIN_PINS; n++) {
		volatile uint32_t *w = g_io_sweep[n];
		if (n == 46U || n == 47U) { /* PA13 SWDIO, PA14 SWCLK */
			for (uint32_t k = 0; k < SWEEP_WORDS; k++) {
				w[k] = 0xFFFFFFFFU;
			}
			continue;
		}
		spin.gpio.configurePin((uint8_t)n, OUTPUT);
		w[0] = GPIOA->MODER;
		w[1] = GPIOB->MODER;
		w[2] = GPIOC->MODER;
		w[3] = GPIOD->MODER;
		spin.gpio.setPin((uint8_t)n);
		w[4] = odr_ab();
		w[5] = odr_cd();
		spin.gpio.resetPin((uint8_t)n);
		w[6] = odr_ab();
		w[7] = odr_cd();
		spin.gpio.configurePin((uint8_t)n, INPUT);
	}
}

static timernumber_t timer_of(uint32_t a)
{
	return a == 3U ? TIMER3 : TIMER4;
}

static void quadrature(uint8_t pa, uint8_t pb, uint32_t cycles, bool b_leads)
{
	/* One cycle: (A, B) = 00 -> 10 -> 11 -> 01 -> 00 with A leading, the reverse order with B leading; 20 us per
	 * state, far above the input filter (fDTS/16, N = 5: ~0.5 us). */
	static const uint8_t SEQ[4][2] = {{1, 0}, {1, 1}, {0, 1}, {0, 0}};
	for (uint32_t c = 0; c < cycles; c++) {
		for (uint32_t s = 0; s < 4U; s++) {
			uint32_t k = b_leads ? 3U - ((s + 1U) & 3U) : s;
			spin.gpio.writePin(pa, SEQ[k][0]);
			spin.gpio.writePin(pb, SEQ[k][1]);
			k_busy_wait(20);
		}
	}
}

static bool run_step(uint32_t step, const uint32_t *a)
{
	uint8_t pin = (uint8_t)a[0];
	uint32_t t0 = DWT->CYCCNT;
	switch (step) {
	case S_GPIO_CONFIG:
		if (a[1] > 2U) return false;
		spin.gpio.configurePin(pin, flags_of(a[1]));
		break;
	case S_GPIO_SET:
		spin.gpio.setPin(pin);
		break;
	case S_GPIO_RESET:
		spin.gpio.resetPin(pin);
		break;
	case S_GPIO_TOGGLE:
		spin.gpio.togglePin(pin);
		break;
	case S_GPIO_WRITE:
		spin.gpio.writePin(pin, (uint8_t)a[1]);
		break;
	case S_GPIO_READ:
		g_io_report.ret[0] = spin.gpio.readPin(pin);
		break;
	case S_GPIO_SWEEP:
		gpio_sweep();
		break;
	case S_LED_ON:
		spin.led.turnOn();
		break;
	case S_LED_OFF:
		spin.led.turnOff();
		break;
	case S_LED_TOGGLE:
		spin.led.toggle();
		break;
	case S_DAC_INIT:
		spin.dac.initConstValue((uint8_t)a[0]);
		break;
	case S_DAC_SET:
		spin.dac.setConstValue((uint8_t)a[0], (uint8_t)a[1], a[2]);
		break;
	case S_DAC_CURRENT:
		if (a[1] > 5U) return false;
		spin.dac.currentModeInit((uint8_t)a[0], TIMERS[a[1]]);
		break;
	case S_DAC_SLOPE:
		spin.dac.slopeCompensation((uint8_t)a[0], u2f(a[1]), u2f(a[2]));
		break;
	case S_COMP_INIT:
		spin.comp.initialize((uint8_t)a[0]);
		break;
	case S_TIMER_START:
		if (a[0] != 3U && a[0] != 4U) return false;
		spin.timer.startLogIncrementalEncoder(timer_of(a[0]));
		break;
	case S_TIMER_GET:
		if (a[0] != 3U && a[0] != 4U) return false;
		g_io_report.ret[0] = spin.timer.getIncrementalEncoderValue(timer_of(a[0]));
		break;
	case S_QUAD:
		quadrature((uint8_t)a[0], (uint8_t)a[1], a[2], a[3] != 0U);
		break;
	case S_UART_INIT:
		spin.uart.usart1Init();
		break;
	case S_UART_WRITE:
		for (uint32_t k = 0; k < a[1]; k++) {
			spin.uart.usart1WriteChar((char)a[0]);
		}
		break;
	case S_UART_READ:
		g_io_report.ret[0] = (uint8_t)spin.uart.usart1ReadChar();
		break;
	case S_UART_SWAP:
		spin.uart.usart1SwapRxTx();
		break;
	case S_ANALOG_INIT:
		communication.analog.init();
		break;
	case S_ANALOG_SET:
		communication.analog.setAnalogCommValue(a[0]);
		break;
	default:
		return false;
	}
	g_io_report.cycles = DWT->CYCCNT - t0;
	return true;
}

void loop_background_task()
{
	uint32_t cmd = g_io_cmd;
	uint32_t seq = cmd >> 8;
	if (seq != 0U && seq != g_io_report.ack) {
		uint32_t step = cmd & 0xFFU;
		uint32_t a[4] = {g_io_args[0], g_io_args[1], g_io_args[2], g_io_args[3]};
		g_io_report.busy = 1U;
		g_io_report.step = step;
		bool ok = run_step(step, a);
		g_io_report.err = ok ? 0U : 1U;
		g_io_report.ack = seq;
		g_io_report.busy = 0U;
	}
	g_io_report.stamp_ms = k_uptime_get_32();
	task.suspendBackgroundMs(5);
}

int main(void)
{
	if (boot_magic != BOOT_MAGIC) {
		boot_magic = BOOT_MAGIC;
		boot_count = 0U;
	}
	boot_count = boot_count + 1U;
	g_io_report.layout = REPORT_LAYOUT;
	g_io_report.boot_count = boot_count;

	/* DWT cycle counter: cost of each step's call(s), recorded. */
	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
	DWT->CYCCNT = 0U;
	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

	/* Nothing else is touched before the host's first step: the boot state (VREFBUF, DAC2 at 2048) is checked as
	 * the kernel left it. */
	g_io_report.magic = REPORT_MAGIC;
	uint32_t background = task.createBackground(loop_background_task);
	task.startBackground(background);
	return 0;
}
