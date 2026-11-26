/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief Negative build check: communication.can on Twist 1.3.0 (see app.conf). Expected to fail to build;
 * tools/build_api_negative.sh checks that the log carries the configure-time message. Never flashed.
 */
#include <stdint.h>
#include <zephyr/kernel.h>
#include "SpinAPI.h"
#include "ShieldAPI.h"
#include "CommunicationAPI.h"

__attribute__((used)) volatile bool g_run = false;
__attribute__((used)) volatile uint32_t g_sink;

int main()
{
	if (g_run) {
		g_sink = communication.can.getCanNodeAddr();
	}
	return 0;
}
