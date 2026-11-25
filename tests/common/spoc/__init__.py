"""SPOC bench host side: board discovery and MUX routing.

thingset.py and mux_controller.py are copied unchanged from
owntech-foundation/SPOC_Software, branch SPOC_Test_Software, commit d02cd65
(PWM/Test_SPIN_API/lib/). Bench history: https://github.com/luizvilla/Core/blob/safety-hw/docs/safety/spoc_bench.md.
"""

# Firmware the bench runs, stamped into every SPOC report. SPOC_Software is private: ask the maintainers for a build.
CUT_FW = 'owntech-foundation/SPOC_Software Core-CUT 3a5df78'
MUX_FW = 'owntech-foundation/SPOC_Software mux-injector fe04fe3'
