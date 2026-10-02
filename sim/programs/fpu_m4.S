| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68884 -- the M4 system test: every coprocessor dialog, on a real MC68020
| (RD68021), with no arithmetic. Self-checking; sim/tb/sys_tb.sv waits for the
| results block and reports it.
|
|   RES+$00  'DONE'            RES+$04  checks made      RES+$08  checks failed
|   RES+$0C  first failing check, RES+$10  an unexpected exception's vector
|   RES+$18  first failing value, RES+$1C  what was wanted
|
| FPU = MC68881UM_split, UM = MC68020UM_split.

        .equ    STACK_TOP, 0x0000F000
        .equ    RES,       0x0000C000

| ---------------------------------------------------------------------------
| Vectors: everything unexpected, except what the tests provoke on purpose
| ---------------------------------------------------------------------------
        .section .vectors, "a"
        .long   STACK_TOP, _start
        .rept   5
        .long   unexpected                  | 2-6
        .endr
        .long   h_trapcc                    | 7  FTRAPcc (FPU 6.2.1)
        .rept   3
        .long   unexpected                  | 8-10
        .endr
        .long   h_fline                     | 11 F-line (FPU 6.1.11)
        .long   unexpected                  | 12
        .long   unexpected                  | 13 protocol violation: never
        .long   h_format                    | 14 format error (FPU 6.2.8)
        .rept   33
        .long   unexpected                  | 15-47
        .endr
        .long   h_bsun                      | 48 BSUN (FPU 6.1.1)
        .rept   6
        .long   h_arith                     | 49-54 the arithmetic exceptions
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
1:      clr.l   (%a0)+                      | the results block
        dbf     %d0, 1b

| ---------------------------------------------------------------------------
| 1. After reset the FPU is in the null state (FPU 6.4.3.1)
| ---------------------------------------------------------------------------
        fsave   -(%sp)
        move.l  (%sp)+, %d0
        clr.w   %d0
        swap    %d0
        lsr.w   #8, %d0                     | the version byte
        moveq   #0, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 2. FMOVE.X in, between registers, out (FPU 7.5.1.1-7.5.1.3)
| ---------------------------------------------------------------------------
        fmove.x xa, %fp0
        fmove.x %fp0, %fp1
        fmove.x %fp1, buf
        lea     buf, %a1
        lea     xa, %a2
        moveq   #3, %d6
        bsr     cmplongs
        fmove.x xb, %fp2
        fmove.x %fp2, -(%sp)                | predecrement out
        move.l  %sp, %a1
        lea     xb, %a2
        moveq   #3, %d6
        bsr     cmplongs
        lea     12(%sp), %sp

| ---------------------------------------------------------------------------
| 3. FMOVEM.X, static and dynamic lists, both directions (FPU 7.5.1.5)
| ---------------------------------------------------------------------------
        fmove.x xa, %fp0
        fmove.x xb, %fp1
        fmove.x xc, %fp2
        fmove.x xd, %fp3
        fmovem.x %fp0-%fp3, -(%sp)          | FP0 at the lowest address
        move.l  %sp, %a1
        lea     xa, %a2
        moveq   #12, %d6
        bsr     cmplongs
        moveq   #0x0F, %d2                  | FP4-FP7, FP0 in bit 7 (mode 11)
        fmovem.x (%sp)+, %d2
        fmove.x %fp4, buf
        fmove.x %fp5, buf+12
        fmove.x %fp6, buf+24
        fmove.x %fp7, buf+36
        lea     buf, %a1
        lea     xa, %a2
        moveq   #12, %d6
        bsr     cmplongs
        lea     buf, %a3
        fmovem.x %fp1/%fp3, (%a3)           | control addressing mode
        lea     buf, %a1
        lea     xb, %a2
        moveq   #3, %d6
        bsr     cmplongs
        lea     buf+12, %a1
        lea     xd, %a2
        moveq   #3, %d6
        bsr     cmplongs
        moveq   #0, %d2                     | an empty dynamic list
        fmovem.x %d2, (%a3)

