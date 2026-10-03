#!/bin/bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# RD68884 -- build TME with RD68021's core, and RD68884 as its coprocessor, as
# a CPU element (doc/system.md).
#
#   sim/tme/build.sh <repo-root> <rd68021-root> <rtl files...>
#
# Copied from RD68021's sim/tme/build.sh and adapted. The TME element and its
# helpers are RD68021's (sim/tme/rd68021.c, rd68021_fpu.c, rd68021_model.h,
# public.vlt), used as they stand; the model is this repository's
# sim/tme/rd68021_tme_rd68884.sv behind sim/tme/rd68021_model.cpp, in the
# element's RTL-FPU ("mh882") mode. TME comes from this repository's
# Inputs/ref/Run-Sun3-SunOS-4.1.1 and, Inputs/ being immutable, is copied to
# build/tme/src and built there. The changes made to the copy are RD68021's,
# described below in its own words.
set -euo pipefail
ROOT=$1; RD=$2; shift 2
B=${TME_BUILD:-$ROOT/build/tme}
SRC=$B/src
TME=$ROOT/Inputs/ref/Run-Sun3-SunOS-4.1.1/tme-0.8_up
AUTO="ACLOCAL=true AUTOCONF=true AUTOHEADER=true AUTOMAKE=true"
CFL="-O2 -g -std=gnu89 -fcommon -Wno-error -Wno-implicit-function-declaration -Wno-int-conversion -Wno-incompatible-pointer-types"

mkdir -p $B
if [ ! -f $SRC/.configured ]; then
  rm -rf $SRC
  cp -a $TME $SRC
  (cd $SRC && (make distclean >/dev/null 2>&1 || true) \
     && CFLAGS="$CFL" ./configure --disable-shared --disable-warnings \
        --enable-ltdl-install --prefix=$B/inst > $B/configure.log 2>&1)
  touch $SRC/.configured
fi

# The core, as a static library behind a C interface.
V=$B/vobj
verilator --cc -O3 --top-module rd68021_tme_rd68884 --prefix Vrd68021_top \
  -Wno-fatal --Mdir $V -CFLAGS "-O2" $RD/rtl/rd68021.vlt $RD/sim/tme/public.vlt \
  "$@" $ROOT/sim/tme/rd68021_tme_rd68884.sv > $B/verilator.log 2>&1
MDEF=-DRD_MH882
make -s -C $V -f Vrd68021_top.mk -j8 > $B/vmake.log 2>&1
VINC=$(verilator --getenv VERILATOR_ROOT)/include
g++ -O2 $MDEF -c -I$V -I$VINC -I$VINC/vltstd -I$RD/sim/tme $ROOT/sim/tme/rd68021_model.cpp -o $V/rd68021_model.o
# One relocatable object holding the wrapper, the model and the Verilator
# runtime, with every symbol but the C interface made local. libtool builds a
# table of every global symbol it can see for its static module preloading, and
# the runtime's thread-local variables cannot be named from it: "TLS reference
# ... mismatches non-TLS reference in tmeshS.o".
ld -r -o $V/rd68021_all.o $V/rd68021_model.o \
   --whole-archive $V/Vrd68021_top__ALL.a $V/libverilated.a --no-whole-archive
nm -g --defined-only $V/rd68021_model.o | awk '$3 ~ /^rdm_/ {print $3}' > $V/keep.txt
objcopy --keep-global-symbols=$V/keep.txt $V/rd68021_all.o $V/rd68021_lib.o
rm -f $V/librd68021.a
ar rcs $V/librd68021.a $V/rd68021_lib.o
MODEL_LIBS="$V/librd68021.a -lstdc++ -lpthread"

