| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68884 -- one of each floating-point instruction shape, on RD68021 with
| RD68884 as its coprocessor (sim/tb/sys_tb.sv). Copied from RD68021's
| sim/programs/fpu.S and adapted:
|   check 1  the null frame's format word: only its version byte is checked.
|            The size byte is undefined (FPU 6.4.2); RD68884's is $18
|            (doc/model.md), mh882's $00.
|   check 46 the idle frame's size: $18, an MC68881's (FPU figure 6-4), where
|            mh882, an MC68882, has $38.
|   check 51 needs FLOGN, so it waits for the transcendentals (M7) behind
|            HAVE_TRANS.
|
| Every operand is chosen so that the exact result is representable: any
| correct MC68881/MC68882 gives the same bits, and the program checks them
| itself. Each check also appends the value it saw to the data buffer, so a
| failure can be read off the dump.
|
| The results block is at RES; sim/tb/sys_tb.sv reads it.
|
| Not built with crt0.S: this owns its vector table, so that an exception
| nobody asked for is recorded rather than run into the weeds.

        .equ    RES,  0xC000
        .equ    DONE, 0x444F4E45

        .section .vectors, "a"
        .long   0xF000                  | 0: the initial interrupt stack pointer
        .long   _start                  | 1: the initial program counter
        .rept   254
        .long   unexpected              | 2-255
        .endr

| One check: the long word in \src, which must be a data register, is \exp.
        .macro  ck id, exp, src
        move.l  \src, (%a4)+
        addq.l  #1, 20(%a5)
        addq.l  #1, 4(%a5)
        cmp.l   #\exp, \src
        beq.s   .Lok\@
        addq.l  #1, 8(%a5)
        tst.l   12(%a5)
        bne.s   .Lok\@
        move.l  #\id, 12(%a5)
.Lok\@:
        .endm

| The same for a long word in memory.
        .macro  ckm id, exp, mem
        move.l  \mem, %d7
        ck      \id, \exp, %d7
        .endm

        .text
        .globl  _start
_start:
        lea     RES, %a5
        lea     0x100(%a5), %a4
        clr.l   20(%a5)

| ---- The state frame a reset leaves: null (MC68881 UM 6.4.1) -------------
        fsave   -(%sp)
        move.l  (%sp)+, %d7
        andi.l  #0xFF000000, %d7        | the version byte (see the header)
        ck      1, 0, %d7

| ---- FMOVE in, from a data register and immediate, every integer size ------
        fmove.l #3, %fp0
        fadd.l  #4, %fp0
        fmove.l %fp0, %d0
        ck      2, 7, %d0

        fmove.b #-5, %fp1
        fmove.l %fp1, %d0
        ck      3, -5, %d0

        fmove.w #1000, %fp1
        fmove.l %fp1, %d0
        ck      4, 1000, %d0

        move.l  #-123456, %d1
        fmove.l %d1, %fp2
        fmove.l %fp2, %d0
        ck      5, -123456, %d0

        move.l  #0x3FC00000, %d1        | 1.5, single
        fmove.s %d1, %fp3
        fadd.s  %d1, %fp3               | 3.0
        fmove.s %fp3, %d0
        ck      6, 0x40400000, %d0

| ---- FMOVE in and out of memory: S, D, X, through several modes ------------
        lea     cdata, %a0
        fmove.s (%a0), %fp0             | 0.25
        fmove.d 4(%a0), %fp1            | 2.75
        fadd.x  %fp0, %fp1              | 3.0
        lea     scratch, %a1
        fmove.d %fp1, (%a1)
        ckm     7, 0x40080000, (%a1)
        ckm     8, 0, 4(%a1)

        fmove.x 12(%a0), %fp2           | -1.0, extended
        fmove.x %fp2, -(%sp)
        ckm     9, 0xBFFF0000, (%sp)+
        ckm     10, 0x80000000, (%sp)+
        ckm     11, 0, (%sp)+

        fmove.d #0r2.5, %fp4
        fmove.s %fp4, (%a1)+
        lea     scratch, %a1
        ckm     12, 0x40200000, (%a1)

| ---- FMOVE out to memory at B, W and L, rounding -----------------------
        fmove.l #-2, %fp0
        fmove.b %fp0, (%a1)
        move.b  (%a1), %d0
        ext.w   %d0
        ext.l   %d0
        ck      13, -2, %d0
        fmove.w %fp0, (%a1)
        move.w  (%a1), %d0
        ext.l   %d0
        ck      14, -2, %d0
        fmove.d #0r2.5, %fp0
        fmove.l %fp0, %d0               | round to nearest even
        ck      15, 2, %d0
        fmove.d #0r3.5, %fp0
        fmove.l %fp0, %d0
        ck      16, 4, %d0

