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
        iss-test rd68021 sys cycles tme sunos board-iisia7 FORCE

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
	@echo "The system, with RD68021 ($(RD68021) at $(RD68021_REV), TME element $(RD68021_TME_REV)) as the MC68020:"
	@echo "  make sys          the programs in SYSPROGS on every port width, and the"
	@echo "                    RTL sequencer against the ISS, clock by clock"
	@echo "  make cycles       clock counts against FPU section 8 (FREEZE=1 to accept)"
	@echo "  make tme          TME with RD68021 + RD68884 as a CPU element"
	@echo "  make sunos        SunOS 4.1.1 on it, against TME's m68020/MC68881"
	@echo
	@echo "Boards:"
	@echo "  make board-iisia7 bitstream and flash image for the IIsiA7 Mini (BOARD_DIVIDE=20: 50 MHz)"
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

# The BIU's front end (doc/bus-timing.md): BUS_SYNC=0, the default, takes the
# bus as asynchronous; BUS_SYNC=1 runs on the main processor's CLK, with
# BUS_SYNC_WAIT wait states (0 or 1). lint, audit and sim cover all three
# builds whatever these say; the other targets build the one they name.
BUS_SYNC      ?= 0
BUS_SYNC_WAIT ?= 0
# BUS_SYNC:BUS_SYNC_WAIT:DSACK_NEGATE -- the asynchronous BIU, the same-clock
# one with no wait state and with one, and the asynchronous one negating
# DSACK before it releases it (the IIsiA7 Mini's, doc/boards.md).
BUILDS        := 0:0:0 1:0:0 1:1:0 0:0:1
GENERICS      := BUS_SYNC=$(BUS_SYNC) BUS_SYNC_WAIT=$(BUS_SYNC_WAIT)

# The same-clock BIU's pin timing, against the main processor's clock: its
# period, how long after a falling edge the strobes change (UM specs 9 and 12;
# a real MC68020 at 16.67 MHz takes up to 30 ns, RD68021 in the same FPGA only
# its routing), after a rising edge the address and the write data (UM specs 6
# and 23), and the setup DSACK and read data need (UM specs 47A and 27).
SYNC_CLK_NS   ?= 60
SYNC_IN_NS    ?= 30
SYNC_AIN_NS   ?= 30
SYNC_DIN_NS   ?= 30
SYNC_SETUP_NS ?= 5
XDC := $(if $(filter 1,$(BUS_SYNC)),$(CURDIR)/$(BUILD)/rd68884_sync.xdc,$(CURDIR)/scripts/rd68884.xdc)

$(BUILD)/rd68884_sync.xdc: scripts/rd68884_sync.xdc.in FORCE | dirs
	@rank='# (zero wait: no rank)'; \
	 amc='# (zero wait: they are used on the edge entering S2, one clock)'; \
	 [ "$(BUS_SYNC_WAIT)" = 1 ] && rank='set_false_path -from [get_ports {cs_n_i as_n_i ds_n_i rw_i}] -to [get_cells -hier -filter {NAME =~ *rank_q_reg*}]' \
	   && amc='# One wait: nothing uses them before the edge entering S4, two clocks.\nset_multicycle_path -setup 2 -from [get_ports {a_i[*] rw_i size_n_i}]\nset_multicycle_path -hold 1 -from [get_ports {a_i[*] rw_i size_n_i}]'; \
	 sed -e 's/@CLK@/$(SYNC_CLK_NS)/g' -e "s/@HALF@/$$(echo '$(SYNC_CLK_NS)' | awk '{printf "%.3f", $$1 / 2}')/g" \
	     -e 's/@IN@/$(SYNC_IN_NS)/g' -e 's/@AIN@/$(SYNC_AIN_NS)/g' -e 's/@DIN@/$(SYNC_DIN_NS)/g' \
	     -e 's/@SETUP@/$(SYNC_SETUP_NS)/g' -e "s|@RANK@|$$rank|" -e "s|@AMC@|$$amc|" $< > $@
FORCE:

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
	@for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  iverilog $(IVFLAGS) -P $(TOP).BUS_SYNC=$$bs -P $(TOP).BUS_SYNC_WAIT=$$bw -P $(TOP).DSACK_NEGATE=$$bn \
	    -o $(BUILD)/$(TOP).vvp -s $(TOP) $(RTL) > $(BUILD)/iverilog.log 2>&1 \
	  || { echo "BUS_SYNC:WAIT:NEGATE $$b"; grep -v $(NOTES) $(BUILD)/iverilog.log; exit 1; }; \
	done
	@echo "  iverilog: ok"

