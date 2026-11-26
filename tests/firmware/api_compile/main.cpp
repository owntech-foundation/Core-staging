/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief Build tier of the API regression suite: every public call of the user-facing API, compiled and linked.
 *
 * Never meant to run. Each public member function of spin.*, shield.*, task, communication.* and safety is called
 * once with plausible arguments inside a branch the compiler cannot remove (g_run is volatile and stays false), so
 * the build proves that every call exists, takes these argument types and links in this shield configuration.
 * Built in the none, twist and ownverter configurations, plus twist130 for shield.ngnd and spin.uart (envs APIC_* in
 * tests/firmware/pio_tests.ini); a call that does not exist in a configuration sits behind the guard its header
 * uses. If flashed,
 * main() returns at once: nothing is driven.
 *
 * A call a configuration declares but cannot compile or link is a known failure: it is listed in known_failures.txt
 * and guarded out with `|| defined(APIC_WITH_KNOWN_FAILURES)`, so that
 * PLATFORMIO_BUILD_FLAGS=-DAPIC_WITH_KNOWN_FAILURES pio run -e APIC_<config> reproduces it.
 *
 * tools/api_calls.py coverage tests/firmware/api_compile/main.cpp checks that every public call in the headers is
 * called here: a new public call must be added to this file.
 */
#include <stdint.h>
#include <zephyr/kernel.h>

#include "SpinAPI.h"
#include "TaskAPI.h"
#ifdef CONFIG_OWNTECH_SHIELD_API
#include "ShieldAPI.h"
#endif
#ifdef CONFIG_OWNTECH_SAFETY_API
#include "SafetyAPI.h"
#endif
#ifdef CONFIG_OWNTECH_COMMUNICATION
#include "CommunicationAPI.h"
#endif

/* Stays false: the calls below are compiled and linked, never executed. */
__attribute__((used)) volatile bool g_run = false;

/* Results land here so that no getter is discarded. */
__attribute__((used)) volatile uint32_t g_sink;
__attribute__((used)) volatile float32_t g_fsink;

static void critical_task() {}
static void background_task() {}
static void period_callback() {}