# Time. TME's scheduler and the Sun-3's clock chip run on host time, and the
# core runs several times slower than a real 16.67 MHz MC68020, so every clock
# tick arrived after a fraction of the instructions it should have and a kernel
# spent most of its time in its clock interrupt. With these, a CPU element can
# install a hook that makes time simulated: tme_gettimeofday asks the hook, and
# the Intersil 7170 reads its time of day through tme_gettimeofday rather than
# gettimeofday. With no hook installed -- TME's own CPUs -- nothing changes.
python3 - "$SRC" <<'PYEOF'
import sys
src = sys.argv[1]
def patch(path, old, new):
    s = open(path).read()
    if new in s:
        return
    assert old in s, (path, old[:40])
    open(path, 'w').write(s.replace(old, new, 1))
patch(src + '/libtme/threads-sjlj.c',
      '/* this returns a reasonably current time: */\nvoid\ntme_sjlj_gettimeofday(struct timeval *now)\n{\n',
      '/* RD68021: a CPU element may make time simulated. */\n'
      'void (*tme_rd_time_hook) _TME_P((struct timeval *));\n\n'
      '/* this returns a reasonably current time: */\nvoid\ntme_sjlj_gettimeofday(struct timeval *now)\n{\n'
      '  if (tme_rd_time_hook != NULL) {\n    (*tme_rd_time_hook)(now);\n    return;\n  }\n')
patch(src + '/tme/threads.h',
      'void tme_sjlj_gettimeofday _TME_P((struct timeval *));',
      'void tme_sjlj_gettimeofday _TME_P((struct timeval *));\n'
      'extern void (*tme_rd_time_hook) _TME_P((struct timeval *));')
# ... and, for comparison with the core, TME's own m68k can trace SunOS system
# calls: RD68021_SYSCALLS set prints D0 at every TRAP #0, as tme/ic/rd68021 does.
patch(src + '/ic/m68k/m68k-misc.c',
      '  /* stack the frame format and vector offset, unless this is a 68000: */\n'
      '  vector_offset = ((tme_uint16_t) vector) << 2;\n',
      '  /* stack the frame format and vector offset, unless this is a 68000: */\n'
      '  vector_offset = ((tme_uint16_t) vector) << 2;\n'
      '  if (vector == 32 && !TME_M68K_SEQUENCE_RESTARTING && getenv("RD68021_SYSCALLS"))\n'
      '    fprintf(stderr, "SYSCALL d0 %lu pc %08lx\\n", (unsigned long) ic->tme_m68k_ireg_d0, (unsigned long) ic->tme_m68k_ireg_pc);\n')
patch(src + '/ic/m68k/m68k-misc.c',
      '#include "m68k-impl.h"\n',
      '#include "m68k-impl.h"\n#include <stdio.h>\n#include <stdlib.h>\n')
# ... and an instruction trace, in user mode, between two system-call numbers:
# RD68021_UTRACE=from:to, counted the same way on both CPUs.
patch(src + '/ic/m68k/m68k-misc.c',
      '#include <stdio.h>\n#include <stdlib.h>\n',
      '#include <stdio.h>\n#include <stdlib.h>\nunsigned long rd68021_syscall_n;\n')
patch(src + '/ic/m68k/m68k-misc.c',
      '  if (vector == 32 && !TME_M68K_SEQUENCE_RESTARTING && getenv("RD68021_SYSCALLS"))\n',
      '  if (vector == 32 && !TME_M68K_SEQUENCE_RESTARTING) rd68021_syscall_n++;\n'
      '  if (vector == 32 && !TME_M68K_SEQUENCE_RESTARTING && getenv("RD68021_SYSCALLS"))\n')