lint-verilator: dirs
	@for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  verilator --lint-only -Wall --top-module $(TOP) -GBUS_SYNC=$$bs -GBUS_SYNC_WAIT=$$bw -GDSACK_NEGATE=$$bn \
	    $(VLT) $(RTL) > $(BUILD)/verilator.log 2>&1 \
	  || { echo "BUS_SYNC:WAIT:NEGATE $$b"; grep -v '^- V e r i l a t i o n\|^- Verilator:' $(BUILD)/verilator.log; exit 1; }; \
	done
	@echo "  verilator: ok"

# The full synth pass, so anything unsynthesisable is caught here rather than in
# Vivado. yosys returns 0 on a warning, and two of its warnings are defects: a
# register driven from two processes, and an inferred latch. Both are gates here.
lint-yosys: dirs
	@set -o pipefail; for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  yosys -p "read_verilog -sv $(RTL); \
	    chparam -set BUS_SYNC $$bs -set BUS_SYNC_WAIT $$bw -set DSACK_NEGATE $$bn $(TOP); synth -top $(TOP); \
	    write_verilog $(BUILD)/$(TOP)_yosys.v" > $(BUILD)/yosys.log 2>&1 \
	  || { echo "BUS_SYNC:WAIT:NEGATE $$b"; tail -40 $(BUILD)/yosys.log; exit 1; }; \
	  if grep -q 'multiple conflicting drivers\|Warning: Identifier .* is implicitly declared\|inferring latch' $(BUILD)/yosys.log; then \
	    echo "FAIL: yosys, BUS_SYNC:WAIT:NEGATE $$b"; \
	    grep -n 'multiple conflicting drivers\|implicitly declared\|inferring latch' $(BUILD)/yosys.log | head -20; \
	    exit 1; fi; \
	done
	@echo "  yosys: ok"

