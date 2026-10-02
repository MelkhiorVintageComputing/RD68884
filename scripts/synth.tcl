# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Vivado out-of-context synthesis.
#
#   vivado -mode batch -source scripts/synth.tcl -tclargs <build> <top> <part>
#
# Out of context because this is a coprocessor core, not a board design: there are
# no pads, no I/O buffers and no board constraints, and the numbers are about the
# logic -- which, for a design whose first goal is size, is the number that matters.

set build [lindex $argv 0]
set top   [lindex $argv 1]
set part  [lindex $argv 2]

# A signal used before its declaration is only [Synth 8-6901], an *info* message.
# Questa rejects the same thing outright, so make Vivado agree. [Synth 8-3332]
# ("sequential element is unused") is NOT promoted: it is ordinary optimisation
# (doc/coding-standard.md).
set_msg_config -id {Synth 8-6901} -new_severity ERROR

set fp [open $build/rtl.f r]
set files [split [string trim [read $fp]] "\n"]
close $fp

foreach f $files {
    if {[string length [string trim $f]] > 0} {
        read_verilog -sv $f
    }
}

read_xdc scripts/rd68884.xdc

synth_design -top $top -part $part -mode out_of_context

write_checkpoint -force $build/${top}_synth.dcp
report_utilization -file $build/${top}_synth_util.rpt
report_utilization -hierarchical -file $build/${top}_synth_util_hier.rpt
report_timing_summary -file $build/${top}_synth_timing.rpt

# A skeleton with nothing between flops has no timing paths, which is not an
# error, so ask quietly and test the length before reporting.
set paths [get_timing_paths -delay_type max -quiet]
if {[llength $paths] > 0} {
    puts [format "SYNTH: part %s  WNS %s ns" $part [get_property SLACK [lindex $paths 0]]]
} else {
    puts "SYNTH: part $part  no timing paths (nothing between flops yet)"
}
puts [format "SYNTH: LUT %s  FF %s  DSP %s  BRAM %s" \
    [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == LUT}]] \
    [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == FLOP_LATCH}]] \
    [llength [get_cells -hier -quiet -filter {REF_NAME =~ DSP*}]] \
    [llength [get_cells -hier -quiet -filter {PRIMITIVE_GROUP == BLOCKRAM}]]]
