# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# RD68884 on the IIsiA7 Mini: timing (doc/boards.md). The pins come from
# tools/board_pins.py; the core clock is the MMCM's, derived from clk100.

create_clock -period 10.000 -name clk100 [get_ports clk100]

# The MC68030's bus is asynchronous to the core (FPU 10.4), and the BIU is built
# for it (doc/bus-timing.md): the strobes pass synchronisers, the stale guard
# is set asynchronously, and A, R/W and the write data are sampled only once
# the synchronised strobes say they are stable. No path from a bus pin into a
# register is a timed one.
set bus_in [get_ports {A_3v3[*] fc_3v3[*] as_3v3_n ds_3v3_n rw_3v3_n reset_3v3_n D_3v3[*]}]
set bus_out [get_ports {D_3v3[*] dsack_3v3_n[*] halt_3v3_n}]
set_false_path -from $bus_in -to [all_registers]

# What is timed is what the BIU does combinationally, from the pins: START
# falling releases the data (specification 16) and negates DSACK (21), and
# DSACK is released within 40 ns at 20 MHz (22) -- 30, 30 and 40 ns at the
# IIsi's 20 MHz, of which the board's switches and traces take a few.
set_max_delay -datapath_only 25.000 -from $bus_in -to $bus_out

# The data and DSACK come from registers loaded on the same edge (doc/bus-timing.md,
# specification 20): bound their clock-to-pin so that they stay together.
set_max_delay -datapath_only 15.000 -from [all_registers] -to $bus_out

# The LEDs are for people.
set_false_path -to [get_ports {user_leds[*]}]

# Synchronisers: both ranks of every rd68884_sync. The one rank that ends
# DSACK's negation (rtl/rd68884_biu.sv, DSACK_NEGATE) samples the stale guard
# as the first rank of its synchroniser does, from the same reset: synthesis
# merges the two, so it is that rank.
set_property ASYNC_REG TRUE [get_cells -hier -filter {NAME =~ *u_sync*/rank0_reg* || NAME =~ *u_sync*/q_reg*}]
