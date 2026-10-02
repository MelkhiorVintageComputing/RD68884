# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Quartus timing constraints. Mirrors scripts/rd68884.xdc.
#
# derive_clock_uncertainty is added explicitly because Quartus adds none by default
# where Vivado does; without it the two tools are not reporting the same thing.

create_clock -period 20.000 -name clk -waveform {0.000 10.000} [get_ports clk]
derive_clock_uncertainty

set_false_path -from [get_ports rst_n]
