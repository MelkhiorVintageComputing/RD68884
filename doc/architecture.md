<!-- SPDX-License-Identifier: CERN-OHL-S-2.0 -->
# RD68884 architecture

RD68884 is an MC68881-compatible FPU for an MC68020: a real chip, or RD68021. It has to be compatible on the bus and in the coprocessor dialogs, written in portable SystemVerilog, and above all **small**, so that it fits an Artix-7 35T. Cycles per instruction are secondary.

**Decisions taken at the outset:**
- **Personality:** MC68881.
- **Transcendental accuracy:** the manual's, at most 1 ulp of double. Basic arithmetic is correctly rounded.
- **Sources:** papers and the algorithms of Motorola's FPSP may be consulted. mh882, Musashi, TME, QEMU and SoftFloat are oracles only.
- **Licence:** CERN-OHL-S-2.0.

## Design


The design follows the original's split: a bus interface unit (BIU) and a microcoded arithmetic processing unit (APU). The APU is one narrow, general datapath. Everything that is not add, multiply, shift or normalise lives in microcode, and the microcode, tables and register file sit in block RAM, so LUTs go only to datapath and control.

```
 CLK pin ─(board PLL)─► clk_core  (≈50 MHz target; any frequency works, slower = wait states)
 CS/AS/DS/RW/A4-0/SIZE ─► BIU ─ events ─► SEQUENCER ─ microword ─► APU datapath
 D31-0 _i/_o/_oe[3:0] ◄──┤  │ response/flags/staging ◄────────────┤  ▲   ▲
 DSACK1-0 _o/_oe       ◄──┘  └─ hardware auto-answers when busy      │   │
                                                         regfile BRAM  const/table ROM
```

### Clocking
- There is **one synchronous core domain, `clk_core`**. Bus strobes go through 2-flop synchronisers, as in RD68021's `rd68021_sync.sv`.
- **DSACK and the data enables are gated combinationally by the raw START term**, CS·AS·(R/W + DS) (section 12, note 8). They release the bus the instant START falls, whatever the core clock is doing (specifications 16, 21, 22).
- **A stale guard** is set asynchronously when START falls and cleared by the core once it has retired the cycle.
  - It stops a new cycle from being acknowledged by the previous cycle's request.
  - It makes even a strobe-negated gap shorter than one core clock visible (specification 13).
  - So the core clock has no minimum relative to the bus; a slow one only adds wait states. The testbench runs core = bus. This replaces the planned "core ≥ 2.5× bus" rule: doc/bus-timing.md.
- **The 50 MHz target comes from the datapath**, not from the bus.
- **A second front end, selected at build time:** `BUS_SYNC = 1` is for a board where the core runs on the main processor's own CLK.
  - It drops the synchronisers and the stale guard, and answers on the MC68020's clock edges: no wait states, or one with `BUS_SYNC_WAIT = 1`.
  - Everything behind the front end, the CIR registers, the protocol checks and the events, is the same code.
  - `doc/bus-timing.md`, "The same-clock BIU".
- The optional synchronous-read mode is not reproduced. Every cycle is answered asynchronously, which the protocol allows.

### BIU (`rd68884_biu`, about 600 LUTs)
- **Decode and port sizing:**
  - CIR decode on A4–A0.
  - 8-, 16- and 32-bit ports per Table 9-2/9-3 and Figure 10-2.
  - **Four per-byte data `_oe` bits**, so only the active lane is driven: on narrow ports the board ties lanes together.
  - Events are raised only on the *last* beat of a register.
  - Immediate .B/.W operands are counted by length.
- **Operand CIR:** one 32-bit staging register in each direction, handed over with valid/acknowledge. An access the sequencer is not ready for is held off by withholding DSACK (FPU 10.5); the sequencer keeps the counts.
- **Implemented in M3:** 242 LUTs and 329 flip-flops (doc/size-and-speed.md). The sequencer interface is listed in the header of `rtl/rd68884_biu.sv`.
- **Response register.** The microcode writes it. A response read that arrives after the sequencer has taken a command but before it has answered is held off, by withholding DSACK, for at most `RESP_HOLD` clocks (`doc/bus-timing.md`, "The response hold-off").
- **Lifetime of primitives:**
  - One-shot primitives (evaluate-EA, transfer-single, transfer-multiple) revert to `$8900` once they have been read.
  - Take-exception primitives persist until a write to the control CIR.
