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
          rtl/rd68884_biu.sv \
          rtl/rd68884_regfile.sv \
          rtl/rd68884_seq.sv \
          rtl/rd68884_top.sv

RTL := $(PKGS) $(GENPKG) $(GENSRC) $(SRCS)
VLT := rtl/rd68884.vlt

IVFLAGS := -g2012 -Wall -Wno-timescale

.PHONY: all help dirs lint lint-source lint-iverilog lint-verilator lint-yosys \
        lint-quartus lint-questa synth impl quartus audit check print-rtl clean \
        oracles model-test testfloat testfloat-full iss-testfloat iss-arith iss-trans trans-accuracy sim ucode ucode-check \
        iss-test sys

all: lint

help:
	@echo "RD68884 -- SystemVerilog MC68881 floating-point coprocessor"
	@echo
	@echo "The gate:"
	@echo "  make check        ucode-check, lint, audit, model-test, iss-test, sim"
	@echo "  make lint         elaborate every rtl module under iverilog, Verilator and yosys"
	@echo "  make audit        prove no register initialises outside reset"
	@echo "  make ucode        regenerate rtl/gen/ from tools/ucode/"
	@echo "  make ucode-check  ... or fail if rtl/gen/ is stale"
	@echo "  make model-test   the reference models' own tests (tools/model/tests)"
	@echo "  make iss-test     the microcode, through the ISS, against the golden model"
	@echo "  make sim          the directed testbenches in sim/tb (iverilog)"
	@echo
	@echo "Oracles:"
	@echo "  make oracles      build SoftFloat and TestFloat into $(BUILD)/oracles"
	@echo "  make testfloat    the arithmetic model against TestFloat, 3000 vectors a case"
	@echo "  make testfloat-full  ... every level-1 vector (minutes)"
	@echo "  make iss-testfloat   TestFloat through the microcode (the ISS), 300 a case"
	@echo "  make iss-arith    the microcode against the model, ARITH_N random cases"
	@echo "  make iss-trans    the transcendentals against the model, TRANS_N cases"
	@echo "  make trans-accuracy  the transcendentals' error in ulps, against mpmath"
	@echo
	@echo "The system, with RD68021 ($(RD68021)) as the MC68020:"
	@echo "  make sys          sim/programs/fpu_m4.S on every port width, and the"
	@echo "                    RTL sequencer against the ISS, clock by clock"
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

# The generated files are committed, so a build needs no Python; ucode-check
# keeps them honest. build/ucode.json, for the ISS, is written either way.
ucode: dirs
	@python3 tools/ucode/asm.py

ucode-check: dirs
	@python3 tools/ucode/asm.py --check

iss-test: ucode-check
	@$(PYENV) python3 -m unittest discover -s tools/iss/tests -t tools -q > $(BUILD)/iss-test.log 2>&1 \
	  && echo "PASS: iss-test" || { tail -30 $(BUILD)/iss-test.log; echo "FAIL: iss-test"; exit 1; }

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

# The same vectors through the microcode (the ISS), with every dialog.
iss-testfloat: oracles ucode-check
	@set -o pipefail; $(PYENV) python3 tools/iss/check_testfloat.py --limit 300 | grep -v ': ok$$'

# The transcendentals against the model (FPU 4.3.2's bound), and how
# accurate they are, in ulps of extended, against mpmath.
TRANS_N ?= 3000
iss-trans: ucode-check
	@TRANS_N=$(TRANS_N) $(PYENV) python3 -m unittest iss.tests.test_trans \
	  && echo "PASS: iss-trans, $(TRANS_N) cases"
trans-accuracy: ucode-check
	@$(PYENV) python3 tools/iss/trans_accuracy.py 400

# The microcode against the golden model on random operands: ARITH_N cases.
ARITH_N ?= 20000
iss-arith: ucode-check
	@ARITH_N=$(ARITH_N) $(PYENV) python3 -m unittest iss.tests.test_arith \
	  && echo "PASS: iss-arith, $(ARITH_N) cases"

