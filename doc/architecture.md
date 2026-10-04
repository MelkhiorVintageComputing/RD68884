<!-- SPDX-License-Identifier: CERN-OHL-S-2.0 -->
# RD68884 architecture

RD68884 is an MC68881-compatible FPU for an MC68020: a real chip, or RD68021. It also works with an MC68030, whose coprocessor interface is the same. It has to be:
- compatible on the bus and in the coprocessor dialogs;
- written in portable SystemVerilog;
- above all **small**, so that it fits an Artix-7 35T.

Cycles per instruction are secondary.

**Decisions taken at the outset:**
- **Personality:** MC68881.
- **Transcendental accuracy:** the manual's, at most 1 ulp of double. Basic arithmetic is correctly rounded.
- **Sources:** papers and the algorithms of Motorola's FPSP may be consulted. mh882, Musashi, TME, QEMU and SoftFloat are oracles only.
- **Licence:** CERN-OHL-S-2.0.

This document gives the design and its reasons. The details are elsewhere:

| Subject | Document |
|---|---|
| The microcode machine | `doc/microcode.md` |
| The bus | `doc/bus-timing.md` |
| What it costs | `doc/size-and-speed.md` |
| How fast it is | `doc/timing-divergences.md` |

## Design

The design follows the original's split: a bus interface unit (BIU) and a microcoded arithmetic unit. The arithmetic unit is one narrow, general datapath. Everything that is not add, multiply, shift or normalise lives in microcode. The microcode, the constant tables and the register file sit in block RAM, so LUTs go only to the datapath and its control.

```
 CLK pin ─(board PLL)─► clk  (50 MHz; any frequency works, a slower one only adds wait states)
 CS/AS/DS/RW/A4-0/SIZE ─► BIU ─ events ─► SEQUENCER ─ microword ─► datapath (A, B, C, adder, shifter, MUL)
 D31-0 _i/_o/_oe[3:0] ◄──┤  │ response/flags/staging ◄────────────┤  ▲   ▲
 DSACK1-0 _o/_oe       ◄──┘  └─ hardware auto-answers when busy      │   │
                                                         regfile BRAM  constant ROM
```

| Module | Contents |
|---|---|
| `rtl/rd68884_top.sv` | The pins (`doc/pinout.md`) and the two units |
| `rtl/rd68884_biu.sv` | The BIU |
| `rtl/rd68884_seq.sv` | The sequencer and the datapath |
| `rtl/rd68884_regfile.sv` | The register file |
| `rtl/gen/` | Generated from `tools/ucode/`: the microcode ROM, the constant ROM and the field package |

### Clocking

- **One synchronous core domain, `clk`.** Bus strobes pass 2-flop synchronisers, as in RD68021's `rd68021_sync.sv`.
- **DSACK and the data enables are gated combinationally by the raw START term**, CS·AS·(R/W + DS) (section 12, note 8). They release the bus the instant START falls, whatever the core clock is doing (specifications 16, 21, 22).
- **A stale guard** is set asynchronously when START falls and cleared by the core once it has retired the cycle.
  - It stops a new cycle from being acknowledged by the previous cycle's request.
  - It makes even a strobe-negated gap shorter than one core clock visible (specification 13).
  - So the core clock has no minimum relative to the bus; a slow one only adds wait states. The testbench runs core = bus. This replaced the planned "core ≥ 2.5× bus" rule (`doc/bus-timing.md`).
- **The 50 MHz target comes from the datapath**, not from the bus.
- **A second front end, selected at build time:** `BUS_SYNC = 1` is for a board where the core runs on the main processor's own CLK.
  - It drops the synchronisers and the stale guard, and answers on the MC68020's clock edges: no wait states, or one with `BUS_SYNC_WAIT = 1`.
  - Everything behind the front end, the CIR registers, the protocol checks and the events, is the same code.
  - `doc/bus-timing.md`, "The same-clock BIU".