- **Hardware auto-answers while the sequencer is busy computing:**
  - a command or condition write is latched and answered `$8900`;
  - a save read is answered come-again `$01xx` and sets `save_req`;
  - a protocol violation is answered `$1D0D`;
  - a control-CIR write (abort) or a restore write forces a sequencer trap.
- **The BIU-flags word of Figure 6-6/Table 6-4 is the BIU's real state register:** PV, pending-access code, EXC_PEND active-low, operand-done, byte-valid. Protocol-violation checks run on it, and the idle frame simply dumps it.
- **Unimplemented and reserved CIRs:** reads of reserved, write-only or unimplemented CIRs return all ones; a write to register-select is a protocol violation.

### Sequencer (`rd68884_seq`, about 300 LUTs)
- **Implemented in M4:** see doc/microcode.md for the field set as built (one cycle per microinstruction) and how it is checked.
- **Microcode store:** about 2–4K words of about 96 bits, in BRAM. It is a generated `case` ROM with a registered output; the MIR is the one register exempt from reset.
- **Next-address operations:** NEXT, JMP, BR on a condition (a 64-way mux, polarity bit), CALL and RET (4-deep stack), LOOP on a 16-bit counter, DISP, and WAIT on a condition.
- **Dispatch has no dispatch ROM in logic.** DISP is an indirect jump `IMM | index` into jump tables held in the microcode BRAM (opmode, format, opclass, predicate). The BRAM words are free; LUTs are not.
- **Forced traps** override the micro-PC: RESET, ABORT (control-CIR write), RESTORE.
- **Three 7-bit operand-pointer registers** let add, multiply, divide and round be reusable micro-subroutines, which keeps the transcendental microcode small.
- **Branch timing:** two cycles per microinstruction, or one cycle with a delay slot. This is chosen at synthesis checkpoint M5 by Fmax.
  - **Decided at M5: one cycle, with no delay slot.** The registered ROM is read at the next micro-address, and that met 56.8 MHz after place and route (doc/size-and-speed.md).

### APU datapath (`rd68884_apu` plus sub-units, about 2.5K LUTs)
- **As built in M5,** it lives inside `rd68884_seq`, and doc/microcode.md describes it. It differs from the plan below in four ways:
  - one shared adder;
  - one right shifter, which also normalises in a single clock by bit reversal;
  - `A`, `B` and `C` with no operand pointers yet;
  - about 3.4K LUTs.
- **Internal format:**
  - 1-bit sign;
  - 18-bit two's-complement unbiased exponent;
  - **72-bit mantissa**: M[71] is the J bit, M[70:8] the 63 extended fraction bits, M[7:0] extra bits, plus a sticky flop;
  - a 2-bit tag: finite, zero, inf, NaN.
  - The rounding positions (last bit, guard bit, sticky) for X, D and S are fixed at M[8], M[19] and M[48].
  - FPn keeps raw extended semantics, so FMOVE.X and FMOVEM.X round-trip bit-exactly, unnormals and denormals included. Every arithmetic entry point normalises.
- **Register file:** about 128 × 94 in BRAM, with synchronous read. It holds FP0–7, about 24 microcode temporaries and ETEMP.
- **Constant and coefficient ROM:** about 1K × 94 plus a sticky bit, as a separate generated ROM. It holds the FMOVECR constants, the powers of ten, multi-word π and the FPSP-style tables.
- **Working registers A, B and C in flip-flops**, reached only through load and store. These narrow the operand muxes and hide BRAM latency.
- **The units:**

| Unit | Implementation |
|---|---|
| Mantissa adder | 72 bits, X/Y operand muxes; operations add, sub, rsub, +1, AND, OR, ANDN, pass |
| Barrel shifter | 72 bits, bidirectional, with sticky collection and a nibble-left mode for BCD; the shift amount always comes from a register (SA or immediate), never from the same cycle |
| Normaliser | 72-bit leading-zero counter; normalising takes 2 cycles |
| Exponent ALU | 18-bit adder/comparator; bias selected by precision |
| Multiplier | iterative 64×16 per step, 4 steps plus accumulation: 3–4 DSP48 slices and about 200 LUTs; maps the same way on every tool |
| DIV/SQRT/REM steps | 1 bit per cycle on the main adder, run in a LOOP; FMOD and FREM use the same step, up to about 16.5K iterations |
| Rounding | reuses the adder with an increment mask selected by precision; mantissa precision and exponent range are separate selectors (FSGLMUL/FSGLDIV); handles the round modes, overflow and underflow defaults, and denormals |
| Pack/unpack | B, W, L, S, D, X steering, plus nibble insert and extract for P |
| Status logic | FPCR, FPSR, FPIAR; EXC and AEXC update equations; trap priority and vector selection; condition codes; evaluator for the 32 predicates, with BSUN |

