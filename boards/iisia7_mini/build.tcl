# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# RD68884 on the IIsiA7 Mini: bitstream and flash image (doc/boards.md).
#
#   vivado -mode batch -source boards/iisia7_mini/build.tcl -tclargs \
#          <repo-root> <part> <clkout0-divide>
#
# Run from the build directory, which holds rtl.f (the RTL, absolute paths) and
# pins.xdc (tools/board_pins.py). Writes rd68884_iisia7.bit, .bin and .mcs, and
# the reports; exits non-zero if timing is not met or DRC finds an error.

set root   [lindex $argv 0]
set part   [lindex $argv 1]
set divide [lindex $argv 2]
set top    rd68884_iisia7_top
set name   rd68884_iisia7

puts "RD68884: IIsiA7 Mini, $part, core clock 1000 MHz / $divide"

set_msg_config -id {Synth 8-6901} -new_severity ERROR

set f [open rtl.f r]
set rtl [split [string trim [read $f]] "\n"]
close $f
read_verilog -sv $rtl
read_verilog -sv $root/boards/iisia7_mini/rd68884_iisia7_top.sv
read_xdc pins.xdc
read_xdc $root/boards/iisia7_mini/timing.xdc

synth_design -top $top -part $part -generic CLKOUT0_DIVIDE=$divide
opt_design
place_design
phys_opt_design
route_design

# The configuration, as the board's platform file has it (IIsiA7_Mini_pds.py):
# SPI x4 at 50 MHz, compressed, 3.3 V configuration banks.
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO      [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4         [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 50          [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE         [current_design]
set_property BITSTREAM.CONFIG.M2PIN PULLNONE         [current_design]
set_property BITSTREAM.CONFIG.M1PIN PULLNONE         [current_design]
set_property BITSTREAM.CONFIG.M0PIN PULLNONE         [current_design]
set_property BITSTREAM.CONFIG.USR_ACCESS TIMESTAMP   [current_design]
set_property BITSTREAM.GENERAL.CRC DISABLE           [current_design]
set_property CONFIG_VOLTAGE 3.3                      [current_design]
set_property CONFIG_MODE SPIx4                       [current_design]
set_property CFGBVS VCCO                             [current_design]
# Every pin the design does not use: three-stated, no pull. The PDS signals
# this FPU has no business with stay the motherboard's.
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLNONE     [current_design]
# The board's 74LVC1G125 (U7) holds HALT low until DONE rises; the design holds
# it from then on until the FPU is out of reset. Release the I/O (GTS, cycle 5)
# before DONE (6), so that there is no gap with HALT undriven between the two.
set_property BITSTREAM.STARTUP.DONE_CYCLE 6          [current_design]

report_utilization                  -file ${name}_utilization.rpt
report_timing_summary -max_paths 20 -file ${name}_timing.rpt
report_io                           -file ${name}_io.rpt
report_drc                          -file ${name}_drc.rpt
report_cdc                          -file ${name}_cdc.rpt
report_clocks                       -file ${name}_clocks.rpt
write_checkpoint -force ${name}_routed.dcp

# The verdict.
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set core [get_property PERIOD [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]]
set p [get_timing_paths -delay_type max -max_paths 1]
puts [format "RD68884: core clock %.3f ns (%.2f MHz), worst setup slack %.3f ns, hold %.3f ns" \
    $core [expr {1000.0 / $core}] $wns $whs]
puts "RD68884: worst path [get_property STARTPOINT_PIN $p] -> [get_property ENDPOINT_PIN $p]"
puts "RD68884: cells: LUT [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == LUT}]] \
     FF [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == FLOP_LATCH}]] \
     DSP [llength [get_cells -hier -quiet -filter {REF_NAME =~ DSP*}]] \
     BRAM [llength [get_cells -hier -quiet -filter {REF_NAME =~ RAMB*}]] \
     IO [llength [get_ports]]"
set drc_err [llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]
if {$wns < 0 || $whs < 0} {
    puts "RD68884: TIMING NOT MET"
    exit 1
}
if {$drc_err > 0} {
    puts "RD68884: $drc_err DRC error(s), see ${name}_drc.rpt"
    exit 1
}

write_bitstream -force ${name}.bit

# The flash image, as the platform's write_cfgmem: 16 MB, SPI x4, the bitstream
# at 0. (The platform also loads the HDMI gateware's declaration ROM at
# 0x280000; an FPU has none.)
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x00000000 ${name}.bit" -file ${name}.bin
write_cfgmem -force -format mcs -interface spix4 -size 16 \
    -loadbit "up 0x00000000 ${name}.bit" -file ${name}.mcs
puts "RD68884: wrote ${name}.bit, ${name}.bin and ${name}.mcs"
