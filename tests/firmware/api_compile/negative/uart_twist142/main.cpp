/*
 * SPDX-License-Identifier: LGPL-2.1
 * Copyright (c) 2026 OWNTECH TECHNOLOGIES
 *
 * @file main.cpp
 * @brief Negative build check: spin.uart on a shield without usart1 (see app.conf). Expected to fail to build;
 * tools/build_api_negative.sh checks that the log carries the configure-time message. Never flashed.
 */
#include <zephyr/kernel.h>
#include "SpinAPI.h"

__attribute__((used)) volatile bool g_run = false;

int main()
{
	if (g_run) {
		spin.uart.usart1Init();
	}
	return 0;
}