# ---------------------------------------------------------------------------
# The reset rule
# ---------------------------------------------------------------------------
audit: dirs
	@for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  python3 tools/reset_audit.py --top $(TOP) --build $(BUILD) \
	    --param BUS_SYNC=$$bs --param BUS_SYNC_WAIT=$$bw --param DSACK_NEGATE=$$bn $(RTL) || exit 1; \
	done
	@# The board tops: their primitives are the vendor's, so the source only.
	@python3 tools/reset_audit.py --source-only boards/*/*_top.sv

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
	@ok=1; for tb in $(TBS); do for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  def=""; [ $$bs = 1 ] && def="-DBIU_SYNC -DBIU_SYNC_WAIT=$$bw"; \
	  [ $$bn = 1 ] && def="$$def -DBIU_NEGATE"; \
	  l=$(BUILD)/$$tb-$$bs$$bw$$bn; \
	  iverilog $(IVFLAGS) $$def -I sim/tb -o $$l.vvp -s $$tb $(RTL) sim/tb/$$tb.sv \
	    > $$l.clog 2>&1 || { grep -v $(NOTES) $$l.clog; ok=0; continue; }; \
	  vvp $$l.vvp > $$l.log 2>&1; \
	  if grep -q '^FAIL' $$l.log; then \
	    grep '^FAIL' $$l.log | head -20; ok=0; \
	  elif ! grep -q '^PASS' $$l.log; then \
	    echo "FAIL: $$tb reported no PASS"; tail -20 $$l.log; ok=0; \
	  else \
	    echo "  $$(grep -E '^PASS' $$l.log | head -1), BUS_SYNC:WAIT:NEGATE $$b"; \
	  fi; \
	done; done; test $$ok -eq 1 && echo "PASS: sim"

# ---------------------------------------------------------------------------
# The system -- M4
#
# RD68021 is read-only (CLAUDE.md), and its checkout moves: its branch and
# uncommitted work are its own. So revisions of it are pinned here and
# exported, with `git archive` (which only reads the repository), to
# build/rd68021-<rev>, and everything below uses those copies:
#   RD68021_REV      the core: its RTL (the list from its own Makefile), its
#                    slave model, and the SunOS scripts. dda5ee6 is master
#                    with the RTE fix RD68884 asked for (1b57478) and posted
#                    writes.
#   RD68021_TME_REV  the TME element's sources. 6d91ae5 is the
#                    mh882_experiment branch, whose element has the RTL-FPU
#                    ("mh882") mode `make sunos` needs; master's has not.
# Not in `check`: it needs that repository, and minutes.
# ---------------------------------------------------------------------------
RD68021     ?= ../RD68021
RD68021_REV ?= dda5ee6
RD68021_TME_REV ?= 6d91ae5
RDSRC       := $(BUILD)/rd68021-$(RD68021_REV)
RDTME       := $(BUILD)/rd68021-$(RD68021_TME_REV)

$(BUILD)/rd68021-%/.exported:
	@test -d $(RD68021)/.git || { echo "FAIL: no RD68021 repository at $(RD68021)"; exit 1; }
	@rm -rf $(BUILD)/rd68021-$*; mkdir -p $(BUILD)/rd68021-$*
	@git -C $(RD68021) archive $* | tar -x -C $(BUILD)/rd68021-$*
	@touch $@
	@echo "  rd68021: $* exported to $(BUILD)/rd68021-$*"
rd68021: $(RDSRC)/.exported $(RDTME)/.exported
CROSS   := m68k-linux-gnu-
# sys_tb with the BIU this build names: the FPU on its own clock, or on the CPU's.
# BOARD=iisia7: the IIsiA7 Mini's whole board top instead (doc/boards.md), on
# a 32-bit port.
SYSDEF := $(if $(filter 1,$(BUS_SYNC)),-DSYS_BUS_SYNC -DSYS_BUS_SYNC_WAIT=$(BUS_SYNC_WAIT))
BOARD  ?=
ifeq ($(BOARD),iisia7)
SYSDEF    += -DSYS_BOARD_IISIA7
SYSEXTRA  := boards/iisia7_mini/rd68884_iisia7_top.sv sim/models/mmcme2_base.sv
SYS_PORTS := 32
endif
SYSPROGS  ?= fpu_m4 fpu_m5 fpu_m6 fpu_m7 fpu_m8 fpu fparith fparith-dbl
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

sys: ucode-check rd68021 $(patsubst %,$(BUILD)/programs/%.hex,$(SYSPROGS))
	@rd="$$(cd $(RDSRC) && $(MAKE) -s print-rtl | sed 's#\(\S*\)#$(RDSRC)/\1#g')"; \
	ok=1; for port in $(SYS_PORTS); do \
	  iverilog $(IVFLAGS) -DSYS_PORT=$$port $(SYSDEF) -o $(BUILD)/sys_tb_$$port.vvp -s sys_tb \
	    $(RTL) $(SYSEXTRA) $$rd $(RDSRC)/sim/models/rd68021_slave.sv sim/tb/sys_tb.sv \
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
# Clock counts against FPU section 8 -- doc/timing-divergences.md
#
# tools/cycles.py writes a program that times every instruction with sys_tb's
# clock count (32-bit port), and it runs four times: at the design's clocks
# (the FPU's core three times the bus clock), with the FPU's core thirty times
# as fast (what is left is the main processor and the protocol), with the
# same-clock BIU on the CPU's clock (BUS_SYNC=1, no wait states), and with the
# asynchronous BIU at the CPU's clock, for comparison. The design's counts and
# the same-clock ones are frozen in tools/cycles.frozen and
# tools/cycles-sync.frozen; a change either way fails until it is looked at and
# frozen again (FREEZE=1). The tables in the doc are regenerated from the
# measurement.
# ---------------------------------------------------------------------------
cycles: ucode-check rd68021
	@mkdir -p $(BUILD)/programs
	@python3 tools/cycles.py gen $(BUILD)/programs/cycles.S
	@$(CROSS)as -mcpu=68020 -m68881 -o $(BUILD)/programs/cycles.o $(BUILD)/programs/cycles.S
	@$(CROSS)ld --no-warn-rwx-segments -T sim/programs/flat.ld -o $(BUILD)/programs/cycles.elf $(BUILD)/programs/cycles.o
	@$(CROSS)objcopy -O verilog --verilog-data-width=1 $(BUILD)/programs/cycles.elf $(BUILD)/programs/cycles.hex
	@rd="$$(cd $(RDSRC) && $(MAKE) -s print-rtl | sed 's#\(\S*\)#$(RDSRC)/\1#g')"; \
	  for v in a: s:-DSYS_BUS_SYNC; do \
	    iverilog $(IVFLAGS) -DSYS_PORT=32 $${v#*:} -o $(BUILD)/cycles_tb_$${v%%:*}.vvp -s sys_tb \
	      $(RTL) $$rd $(RDSRC)/sim/models/rd68021_slave.sv sim/tb/sys_tb.sv \
	      > $(BUILD)/cycles_tb.clog 2>&1 || { grep -v $(NOTES) $(BUILD)/cycles_tb.clog | head; exit 1; }; \
	  done
	@for r in a:20:cycles a:2:cycles-fast a:60:cycles-slow s:60:cycles-sync; do \
	  set -- $$(echo $$r | tr : ' '); \
	  vvp $(BUILD)/cycles_tb_$$1.vvp +image=$(BUILD)/programs/cycles.hex +limit=4000000 \
	    +fpu_ns=$$2 +dump=$(BUILD)/$$3.dump > $(BUILD)/$$3.log 2>&1; \
	  grep -q '^PASS' $(BUILD)/$$3.log || { tail -3 $(BUILD)/$$3.log; exit 1; }; \
	done
	@$(PYENV) python3 tools/cycles.py check $(BUILD)/cycles.dump $(BUILD)/cycles-fast.dump \
	  --sync $(BUILD)/cycles-sync.dump --slow $(BUILD)/cycles-slow.dump \
	  --doc doc/timing-divergences.md $(if $(FREEZE),--freeze)

# ---------------------------------------------------------------------------
# SunOS 4.1.1 with RD68884 as the FPU -- doc/system.md (M9)
#
# RD68021's `make sunos-mh882` with RD68884 in mh882's place: a Sun-3/160 in
# TME (Inputs/ref/Run-Sun3-SunOS-4.1.1, copied to build/tme), booting an
# installed SunOS from disk, on two CPUs:
#   - TME's m68020 with its own MC68881, the reference;
#   - RD68021's core with RD68884 on its coprocessor interface, one
#     Verilator model (sim/tme/rd68021_tme_rd68884.sv) behind RD68021's own
#     TME element.
# drive.sh logs in as root, writes a C program with echo, compiles it with
# cc -f68881 on the machine and runs it (RD68021's sim/tme/sunos-fpu.cmds).
# The consoles must match, time stamps aside, and RD68884 must have answered
# coprocessor instructions. The disk image is made on the host by the
# installer in Run-Sun3-SunOS-4.1.1's diskimage/; SUNOS_IMG says where.
# ---------------------------------------------------------------------------
SUN3SRC   := Inputs/ref/Run-Sun3-SunOS-4.1.1
TMEB      := $(BUILD)/tme
TMEENV    := LTDL_LIBRARY_PATH=$(CURDIR)/$(TMEB)/inst/lib
SUNOS_IMG ?= $(HOME)/Run-Sun3-SunOS-4.1.1/diskimage/work/sunos411-sun3.img
SUNOSDIR  := $(BUILD)/sunos
SUNOS_SECS ?= 21600
# The time stamps: "Thu Sep 24 16:08:05 GMT 2026" and "Sep 24 16:08:10 sun3 ...".
STAMPS    := sed -E 's/[A-Z][a-z]{2} [A-Z][a-z]{2} [ 0-9][0-9] [0-9:]{8} [A-Z]+ [0-9]{4}/DATE/; s/^[A-Z][a-z]{2} [ 0-9][0-9] [0-9:]{8} /STAMP /'

tme: dirs rd68021
	@test -d $(SUN3SRC)/tme-0.8_up || { echo "FAIL: no $(SUN3SRC): git submodule update --init"; exit 1; }
	@rd="$$(cd $(RDSRC) && $(MAKE) -s print-rtl | sed 's#\(\S*\)#$(CURDIR)/$(RDSRC)/\1#g')"; \
	  BUS_SYNC=$(BUS_SYNC) BUS_SYNC_WAIT=$(BUS_SYNC_WAIT) TME_BUILD=$(CURDIR)/$(TMEB) \
	  RD_TME=$(CURDIR)/$(RDTME) \
	  sim/tme/build.sh $(CURDIR) $(CURDIR)/$(RDSRC) $$rd $(addprefix $(CURDIR)/,$(RTL)) $(CURDIR)/$(VLT)

sunos: tme
	@test -f "$(SUNOS_IMG)" || { echo "FAIL: sunos -- no disk image at $(SUNOS_IMG); set SUNOS_IMG"; exit 1; }
	@mkdir -p $(SUNOSDIR)
	@python3 $(RDSRC)/tools/sun3_rom.py $(SUN3SRC)/sun3-carrera-rev-3.0.bin $(SUNOSDIR)/prom.bin
	@sed 's/^console-device .*/console-device ttya/; s/^installed-#megs .*/installed-#megs 8/; s/^boot-device .*/boot-device sd(0,0,0)/' \
	    $(SUN3SRC)/sun3-carrera-eeprom.txt > $(SUNOSDIR)/eeprom.txt
	@$(TMEENV) $(TMEB)/inst/bin/tme-sun-eeprom < $(SUNOSDIR)/eeprom.txt > $(SUNOSDIR)/eeprom.bin 2>/dev/null
	@# tme-sun-idprom makes an IDPROM only when its input is a terminal.
	@cd $(SUNOSDIR) && script -qc "$(CURDIR)/$(TMEB)/inst/bin/tme-sun-idprom 3/150 \
	    8:0:20:11:22:33 > sun3-idprom.bin" /dev/null
	@mapfile -t C < $(RDSRC)/sim/tme/sunos-fpu.cmds; \
	for cpu in m68020 rd68021; do \
	  d=$(SUNOSDIR)/$$cpu; rm -rf $$d; mkdir -p $$d; \
	  cp $(SUNOSDIR)/prom.bin $(SUNOSDIR)/sun3-idprom.bin $$d/; \
	  cp $(SUNOSDIR)/eeprom.bin $$d/sun3-eeprom.bin; \
	  cp --sparse=always "$(SUNOS_IMG)" $$d/disk.img; \
	  arg="tme/ic/m68020 fpu-type m68881 fpu-compliance unknown fpu-incomplete line-f"; \
	  [ $$cpu = rd68021 ] && arg="tme/ic/rd68021 log rd68021.log fpu mh882"; \
	  sed "s|@CPU@|$$arg|" $(RDSRC)/sim/tme/SUNOS-DISK.in > $$d/SUNOS; \
	  (cd $$d && PROMPT='login: *|# *' T=$(CURDIR)/$(TMEB)/inst \
	      $(CURDIR)/$(RDSRC)/sim/tme/drive.sh SUNOS $(SUNOS_SECS) "$${C[@]}" > /dev/null 2>&1); \
	  tr -d '\r\000' < $$d/console.out | LC_ALL=C tr '\200-\377' '\000-\177' \
	    | $(STAMPS) > $$d/console.txt; \
	done
	@grep 'MH882:' $(SUNOSDIR)/rd68021/rd68021.log | tail -1 | sed 's/MH882/RD68884/; s/^/  /'
	@grep '^rd68021: [0-9]' $(SUNOSDIR)/rd68021/rd68021.log | tail -1 | sed 's/^/  /'
	@if cmp -s $(SUNOSDIR)/m68020/console.txt $(SUNOSDIR)/rd68021/console.txt \
	    && tail -c 2 $(SUNOSDIR)/rd68021/console.txt | grep -q '^# $$' \
	    && grep 'MH882:' $(SUNOSDIR)/rd68021/rd68021.log | tail -1 \
	       | grep -qv ' 0 general and 0 conditional'; then \
	  sed -n '/# \.\/t/,$$p' $(SUNOSDIR)/rd68021/console.txt | sed 's/^/    /'; echo; \
	  echo "  sunos: $$(wc -l < $(SUNOSDIR)/rd68021/console.txt) lines of console output identical to TME's m68020 with its MC68881, time stamps aside"; \
	  echo "PASS: sunos"; \
	else \
	  echo "FAIL: sunos -- the console output differs, or no coprocessor instruction ran"; \
	  diff $(SUNOSDIR)/m68020/console.txt $(SUNOSDIR)/rd68021/console.txt | head -20; exit 1; \
	fi

# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
check: ucode-check lint audit model-test iss-test sim
	@echo "PASS: check"

# ---------------------------------------------------------------------------
# Vendor front-ends. Each has its own target and none is in `check`.
# ---------------------------------------------------------------------------
synth: dirs $(if $(filter 1,$(BUS_SYNC)),$(BUILD)/rd68884_sync.xdc)
	@printf '%s\n' $(RTL) > $(BUILD)/rtl.f
	@set -o pipefail; scripts/vivado.sh -mode batch -nojournal -nolog \
	    -source scripts/synth.tcl -tclargs $(BUILD) $(TOP) $(XPART) $(XDC) $(GENERICS) \
	    > $(BUILD)/synth.log 2>&1 \
	  || { grep -E '^(SYNTH|ERROR|CRITICAL WARNING)' $(BUILD)/synth.log; exit 1; }
	@grep -E '^(SYNTH|CRITICAL WARNING)' $(BUILD)/synth.log

impl: dirs $(if $(filter 1,$(BUS_SYNC)),$(BUILD)/rd68884_sync.xdc)
	@printf '%s\n' $(addprefix $(CURDIR)/,$(RTL)) > $(BUILD)/rtl.f
	@cd $(BUILD) && $(CURDIR)/scripts/vivado.sh -mode batch -nojournal -nolog \
	    -source $(CURDIR)/scripts/impl.tcl -tclargs $(XPART) $(TOP) $(CURDIR) $(XDC) $(GENERICS) \
	    > impl.log 2>&1; rc=$$?; \
	  grep -E '^(RD68884|ERROR|CRITICAL WARNING)' impl.log; exit $$rc