static void spin_calls()
{
	/* spin.pwm (PwmHAL) */
	spin.pwm.initFixedFrequency(200000);
	spin.pwm.initVariableFrequency(200000, 50000);
	spin.pwm.initUnit(PWMA);
	spin.pwm.setModulation(PWMA, UpDwn);
	spin.pwm.setSwitchConvention(PWMA, PWMx1);
	spin.pwm.setDeadTime(PWMA, 100, 100);
	spin.pwm.setDutyCycle(PWMA, 0.5F);
	spin.pwm.setDutyCycleRaw(PWMA, 1000);
	spin.pwm.setPhaseShift(PWMB, 180);
	spin.pwm.setMode(PWMA, VOLTAGE_MODE);
	g_sink = spin.pwm.getMode(PWMA);
	spin.pwm.setEev(PWMA, EEV1);
	g_sink = spin.pwm.getEev(PWMA);
	g_sink = spin.pwm.getModulation(PWMA);
	g_sink = spin.pwm.getSwitchConvention(PWMA);
	g_sink = spin.pwm.getPeriod(PWMA);
	g_sink = spin.pwm.getPeriodMax(PWMA);
	g_sink = spin.pwm.getPeriodMin(PWMA);
	spin.pwm.setAdcTriggerPostScaler(PWMA, 1);
	spin.pwm.setAdcTrigger(PWMA, ADC_1);
	g_sink = spin.pwm.getAdcTrigger(PWMA);
	spin.pwm.enableAdcTrigger(PWMA);
	spin.pwm.disableAdcTrigger(PWMA);
	spin.pwm.setAdcTriggerInstant(PWMA, 0.06F);
	spin.pwm.setAdcEdgeTrigger(PWMA, EdgeTrigger_up);
	g_sink = spin.pwm.getAdcEdgeTrigger(PWMA);
	spin.pwm.setAdcDecimation(PWMA, 1);
	spin.pwm.configurePeriodEvnt(TIMA, 10, period_callback);
	spin.pwm.setPeriodEvntRep(TIMA, 10);
	g_sink = spin.pwm.getPeriodEvntRep(TIMA);
	spin.pwm.enablePeriodEvnt(TIMA);
	spin.pwm.disablePeriodEvnt(TIMA);
	g_sink = spin.pwm.getPeriodUs(PWMA);
	spin.pwm.setFrequency(100000);
	g_sink = spin.pwm.getFrequencyMin(PWMA);
	g_sink = spin.pwm.getFrequencyMax(PWMA);
	g_sink = spin.pwm.getResolutionPs(PWMA);
	spin.pwm.startDualOutput(PWMA);
	spin.pwm.stopDualOutput(PWMA);
	spin.pwm.startSingleOutput(PWMC, TIMING_OUTPUT1);
	spin.pwm.stopSingleOutput(PWMC, TIMING_OUTPUT1);
	spin.pwm.initBurstMode();
	spin.pwm.setBurstMode(1, 4);
	spin.pwm.startBurstMode();
	spin.pwm.stopBurstMode();
	spin.pwm.deInitBurstMode();

	/* spin.data (DataAPI) */
	uint32_t count = 0;
	uint8_t valid = 0;
	g_sink = spin.data.enableAcquisition(35, ADC_2);
	spin.data.configureTriggerSource(ADC_2, TRIG_PWM);
	spin.data.configureDiscontinuousMode(ADC_2, 1);
	spin.data.setConversionParametersLinear(35, 2.0F, -1.0F);
	spin.data.setConversionParametersNtcThermistor(35, 10000.0F, 3450.0F, 10000.0F, 298.15F);
	g_fsink = spin.data.getConversionParameterValue(35, gain);
	g_sink = spin.data.getConversionParameterType(35);
	g_sink = spin.data.storeConversionParametersInMemory(35);
	g_sink = spin.data.retrieveConversionParametersFromMemory(35);
	g_sink = spin.data.start();
	g_sink = spin.data.started();
	spin.data.triggerAcquisition(ADC_2);
	g_sink = spin.data.getRawValues(35, count)[0];
	g_fsink = spin.data.getValues(35, count)[0];
	g_fsink = spin.data.peekLatestValue(35);
	g_fsink = spin.data.getLatestValue(35, &valid);
	g_fsink = spin.data.convertValue(35, 2048);
	g_sink = spin.data.stop();

	/* spin.metaData (MetaDataAPI) */
	char text[SPIN_SERIAL_LEN + 1] = {0};
	uint8_t major = 0, minor = 0, rev = 0;
	uint8_t extra[METADATA_EXTRA_MAX_LEN] = {0};
	g_sink = spin.metaData.setSpinSerialNumber("SPIN-0001", 9);
	g_sink = spin.metaData.getSpinSerialNumber(text, sizeof(text));
	g_sink = spin.metaData.setShieldSerialNumber("TWIST-0001", 10);
	g_sink = spin.metaData.getShieldSerialNumber(text, sizeof(text));
	g_sink = spin.metaData.setSpinVersion(1, 2, 0);
	g_sink = spin.metaData.getSpinVersion(&major, &minor, &rev);
	g_sink = spin.metaData.setShieldVersion(1, 4, 2);
	g_sink = spin.metaData.getShieldVersion(&major, &minor, &rev);
	g_sink = spin.metaData.setSpinPassword("secret", 6);
	g_sink = spin.metaData.getSpinPassword(text, sizeof(text));
	g_sink = spin.metaData.setShieldPassword("secret", 6);
	g_sink = spin.metaData.getShieldPassword(text, sizeof(text));
	g_sink = spin.metaData.setExtraData(0, extra, sizeof(extra));
	g_sink = spin.metaData.getExtraData(0, extra, sizeof(extra));
	g_sink = spin.metaData.clearAllMetaData();

#ifdef CONFIG_OWNTECH_GPIO_API
	/* spin.gpio (GpioHAL) */
	spin.gpio.configurePin(PC6, OUTPUT);
	spin.gpio.setPin(PC6);
	spin.gpio.resetPin(PC6);
	spin.gpio.togglePin(PC6);
	spin.gpio.writePin(PC6, 1);
	g_sink = spin.gpio.readPin(PC7);
#endif

	/* spin.led (LedHAL) */
	spin.led.turnOn();
	spin.led.turnOff();
	spin.led.toggle();

	/* spin.dac (DacHAL) */
	spin.dac.initConstValue(2);
	spin.dac.setConstValue(2, 1, 2048);
	spin.dac.slopeCompensation(3, 1.4F, 0.7F);
	spin.dac.currentModeInit(3, TIMA);

	/* spin.comp (CompHAL) */
	spin.comp.initialize(1);

	/* spin.timer (TimerHAL) */
	spin.timer.startLogIncrementalEncoder(TIMER4);
	g_sink = spin.timer.getIncrementalEncoderValue(TIMER4);

#ifdef CONFIG_OWNTECH_UART_API
	/* spin.uart (UartHAL) */
	spin.uart.usart1Init();
	spin.uart.usart1WriteChar('A');
	g_sink = spin.uart.usart1ReadChar();
	spin.uart.usart1SwapRxTx();
#endif
}

