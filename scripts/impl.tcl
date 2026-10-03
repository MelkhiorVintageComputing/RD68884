# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Vivado place and route, for the numbers that mean something.
#
#   vivado -mode batch -source scripts/impl.tcl -tclargs <part> <top> <repo-root> \
#          [<constraints> [NAME=VALUE ...]]
#
# As scripts/synth.tcl: the optional constraints replace scripts/rd68884.xdc and
# each NAME=VALUE sets a top-level parameter.
#
# Out of context, hierarchy kept, so the per-module utilisation in
# impl_utilization_hier.rpt says what each unit costs -- the figure
# doc/size-and-speed.md tracks milestone by milestone.

set part [lindex $argv 0]
set top  [lindex $argv 1]
set root [lindex $argv 2]
set xdc  [expr {$argc > 3 ? [lindex $argv 3] : "$root/scripts/rd68884.xdc"}]
set gen  {}
foreach g [lrange $argv 4 end] { lappend gen -generic $g }

puts "RD68884: implementing $top for $part"

set f [open rtl.f r]
set rtl [split [string trim [read $f]] "\n"]
close $f

set_msg_config -id "Synth 8-6901" -new_severity ERROR

read_verilog -sv $rtl
read_xdc $xdc

synth_design -top $top -part $part -mode out_of_context -flatten_hierarchy none {*}$gen
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
# With the pins constrained (the same-clock BIU, doc/bus-timing.md) their paths
# are half or one and a half clocks, not one: report them on their own, and
# the core's figure from register to register.
set pin_in  [get_timing_paths -quiet -delay_type max -max_paths 1 -from [all_inputs]]
set pin_out [get_timing_paths -quiet -delay_type max -max_paths 1 -to [all_outputs]]
if {[llength $pin_in] > 0 || [llength $pin_out] > 0} {
    foreach {what q} [list "from an input" $pin_in "to an output" $pin_out] {
        if {[llength $q] > 0} {
            puts [format "RD68884: worst path %s: slack %.2f ns, %s -> %s" $what \
                [get_property SLACK $q] [get_property STARTPOINT_PIN $q] [get_property ENDPOINT_PIN $q]]
            if {[get_property SLACK $q] < $wns} { set wns [get_property SLACK $q] }
        }
    }
    set p [get_timing_paths -quiet -delay_type max -max_paths 1 \
        -from [all_registers] -to [all_registers]]
}
if {[llength $p] > 0} {
    set rwns [get_property SLACK $p]
    if {$rwns < $wns} { set wns $rwns }
    set period [get_property PERIOD [get_clocks clk]]
    set need   [expr {$period - $rwns}]
    puts [format "RD68884: needs a period of %.2f ns -- %.2f MHz" $need [expr {1000.0 / $need}]]
    puts "RD68884: limited by [get_property STARTPOINT_PIN $p] -> [get_property ENDPOINT_PIN $p]\
          ([get_property LOGIC_LEVELS $p] levels)"
} else {
    puts "RD68884: no timing paths (nothing between flops yet)"
}
if {[llength $h] > 0} {
    set whs [get_property SLACK $h]
    if {$whs < 0} {
        puts [format "RD68884: worst hold path: slack %.2f ns, %s -> %s" $whs \
            [get_property STARTPOINT_PIN $h] [get_property ENDPOINT_PIN $h]]
    }
}

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