- **Microword:** about 92 bits, padded to 96. The fields are:
  - sequencing: SEQ, COND, IMM(18);
  - register file: RFOP, RFAM, RFADR;
  - mantissa adder: XS, YS, AOP;
  - shifter: SHS, SHOP, SHA;
  - exponent: EX, EY, EOP;
  - load destinations: DA, DB, DC, DE;
  - SGN and TAG, RND, FPS, BIU, AUX.
- **Single source for the encoding:** one Python field table generates the RTL decode package, the assembler and the instruction-set simulator (ISS).

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
  - The microcode answers **come-again** while the APU is purely computing.
  - It produces the **idle frame `$1F18`**, using the MC68881 layout, when nothing is in flight or only a command is latched.
  - It produces an **opaque busy frame `$1FB4`** (180 bytes) when an MPU transfer dialog is in progress, for example a page fault in the middle of FMOVEM. The frame holds the BIU state, the staging register, counters, Dn values and a resume token.
- **Restore:**
  - FRESTORE accepts `$00xx`, `$1F18` and `$1FB4`, and rejects anything else with `$02xx`.
  - After a restore the response is regenerated from the command word and the flags.
  - A null restore resets the FPU: FPn = NaN, FPCR = FPSR = 0.

**Arithmetic:**
- **Basic operations:** FADD, FSUB, FMUL, FDIV, FSQRT, FSGLMUL, FSGLDIV, FINT, FINTRZ, FGETEXP, FGETMAN, FSCALE, FMOD and FREM (with the quotient byte), FCMP, FTST, FABS, FNEG, FMOVE, FMOVECR.
- **Transcendentals** (FSIN through FATANH): table-driven Tang/FPSP-style algorithms evaluated at 72-bit internal precision. Large trigonometric arguments are reduced with an exact FREM against a multi-word π.
- **Packed decimal:**
  - Input accumulates digits ×10 and then scales with the powers-of-ten ROM.
  - Output follows the FPSP bindec scheme, including the k-factor, and produces digits by repeated fraction×10.

### Size and speed budget (Artix-7, -1 speed grade)

| Resource | Estimate |
|---|---|
| LUTs | about 3.5–4.5K (17–22% of a 35T) |
| Flip-flops | about 1.3K |
| DSP48 | 3–4 |
| RAMB36 | about 10–15 |

- **Critical path:** microword → operand mux → 72-bit carry chain. Expected Fmax is 45–60 MHz, so the core is constrained at 50 MHz.
- The size is checked at each milestone with out-of-context Vivado on xc7a35t. Utilisation is tracked in `doc/size-and-speed.md`.

## Repository layout (mirrors RD68021)