static void task_calls()
{
	g_sink = task.createCritical(critical_task, 100, source_hrtim);
	task.startCritical(true);
	task.stopCritical();
#ifdef CONFIG_OWNTECH_TASK_ENABLE_ASYNCHRONOUS_TASKS
	int8_t background = task.createBackground(background_task);
	task.startBackground(background);
	task.suspendBackgroundMs(100);
	task.suspendBackgroundUs(100);
	task.stopBackground(background);
#endif
}

#ifdef CONFIG_OWNTECH_SHIELD_API
static void shield_calls()
{
	/* shield.power (PowerAPI) */
	shield.power.initBuck(LEG1, VOLTAGE_MODE);
	shield.power.initBoost(LEG2);
	shield.power.initMode(ALL, PWMx1, VOLTAGE_MODE);
	shield.power.setDutyCycle(LEG1, 0.5F);
	shield.power.setDutyCycleRaw(LEG1, 1000);
	shield.power.setDutyCycleMin(LEG1, 0.1F);
	shield.power.setDutyCycleMax(LEG1, 0.9F);
	shield.power.setDutyCycleMinRaw(LEG1, 100);
	shield.power.setDutyCycleMaxRaw(LEG1, 20000);
	g_fsink = shield.power.getDutyCycleMax(LEG1);
	g_sink = shield.power.getDutyCycleMaxRaw(LEG1);
	g_fsink = shield.power.getDutyCycleMin(LEG1);
	g_sink = shield.power.getDutyCycleMinRaw(LEG1);
	g_sink = shield.power.getPeriod(LEG1);
	shield.power.setTriggerValue(LEG1, 0.06F);
	shield.power.setPhaseShift(LEG2, 180);
	shield.power.setSlopeCompensation(LEG1, 1.4F, 0.7F);
	shield.power.setDeadTime(LEG1, 100, 100);
	shield.power.setAdcDecim(LEG1, 1);
	shield.power.setFrequency(200000);
	g_sink = shield.power.getFrequency();
	g_sink = shield.power.getFrequencyMin();
#if defined(CONFIG_SHIELD_TWIST) || defined(APIC_WITH_KNOWN_FAILURES)
	/* KNOWN FAILURE (known_failures.txt): declared in Power.h for every shield, defined in Power.cpp only for the
	 * Twist, so the link fails on any other shield. Remove the guard once fixed. */
	shield.power.connectCapacitor(ALL);
	shield.power.disconnectCapacitor(ALL);
#endif
	shield.power.connectDriver(LEG1);
	shield.power.disconnectDriver(LEG1);
	shield.power.start(ALL);
	shield.power.stop(ALL);

	/* shield.sensors (SensorsAPI) */
	uint32_t count = 0;
	uint8_t valid = 0;
	shield.sensors.enableDefaultSensors();
	g_sink = shield.sensors.enableSensor(V1_LOW, ADC_1);
	g_sink = shield.sensors.getRawValues(V1_LOW, count)[0];
	g_fsink = shield.sensors.getValues(V1_LOW, count)[0];
	g_fsink = shield.sensors.peekLatestValue(V1_LOW);
	g_fsink = shield.sensors.getLatestValue(V1_LOW, &valid);
	g_fsink = shield.sensors.convertRawValue(V1_LOW, 2048);
	shield.sensors.setConversionParametersLinear(V1_LOW, 0.03F, -1.0F);
	shield.sensors.setConversionParametersNtcThermistor(V1_LOW, 10000.0F, 3450.0F, 10000.0F, 298.15F);
	g_fsink = shield.sensors.retrieveStoredParameterValue(V1_LOW, gain);
	g_sink = shield.sensors.retrieveStoredConversionType(V1_LOW);
	g_sink = shield.sensors.storeParametersInMemory(V1_LOW);
	g_sink = shield.sensors.retrieveParametersFromMemory(V1_LOW);
#ifdef CONFIG_SHIELD_TWIST
	shield.sensors.enableDefaultTwistSensors();
	shield.sensors.setTwistSensorsUserCalibrationFactors();
	shield.sensors.triggerTwistTempMeas(TEMP_SENSOR_1);
#endif
#ifdef CONFIG_SHIELD_OWNVERTER
	shield.sensors.enableDefaultOwnverterSensors();
	shield.sensors.setOwnverterTempMeas(TEMP_1);
#endif

#ifdef CONFIG_OWNTECH_NGND_DRIVER
	/* shield.ngnd (NgndHAL) */
	shield.ngnd.turnOn();
	shield.ngnd.turnOff();
#endif
}
#endif /* CONFIG_OWNTECH_SHIELD_API */