# ---------------------------------------------------------------------------
# A board: the IIsiA7 Mini, an MC68881 for the Macintosh IIsi's MC68030 on its
# Processor Direct Slot -- doc/boards.md
#
# The pins come from the board's own LiteX platform in Inputs/IIsiFPGA (a
# submodule), through tools/board_pins.py. The core clock is 1000 MHz /
# BOARD_DIVIDE from the 100 MHz oscillator: 20 for 50 MHz; if timing fails,
# 22 (45.45), 25 (40), 30 (33.33). Writes the bitstream and the SPI flash image
# (.bin as the platform's flow, and .mcs) to build/iisia7_mini.
# ---------------------------------------------------------------------------
IISI_PLATFORM := Inputs/IIsiFPGA/IIsi-to-ztex-gateware/IIsiA7_Mini_pds.py
IISI_PART     ?= xc7a50tftg256-1
BOARD_DIVIDE  ?= 20
IISI_BUILD    := $(BUILD)/iisia7_mini

board-iisia7: dirs
	@test -f $(IISI_PLATFORM) || { echo "FAIL: no $(IISI_PLATFORM): git submodule update --init"; exit 1; }
	@mkdir -p $(IISI_BUILD)
	@python3 tools/board_pins.py $(IISI_PLATFORM) $(IISI_BUILD)/pins.xdc
	@printf '%s\n' $(addprefix $(CURDIR)/,$(RTL)) > $(IISI_BUILD)/rtl.f
	@cd $(IISI_BUILD) && $(CURDIR)/scripts/vivado.sh -mode batch -nojournal -nolog \
	    -source $(CURDIR)/boards/iisia7_mini/build.tcl \
	    -tclargs $(CURDIR) $(IISI_PART) $(BOARD_DIVIDE) > build.log 2>&1; rc=$$?; \
	  grep -E '^(RD68884|ERROR|CRITICAL WARNING)' build.log; exit $$rc

