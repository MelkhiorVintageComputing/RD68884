# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# RD68884 timing constraints.
#
# 20 ns, 50 MHz: the core clock, not the MC68881's CLK pin. The bus is
# asynchronous, and the core has to run at two to three times the bus clock to see
# the shortest strobe-negated gap (doc/architecture.md, "Clocking"); 50 MHz serves
# a 20 MHz bus. One clock edge, so every path has the whole period.

create_clock -period 20.000 -name clk -waveform {0.000 10.000} [get_ports clk]

# rst_n is asynchronous by construction and is not timed.
set_false_path -from [get_ports rst_n]