#ifdef CONFIG_OWNTECH_SAFETY_API
static void safety_calls()
{
	sensor_t watched[] = {V1_LOW, I1_LOW};
	float32_t maxima[] = {30.0F, 8.0F};
	float32_t minima[] = {0.0F, -8.0F};
	safety.initShield();
	safety.initShield(watched, 2);
	g_sink = safety.setChannelWatch(watched, 2);
	g_sink = safety.unsetChannelWatch(watched, 2);
	g_sink = safety.getChannelWatch(V1_LOW);
	safety.setChannelReaction(Open_Circuit);
	g_sink = safety.getChannelReaction();
	g_sink = safety.setChannelThresholdMax(watched, maxima, 2);
	g_sink = safety.setChannelThresholdMin(watched, minima, 2);
	g_fsink = safety.getChannelThresholdMax(V1_LOW);
	g_fsink = safety.getChannelThresholdMin(V1_LOW);
	g_sink = safety.getChannelError(V1_LOW);
	safety.enableSafetyApi();
	safety.disableSafetyApi();
	g_sink = safety.storeThreshold(V1_LOW);
	g_sink = safety.retrieveThreshold(V1_LOW);
}
#endif

#ifdef CONFIG_OWNTECH_COMMUNICATION
static uint8_t tx_buffer[16];
static uint8_t rx_buffer[16];
static void rs485_received() {}

static void communication_calls()
{
#ifdef CONFIG_OWNTECH_COMMUNICATION_ENABLE_ANALOG
	communication.analog.init();
	communication.analog.setAnalogCommValue(2048);
	g_fsink = communication.analog.getAnalogCommValue();
#endif
#ifdef CONFIG_OWNTECH_COMMUNICATION_ENABLE_CAN
	g_sink = communication.can.getCanNodeAddr();
	communication.can.setCanNodeAddr(2);
#ifdef CONFIG_THINGSET_CAN_CONTROL_REPORTING
	g_sink = communication.can.getCtrlEnable();
	g_fsink = communication.can.getCtrlReference();
	g_fsink = communication.can.getStartStopState();
	g_sink = communication.can.getControlPeriod();
	communication.can.setCtrlEnable(true);
	communication.can.setCtrlReference(12.0F);
	communication.can.startSlaveDevice();
	communication.can.stopSlaveDevice();
	communication.can.setControlPeriod(100);
#endif
#ifdef CONFIG_THINGSET_SUBSET_LIVE_METRICS
	g_sink = communication.can.getBroadcastEnable();
	g_sink = communication.can.getBroadcastPeriod();
	communication.can.setBroadcastEnable(true);
	communication.can.setBroadcastPeriod(1);
#endif
#endif
#ifdef CONFIG_OWNTECH_COMMUNICATION_ENABLE_RS485
	communication.rs485.configure(tx_buffer, rx_buffer, sizeof(tx_buffer), rs485_received, SPEED_10M);
	communication.rs485.configureCustom(tx_buffer, rx_buffer, sizeof(tx_buffer), rs485_received, 115200, false);
	communication.rs485.turnOnCommunication();
	communication.rs485.startTransmission();
	communication.rs485.turnOffCommunication();
#endif
#ifdef CONFIG_OWNTECH_COMMUNICATION_ENABLE_SYNC
	communication.sync.initMaster();
	communication.sync.initSlave();
#endif
}
#endif /* CONFIG_OWNTECH_COMMUNICATION */

int main(void)
{
	if (g_run) {
		spin_calls();
		task_calls();
#ifdef CONFIG_OWNTECH_SHIELD_API
		shield_calls();
#endif
#ifdef CONFIG_OWNTECH_SAFETY_API
		safety_calls();
#endif
#ifdef CONFIG_OWNTECH_COMMUNICATION
		communication_calls();
#endif
	}
	return 0;
}