| ---- Packed decimal, in and out, static and dynamic k --------------------
        fmove.p 24(%a0), %fp0           | 1.23E2
        fmove.l %fp0, %d0
        ck      17, 123, %d0
        fmove.p %fp0, (%a1){#3}
        ckm     18, 0x00020001, (%a1)
        ckm     19, 0x23000000, 4(%a1)
        ckm     20, 0, 8(%a1)
        moveq   #3, %d1
        clr.l   (%a1)
        fmove.p %fp0, (%a1){%d1}
        ckm     21, 0x00020001, (%a1)

| ---- Arithmetic -----------------------------------------------------------
        fmove.l #7, %fp0
        fdiv.l  #2, %fp0                | 3.5
        fmove.s %fp0, %d0
        ck      22, 0x40600000, %d0

        fmove.l #6, %fp1
        fmul.x  %fp0, %fp1              | 21
        fsub.l  #1, %fp1                | 20
        fmove.l %fp1, %d0
        ck      23, 20, %d0

        fsqrt.l #144, %fp2
        fmove.l %fp2, %d0
        ck      24, 12, %d0

        fneg.x  %fp2, %fp3
        fabs.x  %fp3
        fmove.l %fp3, %d0
        ck      25, 12, %d0

        fintrz.x %fp0, %fp4             | 3.5 -> 3
        fmove.l %fp4, %d0
        ck      26, 3, %d0

        fgetexp.l #8, %fp4
        fmove.l %fp4, %d0
        ck      27, 3, %d0

        fmove.l #3, %fp4
        fscale.l #4, %fp4               | 48
        fmove.l %fp4, %d0
        ck      28, 48, %d0

        fmove.l #17, %fp5
        fmod.l  #5, %fp5                | 2
        fmove.l %fp5, %d0
        ck      29, 2, %d0

| ---- FMOVECR ---------------------------------------------------------------
        fmovecr #0x32, %fp6             | 10^0
        fmove.l %fp6, %d0
        ck      30, 1, %d0
        fmovecr #0x33, %fp6             | 10^1
        fmove.l %fp6, %d0
        ck      31, 10, %d0
        fmovecr #0x0F, %fp6             | 0.0
        fmove.s %fp6, %d0
        ck      32, 0, %d0

| ---- FMOVEM.X: static lists both ways, and a dynamic list ---------------
        fmove.l #11, %fp0
        fmove.l #22, %fp1
        fmove.l #33, %fp2
        fmovem.x %fp0-%fp2, -(%sp)
        fmove.l #0, %fp0
        fmove.l #0, %fp1
        fmove.l #0, %fp2
        fmovem.x (%sp)+, %fp0-%fp2
        fmove.l %fp0, %d0
        ck      33, 11, %d0
        fmove.l %fp1, %d0
        ck      34, 22, %d0
        fmove.l %fp2, %d0
        ck      35, 33, %d0

        fmove.l #44, %fp3
        fmove.l #55, %fp7
        moveq   #-0x78, %d1             | 0x88: predecrement, bit n is FPn: fp3, fp7
        fmovem.x %d1, -(%sp)
        fmove.l #0, %fp3
        fmove.l #0, %fp7
        moveq   #-0x78, %d1             | 0x88: postincrement, bit 7-n is FPn: fp0, fp4
        fmovem.x (%sp)+, %d1
        fmove.l %fp0, %d0
        ck      36, 44, %d0
        fmove.l %fp4, %d0
        ck      37, 55, %d0

| ---- Control registers, singly and together, and FPIAR ---------------------
        fmove.l #0x30, %fpcr            | round toward plus infinity
        fmove.l %fpcr, %d0
        ck      38, 0x30, %d0
        fmove.l #0, %fpcr
        move.l  #0x10, %d1              | round toward zero
        lea     scratch, %a1
        move.l  %d1, (%a1)
        move.l  #0, 4(%a1)
        fmovem.l (%a1), %fpcr/%fpsr
        fmovem.l %fpcr/%fpsr/%fpiar, -(%sp)
        ckm     39, 0x10, (%sp)+
        addq.l  #8, %sp
        fmove.l #0x0400, %fpcr          | FPIAR is only loaded with an
here:   fadd.x  %fp1, %fp1              | exception enabled (MC68881 UM 2.4)
        fmove.l %fpiar, %d0
        fmove.l #0, %fpcr
        ck      40, here, %d0

| ---- Conditions: FTST, FCMP, FBcc both widths, FScc, FDBcc, FTRAPcc --------
        moveq   #0, %d2
        fmove.l #0, %fp0
        ftst.x  %fp0
        fbeq    .Lc1
        bset    #0, %d2                 | not reached
.Lc1:   fbne    .Lc2
        bset    #1, %d2                 | reached
.Lc2:   fmove.l #5, %fp1
        fcmp.l  #3, %fp1                | 5 > 3
        fbgt.l  .Lc3
        bset    #2, %d2                 | not reached
.Lc3:   fblt.l  .Lc4
        bset    #3, %d2                 | reached
.Lc4:   ck      41, 0x0A, %d2

        moveq   #0, %d0
        fsgt    %d0                     | the last compare: 5 > 3
        ck      42, 0xFF, %d0
        fslt    %d0
        ck      43, 0, %d0

        moveq   #0, %d2
        moveq   #3, %d1
.Lc5:   addq.l  #1, %d2
        fdbf    %d1, .Lc5                 | false: always loops until -1
        ck      44, 4, %d2

        move.l  #trapcc, 7*4
        clr.l   %d3
        ftrapgt                         | taken
        ftraplt                         | not taken
        ck      45, 1, %d3

| ---- FSAVE and FRESTORE: an idle frame, and putting it back ----------------
        fmove.l #77, %fp0
        fsave   -(%sp)
        move.l  (%sp), %d0
        andi.l  #0x00FF0000, %d0        | the frame's size: $18 on an MC68881
        ck      46, 0x00180000, %d0
        fmove.l #0, %fp0
        frestore (%sp)+
        fmove.l %fp0, %d0               | the registers are not in the frame
        ck      47, 0, %d0

| ---- FNOP, and a null FRESTORE resets the FPU -------------------------
        fmove.l #0x30, %fpcr
        fnop
        clr.l   -(%sp)
        frestore (%sp)+
        fmove.l %fpcr, %d0
        ck      48, 0, %d0

| ---- An enabled exception: divide by zero, taken as vector 50 at the next
| ---- floating-point instruction (MC68881 UM 6.1) -------------------------
        move.l  #fpdz, 50*4
        clr.l   %d3
        fmove.l #0x0400, %fpcr          | DZ enabled
        fmove.l #1, %fp0
        fmove.l #0, %fp1
        fdiv.x  %fp1, %fp0
        fnop                            | the exception is taken here
        ck      49, 1, %d3
        fmove.l %fpcr, %d0              | the handler's null FRESTORE reset it
        ck      50, 0, %d0

| ---- SunOS's test for an MC68881 older than the A93N mask (libc's ma93n.o,
| ---- run by the start-up of every cc -f68881 program), and gradual underflow
| ---- in the conversions (MC68881 UM 3.3.2, 4.5.1) ---------------------------
        .ifdef  HAVE_TRANS
        flogn.d tiny, %fp0              | ln(1 + 2^-52), as single: 2^-52
        fmove.s %fp0, %d0
        ck      51, 0x25800000, %d0
        .endif
        fmove.d tiny+8, %fp0            | 2^-126 (1 - 2^-28) rounds to 2^-126
        fmove.s %fp0, %d0
        ck      52, 0x00800000, %d0
        fmove.d tiny+16, %fp0           | 2^-149, the smallest single denormal
        fmove.s %fp0, %d0
        ck      53, 0x00000001, %d0
        fmove.d tiny+24, %fp0           | 2^-1030, a double denormal, in and out
        lea     scratch, %a1
        fmove.d %fp0, (%a1)
        ckm     54, 0x00001000, (%a1)