- The optional synchronous-read mode is not reproduced. Every cycle is answered asynchronously, which the protocol allows.

### BIU (`rd68884_biu`)

- **Decode and port sizing:**
  - CIR decode on A4–A0.
  - 8-, 16- and 32-bit ports per Table 9-2/9-3 and Figure 10-2.
  - **Four per-byte data `_oe` bits**, so only the active lane is driven: on narrow ports the board ties lanes together.
  - Events are raised only on the *last* beat of a register. Immediate .B/.W operands are counted by length.
- **Operand CIR:** one 32-bit staging register in each direction, handed over with valid/acknowledge. An access the sequencer is not ready for is held off by withholding DSACK (FPU 10.5); the sequencer keeps the counts.
- **Response register.** The microcode writes it. A response read that arrives after the sequencer has taken a command but before it has answered is held off, for at most `RESP_HOLD` clocks (`doc/bus-timing.md`, "The response hold-off").
- **Lifetime of primitives:**
  - One-shot primitives (evaluate-EA, transfer-single, transfer-multiple) revert to `$8900` once they have been read.
  - Take-exception primitives persist until a write to the control CIR.
- **Hardware auto-answers while the sequencer is busy computing:**
  - a command or condition write is latched and answered `$8900`;
  - a save read with no frame prepared is answered come-again `$01xx` and sets `save_req`;
  - a protocol violation is answered `$1D0D`;
  - a control-CIR write (abort) or a restore write forces a sequencer trap.
- **The BIU-flags word of Figure 6-6/Table 6-4 is the BIU's real state register:** PV, pending-access code, EXC_PEND active-low, operand-done, byte-valid. Protocol-violation checks run on it, and the idle frame simply dumps it.
- **Unimplemented and reserved CIRs:** reads of reserved, write-only or unimplemented CIRs return all ones; a write to register-select is a protocol violation.
- **DSACK release:** three-stated at once, or with `DSACK_NEGATE = 1` driven negated until the next core-clock edge first, as the MC68881 does (FPU 9.8).
- **The races** between the main processor and the microcode that the BIU settles in hardware are listed in `doc/microcode.md`.

### Sequencer (`rd68884_seq`)

