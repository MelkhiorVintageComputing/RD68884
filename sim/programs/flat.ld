/* SPDX-License-Identifier: CERN-OHL-S-2.0
 * Copyright 2026 Romain Dolbeau
 * Source location: https://github.com/MelkhiorVintageComputing/RD68884
 */

/* RD68884 -- the link map (adapted from RD68021's) for a program that runs on the bare core.
 *
 * The system testbench (sim/tb/sys_tb.sv) puts a 32-bit port
 * at $0000_0000 with sixteen address bits, so everything lives in the first
 * 64 KB: the exception vectors at zero, the program at $1000 where the reset
 * vector points, and the stack at the top.
 *
 * There is no operating system and no loader. The image IS the memory. */

ENTRY(_start)

MEMORY
{
  ram (rwx) : ORIGIN = 0x00000000, LENGTH = 0x00010000
}

SECTIONS
{
  /* The two long words the processor reads before it fetches anything --
     UM 6.1.1, and supervisor PROGRAM space, which is why the core reads them
     with FC = 110. */
  .vectors 0x00000000 : { KEEP(*(.vectors)) } > ram

  .text 0x00001000 :
  {
    *(.text.startup)
    *(.text*)
    *(.rodata*)
  } > ram

  .data : { *(.data*) } > ram
  .bss  : { __bss_start = .; *(.bss*) *(COMMON) __bss_end = .; } > ram

  _end = .;

  /* Nothing from the C runtime: there is no runtime. Anything that turns up
     here is a mistake worth seeing rather than silently dropping. */
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
