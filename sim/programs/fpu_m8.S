| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68884 -- the M8 system test: FSAVE in the middle of a coprocessor dialog,
| on a real MC68020 (RD68021).
|
| sim/tb/sys_tb.sv can interrupt the processor (RES+$80: after N clocks, level
| 2, autovectored) or give one data access a bus error (RES+$84: its address).
| Each case runs an instruction once undisturbed, then again with a bus error
| on one of its operands or an interrupt while it polls. The handler saves
| the FPU (the interrupt handler also saves the user's FP registers, uses the
| FPU, and puts everything back), and returns: RTE runs the faulted access again (UM 7.5.2.8), or reads the
| response again after an interrupt (UM 7.4.1). The two runs must agree, and
| the frame the handler saved must be the one expected: busy ($1FB4) in the
| middle of a transfer (FPU 6.4.2.3).
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
        .rept   6
        .long   unexpected                  | 3-8
        .endr
        .long   h_trace                     | 9  trace
        .rept   16
        .long   unexpected                  | 10-25
        .endr
        .long   h_irq                       | 26 level 2 autovector
        .rept   229
        .long   unexpected                  | 27-255
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
        move.w  #0x2000, %sr                | supervisor, interrupts on
        fmove.l #3, %fp3
        fmove.l #-77, %fp4

| ---------------------------------------------------------------------------
| 1. FMOVEM in, a bus error on the 5th long word (the second register)
| ---------------------------------------------------------------------------
        lea     xdata, %a0
        fmovem.x (%a0), %fp0-%fp2
        bsr     dump3                       | BUF: what it loaded
        fmove.l #0, %fp0
        fmove.l #0, %fp1
        fmove.l #0, %fp2
        move.l  #xdata + 16, FAULT
        clr.l   frames
        fmovem.x (%a0), %fp0-%fp2
        bsr     dump3b                      | BUF2
        bsr     cmp_bufs
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check                       | one fault taken
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #0x1FB40000, %d1
        bsr     check                       | a busy frame

| ---------------------------------------------------------------------------
| 2. FMOVEM out, a bus error on a write
| ---------------------------------------------------------------------------
        lea     BUF, %a1
        fmovem.x %fp0-%fp2, (%a1)
        lea     BUF2, %a1
        move.l  #BUF2 + 20, FAULT
        clr.l   frames
        fmovem.x %fp0-%fp2, (%a1)
        bsr     cmp_bufs9
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #0x1FB40000, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 3. FMOVE.X out, a bus error on the second long word (on the third, the
| FPU would already have handed over everything: an idle frame)
| ---------------------------------------------------------------------------
        fmove.x %fp1, BUF
        move.l  #BUF2 + 4, FAULT
        clr.l   frames
        fmove.x %fp1, BUF2
        bsr     cmp_bufs3
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #0x1FB40000, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 4. FMOVE.D in, a bus error on the second long word
| ---------------------------------------------------------------------------
        fmove.d ddata, %fp5
        fmove.x %fp5, BUF
        fmove.l #0, %fp5
        move.l  #ddata + 4, FAULT
        clr.l   frames
        fadd.d  ddata, %fp5                 | 0 + the same value
        fmove.x %fp5, BUF2
        bsr     cmp_bufs3
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check

| ---------------------------------------------------------------------------
| 5. FMOVEM of the control registers out, a bus error on the second
| ---------------------------------------------------------------------------
        fmove.l #0x0030, %fpcr
        fmovem.l %fpcr/%fpsr/%fpiar, BUF
        move.l  #BUF2 + 4, FAULT
        clr.l   frames
        fmovem.l %fpcr/%fpsr/%fpiar, BUF2
        move.l  BUF, %d0                    | FPCR
        move.l  BUF2, %d1
        bsr     check
        move.l  BUF + 4, %d0                | FPSR
        move.l  BUF2 + 4, %d1
        bsr     check
        fmove.l #0, %fpcr

