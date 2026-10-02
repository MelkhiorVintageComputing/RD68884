| SPDX-License-Identifier: CERN-OHL-S-2.0
| Copyright 2026 Romain Dolbeau
| Source location: https://github.com/MelkhiorVintageComputing/RD68884

| RD68884 -- the whole runtime of the C system tests (sim/tb/sys_tb.sv).
| Copied from RD68021's sim/programs/crt0.S.
|
| There is no operating system, no loader and no C library. This sets the stack,
| clears the uninitialised data, calls main and then stops on a branch to
| itself, which is how both the testbench and the trace generator know the
| program is over.
|
| Supervisor mode throughout, with no exception handlers: nothing here may
| take an exception.

        .equ    STACK_TOP, 0x0000F000

        .section .vectors, "a"
        .long   STACK_TOP               | the initial interrupt stack pointer
        .long   _start                  | the initial program counter

        .text
        .globl  _start
_start:
        | The condition codes after a reset are not defined by anything. UM 6.1.1
        | sets the supervisor bit, clears the trace bits and puts the interrupt
        | mask at 7, and says nothing at all about the CCR -- so a program on a
        | bare machine may not depend on it, and this one establishes it before
        | it does anything else. It is also what lets an instruction-by-
        | instruction comparison start from a state both sides agree on.
        andi    #0x00, %ccr

        move.l  #STACK_TOP, %sp

        | Turn the instruction cache on (UM 4.3.1, CACR's E bit). Every program
        | runs cached, which is what a real system does; `make cache` runs the
        | same programs on a core built with no cache at all and holds the two
        | to the same data cycles.
        moveq   #1, %d0
        movec   %d0, %cacr

        | Clear .bss. The linker leaves it empty in the image and C expects
        | zeros there.
        lea     __bss_start, %a0
        lea     __bss_end, %a1
        bra     2f
1:      move.b  #0, (%a0)+
2:      cmp.l   %a1, %a0
        bne     1b

        jsr     main

        | Done. A branch to itself is the halt: the core keeps fetching the same
        | word, the trace stops growing, and nothing takes an exception.
        .globl  _done
_done:
        bra     _done