| ---------------------------------------------------------------------------
| 4. The control registers (FPU 7.5.1.4)
| ---------------------------------------------------------------------------
        fmove.l #0x00000030, %fpcr
        fmove.l %fpcr, %d0
        moveq   #0x30, %d1
        bsr     check
        fmove.l #0x0FFFFFF8, %fpsr
        fmove.l %fpsr, %d0
        move.l  #0x0FFFFFF8, %d1
        bsr     check
        fmove.l #0x12345678, %fpiar
        fmove.l %fpiar, %d0
        move.l  #0x12345678, %d1
        bsr     check
        fmovem.l %fpcr/%fpsr/%fpiar, -(%sp)
        move.l  (%sp), %d0
        moveq   #0x30, %d1
        bsr     check
        move.l  8(%sp), %d0
        move.l  #0x12345678, %d1
        bsr     check
        move.l  #0, (%sp)
        move.l  #0, 4(%sp)
        fmovem.l (%sp)+, %fpcr/%fpsr/%fpiar
        fmove.l %fpiar, %a4                 | FPIAR alone to an address register
        move.l  %a4, %d0
        move.l  #0x12345678, %d1
        bsr     check
        fmove.l %fpsr, %d0
        moveq   #0, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 5. Every predicate, by FScc, for seven condition codes (FPU 4.4)
| ---------------------------------------------------------------------------
        lea     cctab, %a5
        moveq   #6, %d7
5:      move.l  (%a5)+, %d0
        fmove.l %d0, %fpsr
        moveq   #0, %d3
        .irp    p, f,eq,ogt,oge,olt,ole,ogl,or,un,ueq,ugt,uge,ult,ule,ne,t,sf,seq,gt,ge,lt,le,gl,gle,ngle,ngl,nle,nlt,nge,ngt,sne,st
        fs\p    %d0
        add.b   %d0, %d0
        roxl.l  #1, %d3
        .endr
        move.l  %d3, %d0
        move.l  (%a5)+, %d1
        bsr     check
        dbf     %d7, 5b
        fmove.l #0, %fpsr

| ---------------------------------------------------------------------------
| 6. FBcc, FDBcc, FTRAPcc, FNOP
| ---------------------------------------------------------------------------
        fmove.l #0x04000000, %fpsr          | Z
        moveq   #0, %d0
        fbeq    6f
        moveq   #1, %d0
6:      moveq   #0, %d1
        bsr     check
        moveq   #0, %d0
        fbne.l  6f
        moveq   #1, %d0
6:      moveq   #1, %d1
        bsr     check
        moveq   #3, %d4
        moveq   #0, %d0
6:      addq.l  #1, %d0
        fdbf    %d4, 6b                     | never true: four times round
        moveq   #4, %d1
        bsr     check
        clr.l   ntrap
        ftrapf                              | false: nothing
        ftrapt                              | true: vector 7
        ftrapeq.w #0x1234
        move.l  ntrap, %d0
        moveq   #2, %d1
        bsr     check
        fnop
        fmove.l #0, %fpsr

| ---------------------------------------------------------------------------
| 7. BSUN, trapped (FPU 6.1.1, 7.5.4.4): the handler disables the trap and
|    the branch runs again
| ---------------------------------------------------------------------------
        clr.l   nbsun
        fmove.l #0x00008000, %fpcr
        fmove.l #0x01000000, %fpsr          | NAN
        moveq   #0, %d0
        fbgt    7f
        moveq   #1, %d0
7:      moveq   #1, %d1
        bsr     check                       | not taken (unordered)
        move.l  nbsun, %d0
        moveq   #1, %d1
        bsr     check
        fmove.l %fpsr, %d0
        andi.l  #0x00008080, %d0            | EXC BSUN, AEXC IOP
        move.l  #0x00008080, %d1
        bsr     check
        fmove.l #0, %fpcr
        fmove.l #0, %fpsr

| ---------------------------------------------------------------------------
| 8. F-line through the response CIR (FPU 7.5.4.5)
| ---------------------------------------------------------------------------
        clr.l   nfline
        move.l  #4, skip
        .word   0xF200, 0x0045              | FMOVE opmode $45: undefined
        .word   0xF200, 0x2000              | opclass 001: undefined
        move.l  nfline, %d0
        moveq   #2, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 9. A pending exception, made by hand in an idle frame (FPU 6.4.2.2): not
|    reported by FMOVEM or FMOVE of a control register, reported by FNOP
| ---------------------------------------------------------------------------
        fsave   -(%sp)
        move.l  (%sp), %d0
        clr.w   %d0
        swap    %d0
        move.l  #0x1F18, %d1
        bsr     check
        bclr    #3, 0x18(%sp)               | EXC PEND (bit 27), active low
        fmove.l #0x00000400, %fpcr          | DZ enabled
        fmove.l #0x00000400, %fpsr          | DZ in EXC
        frestore (%sp)+
        clr.l   narith
        fmovem.x %fp0, -(%sp)
        fmove.l %fpcr, %d0
        fmovem.x (%sp)+, %fp0
        move.l  narith, %d0
        moveq   #0, %d1
        bsr     check
        fnop                                | vector 50, then runs again
        move.l  narith, %d0
        moveq   #1, %d1
        bsr     check
        move.l  lastvec, %d0
        moveq   #50, %d1
        bsr     check
        fmove.l #0, %fpcr
        fmove.l #0, %fpsr