patch(src + '/ic/m68k/m68k-misc.c',
      'unsigned long rd68021_syscall_n;\n',
      'unsigned long rd68021_syscall_n;\n'
      'void rd68021_utrace(unsigned long pc, unsigned long d0, unsigned long d1, unsigned sr)\n'
      '{\n'
      '  static long from = -2, to;\n'
      '  char *e, *c;\n'
      '  if (from == -2) {\n'
      '    from = -1;\n'
      '    if ((e = getenv("RD68021_UTRACE")) != NULL) { from = strtol(e, &c, 0); to = strtol(c + 1, NULL, 0); }\n'
      '  }\n'
      '  if (from >= 0 && (long) rd68021_syscall_n >= from && (long) rd68021_syscall_n < to && !(sr & 0x2000))\n'
      '    fprintf(stderr, "U %08lx d0 %08lx d1 %08lx sr %04x\\n", pc, d0, d1, sr);\n'
      '}\n')
patch(src + '/ic/m68k/m68k-execute.c',
      '    /* an instruction has ended: */\n    tme_m68k_verify_end(ic, func);\n',
      '    /* an instruction has ended: */\n    tme_m68k_verify_end(ic, func);\n'
      '    { extern void rd68021_utrace(unsigned long, unsigned long, unsigned long, unsigned);\n'
      '      rd68021_utrace(ic->tme_m68k_ireg_pc, ic->tme_m68k_ireg_d0, ic->tme_m68k_ireg_d1,\n'
      '                     ic->tme_m68k_ireg_sr); }\n')
# The MC68881 behind the coprocessor interface -- sim/tme/rd68021_fpu.c, compiled
# as part of m6888x.c so that it reaches that file's static helpers, and one
# return in tme_m68k_fmove_rm so that a converted result can be taken before it
# is stored.
patch(src + '/ic/m68k/m6888x.c',
      '  /* if this is a data register direct EA: */\n  if (ea_mode == 0) {\n\n    switch (ea_size) {\n',
      '  /* RD68021: the result is wanted, not stored -- sim/tme/rd68021_fpu.c */\n'
      '  { extern int rd68021_fpu_capture; if (rd68021_fpu_capture) TME_M68K_INSN_OK; }\n\n'
      '  /* if this is a data register direct EA: */\n  if (ea_mode == 0) {\n\n    switch (ea_size) {\n')
m = src + '/ic/m68k/m6888x.c'
s2 = open(m).read()
if '#include "rd68021_fpu.c"' not in s2:
    open(m, 'a').write('\n/* RD68021 */\n#include "rd68021_fpu.c"\n')
p = src + '/ic/isil7170.c'
s = open(p).read()
s = s.replace('gettimeofday(&now, NULL);', 'tme_gettimeofday(&now);')
open(p, 'w').write(s)
PYEOF

# The element, into the m68k module.
cp $RD/sim/tme/rd68021.c $RD/sim/tme/rd68021_model.h $RD/sim/tme/rd68021_fpu.c $SRC/ic/m68k/
# m6888x.c includes rd68021_fpu.c, which make cannot see: rebuild it by hand.
rm -f $SRC/ic/m68k/m6888x.lo $SRC/ic/m68k/m6888x.o
grep -q 'rd68021.lo' $SRC/ic/m68k/Makefile \
  || sed -i 's/^\(\tm68010.lo m68020.lo m6888x.lo\)$/\1 rd68021.lo/' $SRC/ic/m68k/Makefile
grep -q 'rd68021.lo' $SRC/ic/m68k/Makefile || { echo "sim/tme/build.sh: could not add rd68021.lo"; exit 1; }

# Build. Serially: the modules' all-local rule copies a library before -j has
# built it.
# ... and only tmesh links the model, so only its Makefile names it. tmesh does
# not depend on the preloaded module archives, so make would not relink it when
# the element changes and install would copy the old one: remove it first.
sed -i "s|^LIBS = .*|LIBS = $MODEL_LIBS|" $SRC/tmesh/Makefile
rm -f $SRC/tmesh/tmesh
(cd $SRC && make $AUTO > $B/make.log 2>&1 && make $AUTO install > $B/install.log 2>&1) \
  || { grep -E 'error:|\*\*\* \[|undefined reference' $B/make.log | head -20; exit 1; }
echo "  tme: built with tme/ic/rd68021 in $B/inst"
