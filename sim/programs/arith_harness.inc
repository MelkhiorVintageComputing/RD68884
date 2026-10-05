| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68884 -- the harness of the generated arithmetic system tests
| (sim/programs/fpu_m5.S, fpu_m6.S): vectors, results block, pre, post,
| check, the exception handlers. The including file provides `vectors`.

        .equ    STACK_TOP, 0x0000F000
        .equ    RES,       0x0000C000
        .equ    OBUF,      0x0000C100       | the output buffer, 12 bytes
        .equ    FBUF,      0x0000C110       | FP1's dump, 12 bytes
        .equ    PATTERN,   0xA5C3E187

        .section .vectors, "a"
        .long   STACK_TOP, _start
        .rept   46
        .long   unexpected                  | 2-47
        .endr
        .rept   7
        .long   h_arith                     | 48-54: the FPU's exceptions
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
        lea     OBUF, %a3
        jsr     vectors
        move.l  #0x444F4E45, RES
        .globl  _done
_done:  bra     _done

| pre: a2 is the record.
pre:    fmove.l #0, %fpcr
        fmove.x (%a2), %fp1
        fmove.x 12(%a2), %fp2
        move.l  #PATTERN, (%a3)
        move.l  #PATTERN, 4(%a3)
        move.l  #PATTERN, 8(%a3)
        fmove.l 24(%a2), %fpcr
        fmove.l #0, %fpsr
        moveq   #0, %d7
        rts

| post: the FPSR as the instruction left it; FNOP takes a pending exception
| (FPU 6.1, the handler records its vector); then the results.
post:   fmove.l %fpsr, %d2
        fnop
        fmove.l #0, %fpcr
        fmove.x %fp1, FBUF
        lea     FBUF, %a1
        lea     28(%a2), %a4
        moveq   #2, %d6
1:      move.l  (%a1)+, %d0
        move.l  (%a4)+, %d1
        bsr     check
        dbf     %d6, 1b
        move.l  %a3, %a1
        moveq   #2, %d6
2:      move.l  (%a1)+, %d0
        move.l  (%a4)+, %d1
        bsr     check
        dbf     %d6, 2b
        move.l  %d2, %d0
        move.l  (%a4)+, %d1
        bsr     check
        move.l  %d7, %d0
        move.l  (%a4)+, %d1
        bsr     check
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

| The arithmetic exceptions, pre- or mid-instruction (FPU 6.1, UM 7.4): note
| the vector and return; a pre-instruction exception's RTE runs the
| instruction again, now with nothing pending.
| An MC68882 keeps a floating-point exception reported until its handler
| clears it in the state frame (FPU 5.2.2, figure 5-6): FSAVE, EXC_PEND
| (bit 27 of the BIU flags, the frame's last long word) set to inactive,
| FRESTORE. On an MC68881 the acknowledge has already cleared it, and the
| BSET changes nothing.
h_arith:
        move.w  6(%sp), %d7
        andi.l  #0x0FFF, %d7
        lsr.l   #2, %d7
        move.l  %d0, -(%sp)
        fsave   -(%sp)
        moveq   #0, %d0
        move.b  1(%sp), %d0
        bset    #3, (%sp,%d0.w)
        frestore (%sp)+
        move.l  (%sp)+, %d0
        rte

unexpected:
        move.w  6(%sp), %d0
        andi.l  #0x0FFF, %d0
        lsr.l   #2, %d0
        move.l  %d0, RES+0x10
        move.l  #0x444F4E45, RES
        bra     _done