| ---------------------------------------------------------------------------
| 10. FRESTORE of a frame of another version: format error (FPU 7.5.4.7)
| ---------------------------------------------------------------------------
        clr.l   nformat
        move.l  #2, skip
        lea     badframe, %a0
        frestore (%a0)
        move.l  nformat, %d0
        moveq   #1, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 11. FSAVE and FRESTORE of an idle frame keep the programmer's model
| ---------------------------------------------------------------------------
        fmove.x xc, %fp5
        fsave   -(%sp)
        fmove.x xd, %fp6
        frestore (%sp)+
        fmove.x %fp5, buf
        lea     buf, %a1
        lea     xc, %a2
        moveq   #3, %d6
        bsr     cmplongs
        lea     nullframe, %a0              | FRESTORE of a null frame resets
        frestore (%a0)
        fsave   -(%sp)                      | null again: nothing executed since
        move.l  (%sp)+, %d0
        clr.w   %d0
        swap    %d0
        lsr.w   #8, %d0
        moveq   #0, %d1
        bsr     check
        fmove.x %fp5, buf
        move.l  buf, %d0
        move.l  #0x7FFF0000, %d1            | a nonsignalling NaN (FPU 2.1)
        bsr     check

| ---------------------------------------------------------------------------
| Done
| ---------------------------------------------------------------------------
        move.l  #0x444F4E45, RES
        .globl  _done
_done:  bra     _done

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

| cmplongs: d6 long words at a1 against a2.
cmplongs:
        subq.l  #1, %d6
1:      move.l  (%a1)+, %d0
        move.l  (%a2)+, %d1
        bsr     check
        dbf     %d6, 1b
        rts

| ---------------------------------------------------------------------------
| Exception handlers
| ---------------------------------------------------------------------------
unexpected:
        move.w  6(%sp), %d0
        andi.l  #0x0FFF, %d0
        lsr.l   #2, %d0
        move.l  %d0, RES+0x10
        move.l  #0x444F4E45, RES
        bra     _done

h_trapcc:
        addq.l  #1, ntrap
        rte

| F-line and format error: a pre-instruction frame (format 0), the PC on it
| pointing at the instruction; step over it.
h_fline:
        addq.l  #1, nfline
        move.l  %d0, -(%sp)
        move.l  skip, %d0
        add.l   %d0, 6(%sp)
        move.l  (%sp)+, %d0
        rte

h_format:
        addq.l  #1, nformat
        move.l  %d0, -(%sp)
        move.l  skip, %d0
        add.l   %d0, 6(%sp)
        move.l  (%sp)+, %d0
        rte

h_bsun:
        addq.l  #1, nbsun
        fmove.l #0, %fpcr                   | so the rerun does not trap again
        rte

| The MC68881 clears the pending exception on the acknowledge (FPU 6.1.13),
| so an RTE alone is a complete handler.
h_arith:
        addq.l  #1, narith
        move.l  %d0, -(%sp)
        move.w  10(%sp), %d0
        andi.l  #0x0FFF, %d0
        lsr.l   #2, %d0
        move.l  %d0, lastvec
        move.l  (%sp)+, %d0
        rte

| ---------------------------------------------------------------------------
| Data
| ---------------------------------------------------------------------------
        .data
        .balign 4
xa:     .long   0x40000000, 0xC0000000, 0x00000000      | 3.0
xb:     .long   0xBFFE0000, 0x80000000, 0x00000001      | -0.5 and a little
xc:     .long   0x7FFF0000, 0x00000000, 0x00000000      | +infinity
xd:     .long   0x00000000, 0x00000000, 0x00000000      | +0
| condition code, then the FScc mask for predicates 0 (bit 31) to 31
cctab:  .long   0x00000000, 0x33333333                  | +
        .long   0x08000000, 0x0F0F0F0F                  | -
        .long   0x04000000, 0x55555555                  | +0
        .long   0x0C000000, 0x55555555                  | -0
        .long   0x01000000, 0x00FF00FF                  | NaN
        .long   0x02000000, 0x33333333                  | +inf
        .long   0x0A000000, 0x0F0F0F0F                  | -inf
badframe:  .long 0x3F180000
nullframe: .long 0x00000000
skip:   .long   0
ntrap:  .long   0
nfline: .long   0
nformat: .long  0
nbsun:  .long   0
narith: .long   0
lastvec: .long  0
buf:    .space  64
