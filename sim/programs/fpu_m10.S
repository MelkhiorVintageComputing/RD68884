| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68885 -- the R3 system test: the MC68882's instruction overlap, on a real
| MC68020 (RD68021). Built for both models (the Makefile's FRAMESYM_*, MODEL);
| every check holds on both, the frame checks with the model's own frames.
|
| On an MC68882 the instruction after a long one goes to the conversion unit
| (CU) while the APU computes (FPU 5.1.1.2, doc/rd68885.md); on an MC68881 it
| waits. What the program sees must be the same (FPU 5.1.1: "the system
| apparently executes the instructions in sequence"), exceptions included:
|
|   A  overlapped instructions, their results
|   B  an exception in the APU while a B source waits in the CU: reported by
|      it mid-instruction (FPU 6.1) on an MC68882, a busy frame; on an
|      MC68881 pre-instruction, an idle frame. The handler is figure 5-6's
|   C  the same with an S source, released (CA = 0): the next instruction
|      reports it; the MC68882's idle frame carries the CU's instruction
|   D  a bus error in the middle of a CA = 0 operand transfer into the CU
|   E  an invalid effective address in the CU's dialog: AB aborts it alone
|      (FPU 7.2.2), the APU's instruction completes
|   F  a conditional after overlapped instructions (table 5-6)
|
|   RES+$00  'DONE'            RES+$04  checks made      RES+$08  checks failed
|   RES+$0C  first failing check, RES+$10  an unexpected exception's vector
|   RES+$18  first failing value, RES+$1C  what was wanted
|
| FPU = MC68881UM_split, UM = MC68020UM_split.

        .equ    STACK_TOP, 0x0000F000
        .equ    RES,       0x0000C000
        .equ    IRQ,       RES + 0x80
        .equ    FAULT,     RES + 0x84
        .equ    BUF,       0x0000D000
        .equ    BUF2,      0x0000D100

        .section .vectors, "a"
        .long   STACK_TOP, _start
        .long   h_berr                      | 2  bus error
        .rept   8
        .long   unexpected                  | 3-10
        .endr
        .long   h_fline                     | 11 F-line
        .rept   36
        .long   unexpected                  | 12-47
        .endr
        .rept   7
        .long   h_fp                        | 48-54 floating point
        .endr
        .rept   201
        .long   unexpected                  | 55-255
        .endr

        .text
        .globl  _start
_start:
        andi    #0x00, %ccr
        move.l  #STACK_TOP, %sp
        lea     RES, %a0
        moveq   #31, %d0
1:      clr.l   (%a0)+
        dbf     %d0, 1b
        move.w  #0x2700, %sr                | supervisor, no interrupts

| ---------------------------------------------------------------------------
| A. Overlapped instructions: a long one, then ones the CU takes
| ---------------------------------------------------------------------------
        fmove.l #4, %fp0
        fmove.l #10, %fp1
        fmove.l #3, %fp2
        fsqrt.x %fp0                        | the APU: 2
        fadd.d  d_one, %fp1                 | the CU, CA = 0: 11
        fmul.s  s_two, %fp2                 | a third: 6
        fadd.l  #5, %fp1                    | a B, W, L source: 16
        fdiv.x  %fp0, %fp2                  | register to register: 3
        fmovecr #0x32, %fp3                 | FMOVECR, from the CU: 1
        fsqrt.x %fp1                        | 4
        fadd.x  %fp3, %fp3                  | 2
        fmove.l %fp0, %d0
        moveq   #2, %d1
        bsr     check
        fmove.l %fp1, %d0
        moveq   #4, %d1
        bsr     check
        fmove.l %fp2, %d0
        moveq   #3, %d1
        bsr     check
        fmove.l %fp3, %d0
        moveq   #2, %d1
        bsr     check

| ---------------------------------------------------------------------------
| B. An overflow in the APU while a B source waits in the CU
| ---------------------------------------------------------------------------
        fmove.l #0x1000, %fpcr              | OVFL enabled
        fmove.x huge, %fp0
        fmove.l #7, %fp2
        clr.l   nexc
excb:   fdiv.x  tiny, %fp0                  | overflows: vector 53
        fadd.b  #1, %fp2                    | in the CU, PC passed: 8
        move.l  nexc, %d0
        moveq   #1, %d1
        bsr     check                       | one exception
        move.l  excvec, %d0
        moveq   #53, %d1
        bsr     check
        move.l  excpc, %d0
        move.l  #excb, %d1
        bsr     check                       | FPIAR: the FDIV (FPU 7.2.10)
        move.l  excframe, %d0
        clr.w   %d0
        .if MODEL == 68882
        move.l  #(FRAME_BUSY << 16), %d1    | mid-instruction: the CU's dialog
        .else
        move.l  #(FRAME_IDLE << 16), %d1
        .endif
        bsr     check
        fmove.l %fp2, %d0
        moveq   #8, %d1
        bsr     check                       | the FADD.B, once

| ---------------------------------------------------------------------------
| C. The same with an S source: released, held in the CU; FNOP reports it
| ---------------------------------------------------------------------------
        fmove.x huge, %fp0
        fmove.l #9, %fp3
        clr.l   nexc
excc:   fdiv.x  tiny, %fp0
cmdc:   fadd.s  s_two, %fp3                 | CA = 0, the CU holds it: 11
        fnop
        move.l  nexc, %d0
        moveq   #1, %d1
        bsr     check
        move.l  excpc, %d0
        move.l  #excc, %d1
        bsr     check
        move.l  excframe, %d0
        clr.w   %d0
        move.l  #(FRAME_IDLE << 16), %d1
        bsr     check
        .if MODEL == 68882
        move.l  exccu, %d0                  | the frame's CU word (doc/model.md)
        andi.l  #0xFFFF8000, %d0
        move.l  #0x45A20000, %d1            | FADD.S <ea>,FP3, held
        bsr     check
        .endif
        fmove.l %fp3, %d0
        moveq   #11, %d1
        bsr     check
        fmove.l #0, %fpcr

| ---------------------------------------------------------------------------
| D. A bus error on the second long word of a CA = 0 transfer into the CU
| ---------------------------------------------------------------------------
        fmove.l #16, %fp0
        fmove.l #1, %fp1
        fsqrt.x %fp0
        fadd.d  ddata, %fp1
        fmove.x %fp1, BUF
        fmove.l #16, %fp0
        fmove.l #1, %fp1
        move.l  #ddata + 4, FAULT
        clr.l   frames
        fsqrt.x %fp0
        fadd.d  ddata, %fp1
        fmove.x %fp1, BUF2
        bsr     cmp_bufs3
        move.l  frames, %d0
        moveq   #1, %d1
        bsr     check
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #(FRAME_BUSY << 16), %d1
        bsr     check
        fmove.l %fp0, %d0
        moveq   #4, %d1
        bsr     check

| ---------------------------------------------------------------------------
| E. FADD.D D0,FP1: no data register holds a double. The CU's evaluate
| <ea> refuses it; the main processor aborts (AB) and takes the F-line
| trap (UM 7.4.2.2). The FSQRT before it completes.
| ---------------------------------------------------------------------------
        fmove.l #64, %fp0
        fmove.l #5, %fp1
        clr.l   nfline
        fsqrt.x %fp0
        .word   0xF200, 0x54A2              | FADD.D D0,FP1
        move.l  nfline, %d0
        moveq   #1, %d1
        bsr     check
        fmove.l %fp0, %d0
        moveq   #8, %d1
        bsr     check
        fmove.l %fp1, %d0
        moveq   #5, %d1
        bsr     check

| ---------------------------------------------------------------------------
| F. A conditional after overlapped instructions (table 5-6: it waits for
| both units)
| ---------------------------------------------------------------------------
        fmove.l #81, %fp1
        fsqrt.x %fp1                        | 9
        fmove.x %fp1, %fp2                  | register to register, in the CU
        fmul.x  %fp2, %fp2                  | 81
        fcmp.l  #81, %fp2
        fseq    %d0
        andi.l  #0xFF, %d0
        move.l  #0xFF, %d1
        bsr     check

| ---------------------------------------------------------------------------
| Done
| ---------------------------------------------------------------------------
        move.l  #0x444F4E45, RES
        .globl  _done
_done:  bra     _done

| The floating-point exceptions: FPU figure 5-6's handler, which also
| records the vector, FPIAR (FPU 5.2.2: before any instruction but FMOVEM),
| the frame's format word and its first CU word.
h_fp:   fsave   -(%sp)
        fmove.l %fpiar, excpc
        addq.l  #1, nexc
        move.l  (%sp), excframe
        move.l  8(%sp), exccu
        movem.l %d0-%d1, -(%sp)
        clr.l   %d0
        move.b  9(%sp), %d0                 | the frame's size (past D0-D1)
        move.w  8+4+6(%sp,%d0.l), %d1       | the exception frame's vector offset
        andi.l  #0x0FFF, %d1
        lsr.l   #2, %d1
        move.l  %d1, excvec
        tst.b   8(%sp)
        beq.s   1f                          | a null frame
        bset    #3, 8(%sp,%d0.l)            | EXC PEND inactive (bit 27 of the BIU flags)
1:      movem.l (%sp)+, %d0-%d1
        frestore (%sp)+
        rte

| F-line: count, and go past the 4-byte instruction.
h_fline:
        addq.l  #1, nfline
        addq.l  #4, 2(%sp)
        rte

| The bus error: a context switch around the FPU (as fpu_m8.S); RTE runs
| the faulted access again (UM 7.5.2.8).
h_berr: fsave   -(%sp)
        tst.l   frames
        bne.s   1f
        move.l  (%sp), frame0
1:      addq.l  #1, frames
        fmovem.x %fp0-%fp7, -(%sp)
        fmovem.l %fpcr/%fpsr/%fpiar, -(%sp)
        fmove.l #0x0030, %fpcr              | another task's work
        fmove.l #99, %fp0
        fadd.l  #1, %fp3
        fmul.x  %fp4, %fp4
        fmovem.l (%sp)+, %fpcr/%fpsr/%fpiar
        fmovem.x (%sp)+, %fp0-%fp7
        frestore (%sp)+
        rte

cmp_bufs3:
        moveq   #2, %d6
        lea     BUF, %a2
        lea     BUF2, %a3
2:      move.l  (%a3)+, %d0
        move.l  (%a2)+, %d1
        bsr     check
        dbf     %d6, 2b
        rts

| check: d0 is what was found, d1 what was wanted.
check:  addq.l  #1, RES+4
        cmp.l   %d1, %d0
        beq.s   1f
        addq.l  #1, RES+8
        tst.l   RES+12
        bne.s   1f
        move.l  RES+4, RES+12
        move.l  %d0, RES+0x18
        move.l  %d1, RES+0x1C
1:      rts

unexpected:
        move.w  6(%sp), %d0
        andi.l  #0x0FFF, %d0
        lsr.l   #2, %d0
        move.l  %d0, RES+0x10
        move.l  #0x444F4E45, RES
        bra     _done

        .data
        .balign 4
huge:   .long   0x7FFE0000, 0x80000000, 0x00000000   | 2^16383
tiny:   .long   0x00010000, 0x80000000, 0x00000000   | 2^-16382
d_one:  .long   0x3FF00000, 0x00000000               | 1.0, double
ddata:  .long   0x400921FB, 0x54442D18               | pi, double
s_two:  .long   0x40000000                           | 2.0, single
nexc:   .long   0
excvec: .long   0
excpc:  .long   0
excframe: .long 0
exccu:  .long   0
nfline: .long   0
frames: .long   0
frame0: .long   0