| ---------------------------------------------------------------------------
| 6. An interrupt while FMOVE.P out computes (polling, FPU 7.5.4.3)
| ---------------------------------------------------------------------------
        fmove.x xdata + 12, %fp1
        fmove.p %fp1, BUF{#17}
        clr.l   frames
        move.l  #100, IRQ                   | into the computation, whatever the FPU's
        fmove.p %fp1, BUF2{#17}             | clock (with BUS_SYNC=1, the CPU's own)
        bsr     cmp_bufs3
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check                       | the interrupt was taken
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #0x1FB40000, %d1
        bsr     check                       | busy: $B20C posted, not yet read

| ---------------------------------------------------------------------------
| 7. An interrupt while FMOD computes, a long one (the next FPU instruction
| waits; FSAVE answers come-again until it is done, then an idle frame)
| ---------------------------------------------------------------------------
        fmove.x huge, %fp6
        fmod.x  three, %fp6
        fmove.x %fp6, BUF
        fmove.x huge, %fp6
        clr.l   frames
        move.l  #10, IRQ
        fmod.x  three, %fp6
        fmove.x %fp6, BUF2
        bsr     cmp_bufs3
        move.l  frames, %d0
        move.l  #1, %d1
        bsr     check
        move.l  frame0, %d0
        clr.w   %d0
        move.l  #0x1F180000, %d1
        bsr     check                       | idle: nothing in transfer

| ---------------------------------------------------------------------------
| 8. Tracing through FPU instructions, polling ones among them (UM 7.4.1:
| a trace pending while polling); the results as without
| ---------------------------------------------------------------------------
        fmove.x xdata, %fp1
        fsqrt.x %fp1
        fmove.p %fp1, BUF{#9}
        fmove.x xdata, %fp1
        clr.l   traces
        ori.w   #0x8000, %sr                | T1
        fsqrt.x %fp1
        fmove.p %fp1, BUF2{#9}
        andi.w  #0x7FFF, %sr
        bsr     cmp_bufs3
        move.l  traces, %d0
        sne     %d0
        andi.l  #0xFF, %d0
        move.l  #0xFF, %d1
        bsr     check                       | traced

| ---------------------------------------------------------------------------
| Done
| ---------------------------------------------------------------------------
        move.l  #0x444F4E45, RES
        .globl  _done
_done:  bra     _done

| The handlers: a context switch around the FPU. The first frame's format
| word is kept in frame0, the count in frames.
switch:
        fsave   -(%sp)
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
        rts
| The bus error handler is the same context switch: it uses the FPU between
| FSAVE and FRESTORE, and RTE then resumes the interrupted transfer (UM
| 7.5.2.8). RD68021 keeps the primitive across it since 1b57478, the fix for
| what this test found at M8 (doc/divergences.md).
h_berr: bsr     switch
        rte
h_irq:  bsr     switch
        rte
h_trace:
        addq.l  #1, traces
        rte

dump3:  lea     BUF, %a1
        bra.s   2f
dump3b: lea     BUF2, %a1
2:      fmove.x %fp0, (%a1)
        fmove.x %fp1, 12(%a1)
        fmove.x %fp2, 24(%a1)
        rts

cmp_bufs:
cmp_bufs9:
        moveq   #8, %d6
        bra.s   1f
cmp_bufs3:
        moveq   #2, %d6
1:      lea     BUF, %a2
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
xdata:  .long   0x40000000, 0xC90FDAA2, 0x2168C235   | pi
        .long   0xBFFE0000, 0xB17217F7, 0xD1CF79AC   | -ln 2
        .long   0x7FFE0000, 0x80000000, 0x00000001   | large
ddata:  .long   0x400921FB, 0x54442D18               | pi, double
huge:   .long   0x4FFF0000, 0x9ABCDEF0, 0x12345678   | 2^4096-ish
three:  .long   0x40000000, 0xC0000000, 0x00000000   | 3
frames: .long   0
traces: .long   0
frame0: .long   0
