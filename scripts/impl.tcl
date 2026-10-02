# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Vivado place and route, for the numbers that mean something.
#
#   vivado -mode batch -source scripts/impl.tcl -tclargs <part> <top> <repo-root>
#
# Out of context, hierarchy kept, so the per-module utilisation in
# impl_utilization_hier.rpt says what each unit costs -- the figure
# doc/size-and-speed.md tracks milestone by milestone.

set part [lindex $argv 0]
set top  [lindex $argv 1]
set root [lindex $argv 2]

puts "RD68884: implementing $top for $part"

set f [open rtl.f r]
set rtl [split [string trim [read $f]] "\n"]
close $f

set_msg_config -id "Synth 8-6901" -new_severity ERROR

read_verilog -sv $rtl
read_xdc $root/scripts/rd68884.xdc

synth_design -top $top -part $part -mode out_of_context -flatten_hierarchy none
opt_design
place_design
phys_opt_design
route_design

report_utilization               -file impl_utilization.rpt
report_utilization -hierarchical -file impl_utilization_hier.rpt
report_ram_utilization           -file impl_ram.rpt
report_timing_summary -delay_type max -max_paths 20 -file impl_timing.rpt
report_timing -delay_type max -max_paths 400 -unique_pins -nworst 1 \
    -file impl_timing_families.rpt

write_checkpoint -force ${top}_impl.dcp

# One clock edge: every path has the whole period, so the shortest period is the
# period minus the worst slack. A design with no register-to-register path yet
# has nothing to report.
set p [get_timing_paths -quiet -delay_type max -max_paths 1]
set h [get_timing_paths -quiet -delay_type min -max_paths 1]
set wns 0
set whs 0
if {[llength $p] > 0} {
    set wns    [get_property SLACK $p]
    set period [get_property PERIOD [get_clocks clk]]
    set need   [expr {$period - $wns}]
    puts [format "RD68884: needs a period of %.2f ns -- %.2f MHz" $need [expr {1000.0 / $need}]]
    puts "RD68884: limited by [get_property STARTPOINT_PIN $p] -> [get_property ENDPOINT_PIN $p]\
          ([get_property LOGIC_LEVELS $p] levels)"
} else {
    puts "RD68884: no timing paths (nothing between flops yet)"
}
if {[llength $h] > 0} { set whs [get_property SLACK $h] }

puts "RD68884: cells: LUT [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == LUT}]] \
     LUTRAM [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == DMEM}]] \
     FF [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == FLOP_LATCH}]] \
     CARRY [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == CARRY}]] \
     DSP [llength [get_cells -hier -quiet -filter {REF_NAME =~ DSP*}]] \
     BRAM [llength [get_cells -hier -quiet -filter {REF_NAME =~ RAMB*}]]"

if {$wns < 0 || $whs < 0} {
    puts "RD68884: TIMING NOT MET (setup $wns ns, hold $whs ns)"
    exit 1
}
puts "RD68884: implementation ok"
exit 0