| Path | Contents |
|---|---|
| `CLAUDE.md` | Hard rules: Inputs immutable; oracle-only list; no initialisation outside reset; `_i/_o/_oe` pins; six-tool portability |
| `doc/` | coding-standard.md (adapted from RD68021's), architecture.md, microcode.md, coprocessor-dialogs.md, manual-contradictions.md (e.g. `$1C0B` vs PC=1 for F-line; errata in Table 7-7), size-and-speed.md, divergences.md |
| `rtl/` | `rd68884_pkg`, `_sync`, `_biu`, `_seq`, `_apu`, `_shifter`, `_lzc`, `_mul`, `_round`, `_regfile`, `_top`; generated `rtl/gen/` (ucode ROM, constant ROM, field package) |
| `tools/` | `ucode/` (field table, assembler, microprograms per area); `iss/` (Python ISS); `model/` (exact-rational golden arithmetic model with MC68881 rules); adapted copies of RD68021's `src_lint.py`, `reset_audit.py` and `timing/` (pointed at `MC68881UM_split/ac-electrical-specifications.csv`) |
| `sim/` | `tb/` (BIU/BFM unit tests, datapath unit tests, Verilator lockstep against the ISS); `models/` (68020-side bus functional model with 8/16/32 ports and randomised core-to-bus clock phase) |
| `scripts/` | Vivado (xc7a35t, out-of-context and implementation), Quartus, Yosys; XDC/SDC at 50 MHz core |
| `Makefile` | `ucode`, `lint` (iverilog, Verilator, Yosys), `audit`, `sim`, `iss-test`, `synth`, `impl`, `check`, `rd68021` (system run) |

**Reused from RD68021**, copied and adapted rather than linked:
- the coding rules in `doc/coding-standard.md`;
- the assembler techniques in `tools/ucode/assemble.py`: disjoint casez, ROMs indexed by the address bits in use, `rom_style` block;
- `tools/reset_audit.py`, `tools/src_lint.py`, `tools/timing/`;
- the Vivado and Quartus script structure from `scripts/`.

**System tests use RD68021 in place** and never modify it:
- `sim/tb/rd68021_core_harness.svh` and its `TB_MH882` attachment pattern (CS for CpID 1, A0 tied high, SIZE tied high);
- `sim/programs/fpu.S` (54 checks), `fparith.c` and `tools/mh882_compare.py`;
- later, the TME/SunOS flow.

This is done through an RD68884-side harness that includes or copies those files.

**External oracles, to be fetched with the user's approval:**
- Berkeley SoftFloat 3e and TestFloat (BSD licence; extF80 with rounding precisions 32, 64 and 80), as a git submodule;
- mpmath in a Python venv, for the transcendental and decimal references;
- a small C helper on the host's x87 as a secondary check;
- mh882 and TME's MC68881 as cross-check oracles only.

## Milestones and verification

1. **Skeleton.** Repository, licence and SPDX headers, coding standard, Makefile, lint, audit, and a stub top level synthesised in all tools.
   - *Verify:* `make lint audit` is clean; the stub synthesises in Vivado (35T), Quartus and Yosys.
2. **Spec models.** The Python golden arithmetic model, cross-checked against SoftFloat/TestFloat, and a dialog model that replays the dialogs of manual Figures 7-17 to 7-31.
3. **BIU and bus functional model.** All port sizes, byte lanes, DSACK encodings, auto-answers, protocol violation, and randomised clock phase.
   - *Verify:* scripted CIR tests in the style of RD68021's `make cpif`; the AC timing checker on pin event logs.
4. **Sequencer, assembler, ISS and lockstep.** Dialog microcode for every class, with arithmetic stubbed.
   - *Instructions:* FMOVE.X, FMOVEM, moves of the control registers, conditionals, FNOP, null and idle FSAVE/FRESTORE.
   - *Verify:* RD68021 running the matching subset of `fpu.S`; Verilator lockstep of the RTL against the ISS.
5. **Core arithmetic.**
   - *Scope:* unpack and pack for B, W, L, S, D, X; add, subtract, multiply, divide, square root; FSGL*, FINT, FGETEXP/FGETMAN; every rounding mode and precision; exceptions and ETEMP.
   - *Verify:* TestFloat vectors through the ISS and Verilator; `fparith` and `fparith-dbl` bit-exact; most of `fpu.S`.
   - **Synthesis checkpoint:** LUTs and Fmax on the 35T, and the decision on branch timing (two cycles or delay slot).
6. **FMOD/FREM, FSCALE, FMOVECR, packed decimal.**
   - *Verify:* mpmath and golden-model vectors; `fpu.S` checks 51–54 (SunOS's A93N mask test and gradual underflow).
7. **Transcendentals**, one function at a time, developed in the ISS first.
   - *Verify:* ulp histograms against mpmath (at most 1 ulp of double, with special values checked); then sampled runs on the RTL.
8. **Exception and context corner cases.**
   - *Scope:* pending, mid-instruction, BSUN, protocol violation, abort, come-again, busy frames with faults injected in the middle of FMOVEM, interrupts and trace while polling.
   - *Verify:* `fpu.S` all 54 checks; new targeted programs.
9. **System.**
   - SunOS through TME, with the FPU as a Verilator model next to RD68021, compared against TME's MC68881.
   - Full Vivado implementation on the 35T at 50 MHz core, 20 MHz bus.
   - Later, hardware with level-shifted 5 V I/O.

`make check` (ucode check, lint, audit, unit simulations, ISS tests and a short system run) must pass at every milestone. Size is re-measured at milestones 5, 7 and 9.