# ---------------------------------------------------------------------------
# Directed testbenches -- M3
#
# Each runs to completion and prints PASS or FAIL. The loop fails on a FAIL and
# ALSO on a missing PASS: a testbench that stopped early without saying so has
# not passed (the RD68021 rule).
# ---------------------------------------------------------------------------
TBS := $(filter-out sys_tb,$(patsubst sim/tb/%.sv,%,$(wildcard sim/tb/*_tb.sv)))

sim: dirs
	@ok=1; for tb in $(TBS); do \
	  iverilog $(IVFLAGS) -I sim/tb -o $(BUILD)/$$tb.vvp -s $$tb $(RTL) sim/tb/$$tb.sv \
	    > $(BUILD)/$$tb.clog 2>&1 || { grep -v $(NOTES) $(BUILD)/$$tb.clog; ok=0; continue; }; \
	  vvp $(BUILD)/$$tb.vvp > $(BUILD)/$$tb.log 2>&1; \
	  if grep -q '^FAIL' $(BUILD)/$$tb.log; then \
	    grep '^FAIL' $(BUILD)/$$tb.log | head -20; ok=0; \
	  elif ! grep -q '^PASS' $(BUILD)/$$tb.log; then \
	    echo "FAIL: $$tb reported no PASS"; tail -20 $(BUILD)/$$tb.log; ok=0; \
	  else \
	    echo "  $$(grep -E '^PASS' $(BUILD)/$$tb.log | head -1)"; \
	  fi; \
	done; test $$ok -eq 1 && echo "PASS: sim"

# ---------------------------------------------------------------------------
# The system -- M4
#
# RD68021 is used where it stands (CLAUDE.md: read-only); its RTL list comes
# from its own Makefile. Not in `check`: it needs that checkout, and minutes.
# ---------------------------------------------------------------------------
RD68021 ?= ../RD68021
CROSS   := m68k-linux-gnu-
SYSPROGS  ?= fpu_m4 fpu_m5 fpu_m6 fpu_m7 fpu fparith fparith-dbl
SYS_PORTS ?= 32 16 8

# fpu_m5's and fpu_m6's vectors come from the golden model.
$(BUILD)/programs/fpu_m5_vec.S: sim/programs/gen_fpu_m5.py tools/model/arith.py | dirs
	@mkdir -p $(BUILD)/programs
	@$(PYENV):$(CURDIR)/sim/programs python3 sim/programs/gen_fpu_m5.py $@ 150 5
$(BUILD)/programs/fpu_m6_vec.S: sim/programs/gen_fpu_m6.py sim/programs/gen_fpu_m5.py \
                                tools/model/arith.py tools/model/packed.py | dirs
	@mkdir -p $(BUILD)/programs
	@$(PYENV):$(CURDIR)/sim/programs python3 sim/programs/gen_fpu_m6.py $@ 120 6
$(BUILD)/programs/fpu_m7_vec.S: sim/programs/gen_fpu_m7.py sim/programs/gen_fpu_m5.py \
                                build/ucode.json | dirs
	@mkdir -p $(BUILD)/programs
	@$(PYENV):$(CURDIR)/sim/programs python3 sim/programs/gen_fpu_m7.py $@ 100 7
$(BUILD)/programs/fpu_m5.hex: $(BUILD)/programs/fpu_m5_vec.S sim/programs/arith_harness.inc
$(BUILD)/programs/fpu_m6.hex: $(BUILD)/programs/fpu_m6_vec.S sim/programs/arith_harness.inc
$(BUILD)/programs/fpu_m7.hex: $(BUILD)/programs/fpu_m7_vec.S sim/programs/arith_harness.inc

# fparith.c (copied from RD68021), twice: at extended rounding precision
# against the host's x87, and at double against its SSE (the comment at its
# top). C11, so that both sides round on every assignment; __builtin_sqrt is
# FSQRT only when nothing has to set errno.
CFLAGS68  := -O2 -fno-builtin -fomit-frame-pointer -nostdlib -ffreestanding \
             -Wall -Wextra -std=c11 -fno-math-errno
FPARITHCC := -std=c11 -O2 -fno-math-errno -ffp-contract=off

$(BUILD)/programs/crt0.o: sim/programs/crt0.S | dirs
	@mkdir -p $(BUILD)/programs
	@$(CROSS)as -mcpu=68020 -m68881 -o $@ $<
$(BUILD)/programs/fparith.o: sim/programs/fparith.c | dirs
	@mkdir -p $(BUILD)/programs
	@$(CROSS)gcc -c $(CFLAGS68) -o $@ $<
$(BUILD)/programs/fparith-dbl.o: sim/programs/fparith.c | dirs
	@mkdir -p $(BUILD)/programs
	@$(CROSS)gcc -c $(CFLAGS68) -DFPU_PREC_DOUBLE -o $@ $<
FPARITH_HEX := $(BUILD)/programs/fparith.hex $(BUILD)/programs/fparith-dbl.hex
$(FPARITH_HEX): $(BUILD)/programs/%.hex: $(BUILD)/programs/%.o $(BUILD)/programs/crt0.o \
                $(BUILD)/programs/%.expect sim/programs/flat.ld
	@$(CROSS)gcc -nostdlib -nostartfiles -Wl,--no-warn-rwx-segments,--build-id=none \
	    -T sim/programs/flat.ld -o $(BUILD)/programs/$*.elf \
	    $(BUILD)/programs/crt0.o $< -lgcc
	@$(CROSS)objcopy -O verilog --verilog-data-width=1 $(BUILD)/programs/$*.elf $@
$(BUILD)/programs/fparith.expect: sim/programs/fparith.c | dirs
	@cc $(FPARITHCC) -mfpmath=387 -o $(BUILD)/programs/fparith-host $< -lm
	@$(BUILD)/programs/fparith-host > $@ 2> $(BUILD)/programs/fparith.exact
$(BUILD)/programs/fparith-dbl.expect: sim/programs/fparith.c | dirs
	@cc $(FPARITHCC) -mfpmath=sse -o $(BUILD)/programs/fparith-dbl-host $< -lm
	@$(BUILD)/programs/fparith-dbl-host > $@ 2> $(BUILD)/programs/fparith-dbl.exact

$(BUILD)/programs/%.hex: sim/programs/%.S sim/programs/flat.ld | dirs
	@mkdir -p $(BUILD)/programs
	@$(CROSS)as -mcpu=68020 -m68881 -I $(BUILD)/programs -I sim/programs -o $(BUILD)/programs/$*.o $<
	@$(CROSS)ld --no-warn-rwx-segments -T sim/programs/flat.ld -o $(BUILD)/programs/$*.elf $(BUILD)/programs/$*.o
	@$(CROSS)objcopy -O verilog --verilog-data-width=1 $(BUILD)/programs/$*.elf $@

sys: ucode-check $(patsubst %,$(BUILD)/programs/%.hex,$(SYSPROGS))
	@test -d $(RD68021) || { echo "FAIL: no RD68021 at $(RD68021)"; exit 1; }
	@rd="$$(cd $(RD68021) && $(MAKE) -s print-rtl | sed 's#\(\S*\)#$(RD68021)/\1#g')"; \
	ok=1; for port in $(SYS_PORTS); do \
	  iverilog $(IVFLAGS) -DSYS_PORT=$$port -o $(BUILD)/sys_tb_$$port.vvp -s sys_tb \
	    $(RTL) $$rd $(RD68021)/sim/models/rd68021_slave.sv sim/tb/sys_tb.sv \
	    > $(BUILD)/sys_tb_$$port.clog 2>&1 || { grep -v $(NOTES) $(BUILD)/sys_tb_$$port.clog | head; exit 1; }; \
	  for p in $(SYSPROGS); do \
	    ls=""; if [ $$port = 32 ]; then ls="+lockstep=$(BUILD)/sys-$$p.lockstep"; fi; \
	    dp=""; if [ -f $(BUILD)/programs/$$p.expect ]; then dp="+dump=$(BUILD)/sys-$$p-$$port.dump"; fi; \
	    vvp $(BUILD)/sys_tb_$$port.vvp +image=$(BUILD)/programs/$$p.hex +limit=4000000 $$ls $$dp \
	      > $(BUILD)/sys-$$p-$$port.log 2>&1; \
	    if grep -q '^PASS' $(BUILD)/sys-$$p-$$port.log; then \
	      echo "  $$p, $$port-bit port: $$(grep '^PASS' $(BUILD)/sys-$$p-$$port.log)"; \
	    else grep -E '^FAIL|sys_tb:' $(BUILD)/sys-$$p-$$port.log; ok=0; fi; \
	    if [ -n "$$dp" ]; then \
	      python3 tools/fparith_compare.py $(BUILD)/programs/$$p.expect $(BUILD)/sys-$$p-$$port.dump \
	        $$(sed -n 's/^exact \([0-9]*\) of.*/\1/p' $(BUILD)/programs/$$p.exact) \
	        > $(BUILD)/sys-$$p-$$port.cmp; \
	      grep -E '^  FAIL|exact long words match' $(BUILD)/sys-$$p-$$port.cmp | head -5 | sed 's/^/  /'; \
	      grep -q '^PASS' $(BUILD)/sys-$$p-$$port.cmp || ok=0; \
	    fi; \
	    if [ $$port = 32 ]; then \
	      $(PYENV) python3 tools/iss/lockstep.py $(BUILD)/sys-$$p.lockstep | sed 's/^/  /' || ok=0; \
	    fi; \
	  done; \
	done; test $$ok -eq 1 && echo "PASS: sys"

# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
check: ucode-check lint audit model-test iss-test sim
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
