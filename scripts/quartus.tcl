# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Quartus analysis and synthesis, and optionally the fit.
#
#   quartus_sh -t scripts/quartus.tcl <stage> <build> <top> <family> <part> [NAME=VALUE ...]
#
# Each NAME=VALUE sets a top-level parameter (BUS_SYNC, BUS_SYNC_WAIT). The
# ports are virtual pins, so the same-clock BIU's pin timing is Vivado's
# business (scripts/rd68884_sync.xdc.in); here it is the logic.
#
# <stage> is "map" (analysis and synthesis, the front-end check) or "fit" (place and
# route, for a second post-route frequency).
#
# Every port but clk is made a virtual pin. This design is a coprocessor core, not a
# board design; without it Quartus tries to place every pin and the fit is about the
# package rather than the logic, which is also what Vivado's out-of-context mode
# avoids.

package require ::quartus::project
package require ::quartus::flow

# quartus_sh reports a failed script as "Error (23031): Evaluation of Tcl script
# unsuccessful" and throws the message away, which tells you nothing. Catch it here
# and print the stack, or every failure costs a bisection.
proc main {} {

    set stage   [lindex $::quartus(args) 0]
    set build   [lindex $::quartus(args) 1]
    set top     [lindex $::quartus(args) 2]
    set family  [lindex $::quartus(args) 3]
    set part    [lindex $::quartus(args) 4]
    set params  [lrange $::quartus(args) 5 end]

    set proj $build/$top

    # project_new/project_open change the working directory to the project's, so
    # every path taken from the command line has to be made absolute first or it
    # silently stops resolving. Measured: "couldn't open build/rtl.f".
    set here [pwd]
    set rtlf [file join $here $build rtl.f]
    set sdc  [file join $here scripts rd68884.sdc]

    if {[project_exists $proj]} {
        project_open -revision $top $proj
    } else {
        project_new -revision $top $proj
    }

    set_global_assignment -name FAMILY $family
    set_global_assignment -name DEVICE $part
    # A MAX 10 configures from its own flash, and only one of its configuration
    # modes carries initialised memory: without it Quartus will not infer the
    # microcode store and tables as ROMs at all ("MIF is not supported for the selected
    # family") and builds it from half the part's logic elements.
    if {[string match -nocase "max 10" $family] || [string match -nocase "max10" $family]} {
        set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM"
    }
    set_global_assignment -name TOP_LEVEL_ENTITY $top
    set_global_assignment -name PROJECT_OUTPUT_DIRECTORY [file join $here $build quartus_out]
    set_global_assignment -name SDC_FILE $sdc
    set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005
    set_global_assignment -name NUM_PARALLEL_PROCESSORS 8
    # The project is reopened from run to run, so the Makefile passes every
    # parameter every time.
    foreach p $params {
        lassign [split $p =] name value
        set_parameter -name $name $value
    }

    set fp [open $rtlf r]
    foreach f [split [string trim [read $fp]] "\n"] {
        if {[string length [string trim $f]] > 0} {
            set_global_assignment -name SYSTEMVERILOG_FILE [file join $here $f]
        }
    }
    close $fp

    # Out of context, by hand: everything but the clock becomes a virtual pin.
    set_instance_assignment -name VIRTUAL_PIN ON -to *
    set_instance_assignment -name VIRTUAL_PIN OFF -to clk

    export_assignments

    if {$stage eq "map"} {
        if {[catch {execute_module -tool map} result]} {
            puts "QUARTUS: analysis and synthesis FAILED: $result"
            project_close
            exit 1
        }
        puts "QUARTUS: analysis and synthesis ok"
    } elseif {$stage eq "fit"} {
        foreach tool {map fit sta} {
            if {[catch {execute_module -tool $tool} result]} {
                puts "QUARTUS: $tool FAILED: $result"
                project_close
                exit 1
            }
        }
        puts "QUARTUS: fit ok"
    } else {
        puts "QUARTUS: unknown stage '$stage'"
        project_close
        exit 1
    }

    project_close
}

if {[catch {main} result]} {
    puts "QUARTUS: script failed: $result"
    puts $::errorInfo
    exit 1
}