# quartus_map returns 0 on the thing that matters most, so the grep is the gate:
# a package-scoped constant inside a port expression becomes an implicit net,
# Warning (10236), and the netlist stops matching the source.
lint-quartus: dirs
	@printf '%s\n' $(RTL) > $(BUILD)/rtl.f
	@set -o pipefail; scripts/altera.sh quartus_sh -t scripts/quartus.tcl map \
	    $(BUILD) $(TOP) $(AFAMILY) $(APART) $(GENERICS) > $(BUILD)/quartus_map.log 2>&1 \
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
	    $(QBUILD) $(TOP) $(AFAMILY) $(APART) $(GENERICS) \
	    > $(QBUILD)/fit.log 2>&1 || { grep -E '^QUARTUS|Error' $(QBUILD)/fit.log | head; exit 1; }
	@if grep -q 'Warning (10236)' $(QBUILD)/fit.log; then \
	    echo "FAIL: quartus found an implicit net"; exit 1; fi
	@grep -E '^QUARTUS' $(QBUILD)/fit.log
	@grep -E 'Logic utilization|Total registers|Total block memory bits|Total DSP|Total RAM Blocks' \
	    $(QBUILD)/quartus_out/$(TOP).fit.summary

lint-questa: dirs
	@for b in $(BUILDS); do \
	  bs=$${b%%:*}; bw=$$(echo $$b | cut -d: -f2); bn=$${b##*:}; \
	  QUESTA_G="-GBUS_SYNC=$$bs -GBUS_SYNC_WAIT=$$bw -GDSACK_NEGATE=$$bn" scripts/questa.sh $(BUILD) $(TOP) $(RTL) \
	    || { echo "BUS_SYNC:WAIT:NEGATE $$b"; exit 1; }; \
	done
	@echo "  questa: ok"

print-rtl:
	@echo $(RTL)

clean:
	rm -rf $(BUILD)
