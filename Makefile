# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# RD68884 -- SystemVerilog MC68881 floating-point coprocessor
#
# Targets arrive as the milestones that need them do. `make help` lists what exists.
#
# The three always-available front-ends -- iverilog, Verilator, yosys -- are what
# `make lint` runs and what the RTL is written against day to day. Vivado, Quartus
# and Questa each have their own target and none of them is in `make check`, which
# has to work on a machine with no vendor tools installed.

SHELL := /bin/bash

TOP   ?= rd68884_top
BUILD ?= build

# Vendor tool locations. Neither is on PATH; the launchers in scripts/ set it up.
VIVADO_SETTINGS ?= /opt/Xilinx/2025.2/Vivado/settings64.sh
QUARTUS_ROOTDIR ?= /opt/Altera/quartus
QUESTA_ROOTDIR  ?= /opt/Altera/questa_fse
export VIVADO_SETTINGS QUARTUS_ROOTDIR QUESTA_ROOTDIR

# The size target is the smallest Artix-7 (doc/architecture.md); the 35T is the
# part every utilisation figure in doc/size-and-speed.md is quoted against.
XPART   ?= xc7a35tcsg324-1
AFAMILY ?= "Cyclone V"
APART   ?= 5CSEMA5F31C6

# ---------------------------------------------------------------------------
# The file list. Packages first: Vivado, Questa and iverilog need a package read
# before its users. Generated files are split the same way, because a plain
# wildcard over rtl/gen/ sorts a module ahead of the package it depends on.
# ---------------------------------------------------------------------------
PKGS   := rtl/rd68884_pkg.sv
GENPKG := $(wildcard rtl/gen/*_pkg.sv)
GENSRC := $(filter-out $(GENPKG),$(wildcard rtl/gen/*.sv))
SRCS   := rtl/rd68884_sync.sv \
          rtl/rd68884_top.sv

RTL := $(PKGS) $(GENPKG) $(GENSRC) $(SRCS)
VLT := rtl/rd68884.vlt

IVFLAGS := -g2012 -Wall -Wno-timescale

.PHONY: all help dirs lint lint-source lint-iverilog lint-verilator lint-yosys \
        lint-quartus lint-questa synth impl quartus audit check print-rtl clean \
        oracles model-test testfloat testfloat-full

all: lint

help:
	@echo "RD68884 -- SystemVerilog MC68881 floating-point coprocessor"
	@echo
	@echo "The gate:"
	@echo "  make check        lint and audit, together"
	@echo "  make lint         elaborate every rtl module under iverilog, Verilator and yosys"
	@echo "  make audit        prove no register initialises outside reset"
	@echo "  make model-test   the reference models' own tests (tools/model/tests)"
	@echo
	@echo "Oracles:"
	@echo "  make oracles      build SoftFloat and TestFloat into $(BUILD)/oracles"
	@echo "  make testfloat    the arithmetic model against TestFloat, 3000 vectors a case"
	@echo "  make testfloat-full  ... every level-1 vector (minutes)"
	@echo
	@echo "Vendor tools, minutes each:"
	@echo "  make synth        Vivado synthesis only ($(XPART)), out of context"
	@echo "  make impl         Vivado place and route ($(XPART)), out of context"
	@echo "  make quartus      Quartus fit and timing ($(AFAMILY) $(APART))"
	@echo "  make lint-quartus Quartus analysis and synthesis"
	@echo "  make lint-questa  Questa vlog + vopt"
	@echo
	@echo "  make clean        remove $(BUILD)/"

dirs:
	@mkdir -p $(BUILD)

# ---------------------------------------------------------------------------
# Lint -- the three that need no vendor installation
# ---------------------------------------------------------------------------
lint: lint-source lint-iverilog lint-verilator lint-yosys
	@echo "PASS: lint"

# The two portability rules the three free front-ends do not enforce and the
# three vendor ones do: declaration before use, and no package-scoped name in a
# port connection (doc/coding-standard.md).
lint-source:
	@python3 tools/src_lint.py $(filter-out $(PKGS) $(GENPKG),$(RTL))

# Each tool runs into a log and the recipe decides from its exit status. Piping
# into a filter instead throws the status away (measured in RD68021, twice).
#
# iverilog prints a "sorry: ..." note for every unique case and for every
# variable part-select in an always_ block; they are notes, not diagnostics, and
# are filtered out of what is shown on a failure.
NOTES := ': sorry: .*\(ignored\|all bits will be included\)\.$$'

lint-iverilog: dirs
	@iverilog $(IVFLAGS) -o $(BUILD)/$(TOP).vvp -s $(TOP) $(RTL) \
	    > $(BUILD)/iverilog.log 2>&1 \
	  || { grep -v $(NOTES) $(BUILD)/iverilog.log; exit 1; }
	@echo "  iverilog: ok"

lint-verilator: dirs
	@verilator --lint-only -Wall --top-module $(TOP) $(VLT) $(RTL) \
	    > $(BUILD)/verilator.log 2>&1 \
	  || { grep -v '^- V e r i l a t i o n\|^- Verilator:' $(BUILD)/verilator.log; exit 1; }
	@echo "  verilator: ok"

# The full synth pass, so anything unsynthesisable is caught here rather than in
# Vivado. yosys returns 0 on a warning, and two of its warnings are defects: a
# register driven from two processes, and an inferred latch. Both are gates here.
lint-yosys: dirs
	@set -o pipefail; yosys -p "read_verilog -sv $(RTL); synth -top $(TOP); \
	    write_verilog $(BUILD)/$(TOP)_yosys.v" > $(BUILD)/yosys.log 2>&1 \
	  || { tail -40 $(BUILD)/yosys.log; exit 1; }
	@if grep -q 'multiple conflicting drivers\|Warning: Identifier .* is implicitly declared\|inferring latch' $(BUILD)/yosys.log; then \
	    echo "FAIL: yosys"; \
	    grep -n 'multiple conflicting drivers\|implicitly declared\|inferring latch' $(BUILD)/yosys.log | head -20; \
	    exit 1; fi
	@echo "  yosys: ok"

# ---------------------------------------------------------------------------
# The reset rule
# ---------------------------------------------------------------------------
audit: dirs
	@python3 tools/reset_audit.py --top $(TOP) --build $(BUILD) $(RTL)

# ---------------------------------------------------------------------------
# Reference models -- M2
#
# Pure Python; mpmath is the third_party/mpmath submodule, used in place, so
# nothing is installed. SoftFloat and TestFloat are submodules too, built out of
# tree so their checkouts stay clean.
# ---------------------------------------------------------------------------
PYENV := PYTHONPATH=$(CURDIR)/tools:$(CURDIR)/third_party/mpmath
SF    := $(CURDIR)/third_party/berkeley-softfloat-3
TF    := $(CURDIR)/third_party/berkeley-testfloat-3
ORA   := $(BUILD)/oracles

model-test:
	@$(PYENV) python3 -m unittest discover -s tools/model/tests -t tools -q 2>&1 | tail -5
	@$(PYENV) python3 -m unittest discover -s tools/model/tests -t tools -q > /dev/null 2>&1 \
	  && echo "PASS: model-test" || { echo "FAIL: model-test"; exit 1; }

oracles: $(ORA)/testfloat/testfloat_gen

$(ORA)/testfloat/testfloat_gen:
	@mkdir -p $(ORA)
	@rm -rf $(ORA)/softfloat $(ORA)/testfloat
	@cp -r $(SF)/build/Linux-x86_64-GCC $(ORA)/softfloat
	@cp -r $(TF)/build/Linux-x86_64-GCC $(ORA)/testfloat
	@$(MAKE) -s -C $(ORA)/softfloat SOURCE_DIR=$(SF)/source > $(ORA)/softfloat.log 2>&1
	@$(MAKE) -s -C $(ORA)/testfloat SOURCE_DIR=$(TF)/source SOFTFLOAT_DIR=$(SF) \
	    SOFTFLOAT_LIB=$(CURDIR)/$(ORA)/softfloat/softfloat.a > $(ORA)/testfloat.log 2>&1
	@echo "  oracles: SoftFloat and TestFloat built"

testfloat: oracles
	@set -o pipefail; $(PYENV) python3 tools/model/check_testfloat.py --limit 3000 | grep -v ': ok$$'

testfloat-full: oracles
	@set -o pipefail; $(PYENV) python3 tools/model/check_testfloat.py | grep -v ': ok$$'

# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
check: lint audit model-test
	@echo "PASS: check"

# ---------------------------------------------------------------------------
# Vendor front-ends. Each has its own target and none is in `check`.
# ---------------------------------------------------------------------------
synth: dirs
	@printf '%s\n' $(RTL) > $(BUILD)/rtl.f
	@set -o pipefail; scripts/vivado.sh -mode batch -nojournal -nolog \
	    -source scripts/synth.tcl -tclargs $(BUILD) $(TOP) $(XPART) \
	    > $(BUILD)/synth.log 2>&1 \
	  || { grep -E '^(SYNTH|ERROR|CRITICAL WARNING)' $(BUILD)/synth.log; exit 1; }
	@grep -E '^(SYNTH|CRITICAL WARNING)' $(BUILD)/synth.log

impl: dirs
	@printf '%s\n' $(addprefix $(CURDIR)/,$(RTL)) > $(BUILD)/rtl.f
	@cd $(BUILD) && $(CURDIR)/scripts/vivado.sh -mode batch -nojournal -nolog \
	    -source $(CURDIR)/scripts/impl.tcl -tclargs $(XPART) $(TOP) $(CURDIR) \
	    > impl.log 2>&1; rc=$$?; \
	  grep -E '^(RD68884|ERROR|CRITICAL WARNING)' impl.log; exit $$rc

# quartus_map returns 0 on the thing that matters most, so the grep is the gate:
# a package-scoped constant inside a port expression becomes an implicit net,
# Warning (10236), and the netlist stops matching the source.
lint-quartus: dirs
	@printf '%s\n' $(RTL) > $(BUILD)/rtl.f
	@set -o pipefail; scripts/altera.sh quartus_sh -t scripts/quartus.tcl map \
	    $(BUILD) $(TOP) $(AFAMILY) $(APART) > $(BUILD)/quartus_map.log 2>&1 \
	  || { grep -E '^QUARTUS|Error' $(BUILD)/quartus_map.log | head; exit 1; }
	@if grep -q 'Implicit Net warning\|Warning (10236)' $(BUILD)/quartus_map.log; then \
	    echo "FAIL: quartus found an implicit net -- the netlist does not match the source"; \
	    grep 'Warning (10236)' $(BUILD)/quartus_map.log; exit 1; fi
	@echo "  quartus: ok"

QBUILD := $(BUILD)/quartus

quartus: dirs
	@mkdir -p $(QBUILD)
	@printf '%s\n' $(RTL) > $(QBUILD)/rtl.f
	@set -o pipefail; scripts/altera.sh quartus_sh -t scripts/quartus.tcl fit \
	    $(QBUILD) $(TOP) $(AFAMILY) $(APART) \
	    > $(QBUILD)/fit.log 2>&1 || { grep -E '^QUARTUS|Error' $(QBUILD)/fit.log | head; exit 1; }
	@if grep -q 'Warning (10236)' $(QBUILD)/fit.log; then \
	    echo "FAIL: quartus found an implicit net"; exit 1; fi
	@grep -E '^QUARTUS' $(QBUILD)/fit.log
	@grep -E 'Logic utilization|Total registers|Total block memory bits|Total DSP|Total RAM Blocks' \
	    $(QBUILD)/quartus_out/$(TOP).fit.summary

lint-questa: dirs
	@scripts/questa.sh $(BUILD) $(TOP) $(RTL)
	@echo "  questa: ok"

print-rtl:
	@echo $(RTL)

clean:
	rm -rf $(BUILD)