- **One microinstruction per clock, no delay slot.** The microcode store is a generated `case` ROM in block RAM, read at the next micro-address, so the word arrives with its address. This was decided at M5 by Fmax.
- **The store:** 3128 words of 122 bits, a 12-bit micro-address. The microword register is the one register exempt from reset.
- **Next-address operations:** NEXT, JUMP, BR on one of 66 conditions (with a polarity bit), CALL and RET (a 4-deep stack), LOOP on a counter, WAIT on a condition, and DISP.
- **Dispatch has no dispatch ROM in logic.** DISP is an indirect jump `TGT | index` into jump tables held in the microcode store (opclass, the command word's rx field, opmode). The block-RAM words are free; LUTs are not.
- **Traps** override the micro-PC: RESET, ABORT (control-CIR write), RESTORE.
- **One field table** (`tools/ucode/fields.py`, 29 fields) generates the RTL package and the assembler, and drives the instruction-set simulator. The ISS (`tools/iss/core.py`) is the executable definition of every field. The RTL is its transcription, and lockstep holds the two to the same micro-address and outputs on every clock (`make sys`).

### Datapath (inside `rd68884_seq`)

**The value format:**
- a sign;
- an 18-bit two's-complement unbiased exponent;
- a 72-bit mantissa: M[71] is the J bit, M[70:8] the 63 extended fraction bits, M[7:0] guard bits;
- a sticky bit and a 2-bit tag (finite, zero, infinity, NaN).

FPn keep raw extended semantics, so FMOVE.X and FMOVEM.X round-trip bit-exactly, unnormals and denormals included. Every arithmetic entry point normalises.

**Registers:**
- `A` and `B` (91 bits), `C` (88 bits), and a few narrow ones: `RX`, the sticky bits, the shift amount `SA`, precision and rounding mode.
- The register file is 128 × 91 in block RAM, read synchronously. It holds FP0–7, ETEMP and the microcode's temporaries.

**Units:**

| Unit | As built |
|---|---|
| Adder | **One shared 79-bit adder** for every mantissa sum: add, subtract, negate, round, the divide and square-root steps, ×10 and digit insertion for packed decimal, and the magnitude compare |
| Shifter | **One right shifter**, which also normalises in a single clock by bit reversal |
| Multiplier | 72 × 16 per step (`MULSTEP`), the dropped low product bits kept in `B` |
| Constant ROM | 2048 entries, each truncated to 72 bits with a sticky bit: the FMOVECR constants, powers of ten, the transcendentals' tables and coefficients, and 2/π |

**Size first.** The M7 size pass measured each group of operations by synthesising variants without it. Everything cheap but wide moved into microcode, because each 72-bit source into `A`'s or `B`'s next state costs tens of LUTs whatever it does (`doc/size-and-speed.md`).

### Microcode responsibilities

**Coprocessor dialogs, every opclass, per section 7:**
- **Start and release:**
  - An instruction starts at the first response read after the command write.
  - A register-to-register instruction answers `$0900`, or `$4900` with the PC bit; the PC is requested only when FPCR[15:8] is non-zero.
  - The microcode sets `$0802` when the instruction completes.
- **Exception reporting:**
  - A pending exception is reported as a pre-instruction exception `$1C3x` on the next opclass 000/010/011 instruction or the next conditional, FNOP included.
  - It is never reported on FMOVEM, FMOVE of control registers, FSAVE or FRESTORE.
  - A mid-instruction exception `$1D3x` is used only for FMOVE out.
- **Conditionals:** BSUN is answered `$5C30`; predicates above `$1F` and illegal command words take the F-line exception.
- **FMOVEM:** register-select returns the mask in bits 15:8; control registers transfer in the order FPCR, FPSR, FPIAR.
- **Context save:**
  - The BIU answers **come-again** while the microcode is purely computing.
  - The microcode produces the **idle frame `$1F18`**, using the MC68881 layout, when nothing is in flight or only a command is latched.
  - It produces an **opaque busy frame `$1FB4`** (45 long words) when operands are part-way through a transfer, for example after a page fault in the middle of FMOVEM. The frame holds the resume address and sequencer state, the command word, ETEMP, the operand being transferred and the BIU flags (`doc/microcode.md`, "The busy frame").
- **Restore:**
  - FRESTORE accepts null, idle and busy frames, and rejects anything else with `$02xx`.
  - A null restore resets the FPU: FPn = NaN, FPCR = FPSR = 0.

**Arithmetic** (`doc/microcode.md`, "The arithmetic"):
- **Basic operations:** FADD, FSUB, FMUL, FDIV, FSQRT, FSGLMUL, FSGLDIV, FINT, FINTRZ, FGETEXP, FGETMAN, FSCALE, FMOD and FREM (with the quotient byte), FCMP, FTST, FABS, FNEG, FMOVE, FMOVECR. All correctly rounded in every mode and precision, checked against TestFloat.
- **Transcendentals**, FSIN through FATANH: Tang's table methods, at 72-bit internal precision.
  - Trigonometric arguments are reduced exactly: Cody–Waite below 2²⁰, Payne–Hanek above.
  - The error is at most 0.54 ulp of extended against mpmath, far inside FPU 4.3.2's one ulp of double.
- **Packed decimal:** both directions scale by a power of ten from the constant ROM: one multiplication or division (exact for |s| ≤ 27), or two products beyond. Output divides the 17 digits out one at a time. Both are within FPU 4.3.3's bound, and exact where the result can be.

### Size and speed

Measured on xc7a35t, -1, implemented out of context (`doc/size-and-speed.md`). The budget was the plan's, before any RTL:

| | Budget | As built (today; M9 in `doc/size-and-speed.md`) |
|---|---|---|
| LUTs | 3.5–4.5K | 4377 (21% of the 35T) |
| Flip-flops | about 1.3K | 921 |
| DSP48 | 3–4 | 6 |
| Block RAM | 10–15 RAMB36 | 21 tiles (microcode store, constant ROM, register file) |
| Fmax | 45–60 MHz | 54.9 MHz; limited by microword → `A`'s exponent |

On the IIsiA7 Mini's xc7a50t, with real I/O, the whole board design meets 50 MHz (`doc/boards.md`).

## Repository layout

| Path | Contents |
|---|---|
| `CLAUDE.md` | The goals and the hard rules: Inputs immutable; RD68021 read-only; oracle-only list; no initialisation outside reset; `_i/_o/_oe` pins; six-tool portability |
| `doc/` | This document, and those listed in `CLAUDE.md` |
| `rtl/` | The design (above); `rtl/gen/` generated by `make ucode` |
| `tools/ucode/` | The field table, the assembler (`asm.py`), the constant ROM (`crom.py`), the microcode by area |
| `tools/iss/` | The instruction-set simulator, its BIU model, the lockstep checker, its tests |
| `tools/model/` | The golden models: exact arithmetic, packed decimal and transcendentals, the coprocessor interface, an MC68020 driver (`doc/model.md`) |
| `tools/` | `reset_audit.py` and `src_lint.py` (adapted from RD68021), `cycles.py` (`make cycles`), `board_pins.py`, `fparith_compare.py` |
| `sim/tb/` | `biu_tb.sv` (the BIU against an MC68020 bus model, all port sizes and clock ratios, every build); `sys_tb.sv` (RD68021 + RD68884) |
| `sim/programs/` | The system test programs: `fpu_m4.S`…`fpu_m8.S`, RD68021's `fpu.S` and `fparith.c` (adapted) |
| `sim/tme/` | RD68021's core and RD68884 as one Verilator model for TME (`doc/system.md`) |
| `boards/` | Board tops and their builds: the IIsiA7 Mini (`doc/boards.md`) |
| `scripts/` | Vivado, Quartus and Questa scripts and constraints |
| `third_party/` | mpmath, Berkeley SoftFloat and TestFloat (submodules; oracles) |
| `Inputs/` | The Motorola manuals, the SunOS 4.1.1 machine for TME, the IIsiA7 Mini's sources (read-only) |

**Reused from RD68021**, copied and adapted rather than linked:
- the coding rules (`doc/coding-standard.md`);
- the assembler's techniques;
- `reset_audit.py` and `src_lint.py`;
- the vendor script structure;
- `fpu.S`, `fparith.c` and its start-up code;
- the TME build and drive scripts.

The system targets run RD68021 from pinned revisions exported with `git archive`, never its working tree.

## History

| Milestone | Result |
|---|---|
| M1 | Repository, coding standard, lint, audit, vendor scripts, the final port list |
| M2 | The golden models, checked against TestFloat |
| M3 | The BIU, for 8-, 16- and 32-bit ports, against an MC68020 bus model |
| M4 | The sequencer, the assembler, the ISS and lockstep; every coprocessor dialog on RD68021 |
| M5 | The core arithmetic; the branch-timing decision |
| M6 | FMOD/FREM, FSCALE, FMOVECR, packed decimal |
| M7 | The transcendentals; the size pass (−791 LUTs) |
| M8 | Busy frames; FSAVE in mid-transfer through bus errors, interrupts and tracing. A bug found in RD68021, since fixed there |
| M9 | SunOS 4.1.1 in TME on RD68021 + RD68884, identical to TME's own MC68881; the final implementation |

**After M9:**
- the clock counts against FPU section 8 (`make cycles`);
- the same-clock BIU;
- the response hold-off;
- RD68021's master and its RTE fix;
- **in hardware:** the IIsiA7 Mini, on which a Macintosh IIsi boots with RD68884 as its FPU and sees an MC68881 (`doc/boards.md`).