| ---- The end --------------------------------------------------------------
        move.l  #DONE, (%a5)
_done:  bra.s   _done

| FTRAPcc: count, and return past the trap (the frame's PC is the next one).
trapcc: addq.l  #1, %d3
        rte

| The divide-by-zero handler. It does nothing for the exception but count it,
| and puts the FPU back to its reset state with a null FRESTORE -- which also
| takes the exception away, so RTE reruns the instruction it interrupted.
fpdz:   addq.l  #1, %d3
        clr.l   -(%sp)
        frestore (%sp)+
        rte

| Anything else: record the vector and stop.
unexpected:
        move.w  6(%sp), %d0
        andi.l  #0xFFF, %d0
        lsr.l   #2, %d0
        move.l  %d0, 16(%a5)
        move.l  #DONE, (%a5)
1:      bra.s   1b

        .data
        .balign 4
cdata:  .long   0x3E800000              | +0:  0.25, single
        .long   0x40060000, 0           | +4:  2.75, double
        .long   0xBFFF0000, 0x80000000, 0 | +12: -1.0, extended
        .long   0x00020001, 0x23000000, 0 | +24: 1.23E2, packed
tiny:   .long   0x3FF00000, 0x00000001 | +0:  1 + 2^-52
        .long   0x380FFFFF, 0xFE000000 | +8:  2^-126 (1 - 2^-28)
        .long   0x36A00000, 0          | +16: 2^-149
        .long   0x00001000, 0          | +24: 2^-1030
scratch: .long  0, 0, 0, 0
