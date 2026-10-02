# The microcode machine

The sequencer, its datapath and the microcode: `rtl/rd68884_seq.sv`, `rtl/rd68884_regfile.sv` and `tools/ucode/`.

The four parts and where each is defined:

| Part | Defined in |
|---|---|
| Field table | `tools/ucode/fields.py`. The assembler, the generated RTL package and the ISS all read it |
| Executable meaning of every field | `tools/iss/core.py` |
| RTL transcription | `rtl/rd68884_seq.sv` |
| The program | `tools/ucode/program.py` |

## How the pieces are checked

| Check | What it compares |
|---|---|
| `make iss-test` | The microcode on the ISS, with a Python model of the BIU's registers, against the golden model `tools/model/cpif.py`. Both are driven through the CIRs by the same MC68020 driver. The registers, main-processor state, memory (so FSAVE frames), exceptions and primitives must all match. It includes 300 random arithmetic cases (`tools/iss/tests/test_arith.py`) |
| `make iss-arith` | The same random arithmetic cases, `ARITH_N` of them (20,000 by default): operands drawn towards the specials, the ends of each precision's range and the rounding boundaries; every rounding mode, precision and trap enable |
| `make iss-testfloat` | Berkeley TestFloat's vectors through the microcode, with every dialog, with the skips of `tools/model/check_testfloat.py` |
| `make sys` | `sim/programs/fpu_m4.S` (the dialogs) and `fpu_m5.S` (150 arithmetic vectors from the golden model, 1200 checks) on RD68021 with RD68884 as its coprocessor, for 32-, 16- and 8-bit FPU ports |
| `tools/iss/lockstep.py` (part of `make sys`) | The RTL sequencer against the ISS, every clock of the 32-bit runs: the recorded BIU signals are replayed into the ISS, and the micro-address and every enabled output must be identical |

## Timing rules

- **One microinstruction per clock.** The store is a block-RAM `case` ROM read at the *next* micro-address, so the word arrives with its address.
- **Every action reads the state as it was at the start of the clock.** Results land at the end. For example, `RF WRITE` stores the old `A` even when the same word loads a new one.
- **The register file reads synchronously.** `RF READ` fills `RFQ` at the end of the clock; `ASRC=RFQ` in a later word uses it.
- **The transfer bus.** `TBUS` is chosen by `TSRC`. `T`, the `TDST` destination and a BIU write (`OPR_WR`, `SAVE_WR` and the rest) all take it in the same clock.

## Traps

A trap overrides the next micro-address and kills the current word's actions:

| Trap | Cause | Entry |
|---|---|---|
| Reset | The RESET pin, while it is asserted | `reset` |
| Abort | A control CIR write (FPU 7.2.2) | `abort` |
| Restore | A rising restore request, i.e. a restore CIR write (FPU 7.2.4) | `restore` |

## Events

The BIU raises one-clock events: a response read, a register-select read, a save word read. The core keeps a sticky copy of each, so the microcode never depends on reacting within a clock. The action that makes the next event meaningful re-arms it:

- a response write re-arms `RESP_READ`;
- `RSEL_WR` re-arms `RSEL_READ`;
- `SAVE_WR` re-arms `SAVE_READ`.

## The races the BIU settles in hardware

Each of these was found by the differential tests. Each needs the BIU, because no microcode reaction time is fast enough.

| Race | Fix |
|---|---|
| A command arrives while the microcode is finishing the previous instruction, and the microcode's final `$0802` overwrites the BIU's `$8900` | `RESP=WRC` and `CLEAR` leave the response alone if a command is latched |
| The MPU reads the register select CIR and writes or reads the registers at once | The BIU switches its expectation itself, from `rsel_dir` |
| FSAVE/FRESTORE frames are not followed by a response read | The BIU counts the frame's long words (`XFER`) and expects a command after the last |
| After FRESTORE of a frame with a pending instruction, the interrupted MPU re-reads the response before the microcode has rebuilt the first primitive | The restore CIR write itself sets the response to `$8900`. The restart path skips `CLEAR` |

## The value format

`A`, `RFQ` and the register file hold three fields:

| Field | Width | Contents |
|---|---|---|
| Sign | 1 | |
| Exponent | 18 | Two's complement, unbiased: the biased exponent minus 16383. 16384 marks infinity or NaN |
| Mantissa | 72 | The explicit integer bit at 71, the extended fraction at 70–8, eight extra bits below |

`UNPACKX` and `PACKX` convert to and from the extended memory format (FPU table 3-3). The exponent is copied with the bias removed or added back, and nothing is normalised, so FMOVEM and FMOVE.X move unnormals, denormals and NaN payloads bit for bit. The condition codes are derived from this format (FPU table 2-1).

## The arithmetic

The microcode is `tools/ucode/arith_ucode.py`: opclasses 000, 010 and 011.

### Registers

| Register | Width | Contents |
|---|---|---|
| `A` | 91 | The working value: the source, then the result |
| `B` | 91 | The destination of a dyadic operation; the divisor; the radicand; the saved intermediate result |
| `C` | 80 | The multiplier's accumulator, the quotient, the root |
| `RX` | 4 | The bits above `A`'s mantissa: a carry, a borrow, a partial remainder's top |
| `STK`, `STKB` | 1 each | The sticky bit below `A`'s mantissa, and `B`'s copy of it |
| `SA` | 7 | A shift amount, clamped to 0..127 |
| `RINEX` | 1 | The last rounding was inexact |
| `PSR` | 2 | The precision: X, S, D, or SGLX (single mantissa, extended range, for FSGLMUL/FSGLDIV) |
| `RMR` | 2 | The rounding mode |

Every instruction loads `PSR` and `RMR` from the FPCR once, and clears `STK`.

### Mantissa operations (`MOP`)

| Operation | Effect |
|---|---|
| `ADD`, `SUB` | `A ± B` (`SUB` also subtracts `STK`, the bits shifted out of `B`); carry or borrow into `RX` |
| `NEG` | `0 − A` |
| `SHRA`, `SHRB` | `A` or `B` right by `SA`, the bits lost ORed into `STK` |
| `NORM` | `A` left until its bit 71 is set, the exponent down by as much; nothing if `A` is zero |
| `RSH1` | If `RX` holds a carry: `A` right by one with the carry in, the exponent up |
| `ROUND`, `ROUNDX` | Round `A` at the precision's LSB (bit 8, 19 or 48), or at extended's, in `RMR`'s mode; a carry out renormalises; `RINEX` says whether it was inexact |
| `TRUNCA`, `TRUNCB` | Clear the bits below the precision's LSB (FSGLMUL/FSGLDIV inputs) |
| `MULSTEP` | `C = C/2¹⁶ + A × B[23:8]`, then `B` right by 16: four steps make the 64 × 64 product |
| `MULFIN` | The product's top 72 bits into `A`, the rest into `STK` |
| `DIVSTEP` | One restoring-division step: trial-subtract `B` from `{RX, A}`, the quotient bit into `C` |
| `DIVFIN` | 72 quotient bits into `A`; the rest and a non-zero remainder into `STK` |
| `SQSTEP` | One bit of the square root: the radicand's next two bits from `B`, trial-subtract `4·root + 1` |
| `SQFIN` | The root into `A`; a non-zero remainder into `STK` |
| `EXPF` | `A` becomes its own exponent as a value (FGETEXP) |
| `QUIET`, `INFA`, `MAXA`, `ZEROM` | A quiet NaN, an infinity, the largest number of the precision, a zero |

### The algorithms

- **FADD/FSUB:** the larger exponent goes in `A`, the other is shifted right into `STK`. Unlike signs subtract with `STK` as a borrow, so `A`'s low bits stay exact; an exact zero is +0, or −0 when rounding toward minus infinity (FPU figure 4-2).
- **FMUL:** four `MULSTEP`s. The 128-bit product keeps its top 72 bits and a sticky bit.
- **FDIV:** 74 `DIVSTEP`s, 72 quotient bits and two to place the result.
- **FSQRT:** the radicand's exponent made even, then 72 `SQSTEP`s.
- **Rounding (`post`)** follows FPU 4.5.5.2:
  1. Underflow is checked on the exact intermediate result, and a tiny result is denormalised to the precision's minimum exponent.
  2. Then it is rounded.
  3. Then overflow is checked.

  The intermediate is kept in `B`. The exceptional operand (ETEMP) is made from it: it is rounded to extended and its exponent wraps by ±$6000, or becomes $0000 when the result is catastrophic (FPU 6.1.4 and 6.1.5). FMOVE out to S or D uses the format's range, and ETEMP is the intermediate rounded to the format.
- **FSGLMUL/FSGLDIV:** a tiny result has 24 significant bits, but none finer than the extended quantum (doc/model.md). Up to a 40-bit denormalisation the microcode rounds first and then shifts, which is exact; beyond that, it shifts and then rounds at extended.
- **FINT/FINTRZ:** shifted so that the integer part ends at the extended LSB, rounded there with `ROUNDX`, shifted back, and rounded to the precision like any result.
- **FMOVE out to an integer:** the same rounding at the integer's LSB. Beyond the format's range, OPERR and the saturated value (FPU 6.1.3).

### In the RTL

- **One 79-bit adder:** `X + Y + CIN` serves `ADD`, `SUB`, `NEG`, `ROUND`, `DIVSTEP` and `SQSTEP`.
- **One 72-bit right shifter** serves `SHRA` and `SHRB`, and `NORM` as a right shift of the bit-reversed mantissa by the leading-zero count. The count is taken per byte, then from the highest non-zero byte.
- **The rounding masks are constants** chosen by `PSR`.
- **The multiplier** is a 64 × 16 product added to `C`/2¹⁶. Vivado puts it in four DSP48E1 slices.
- **Branch timing:** one microinstruction per clock, with no delay slot. The M5 checkpoint measures 56.8 MHz after place and route on the xc7a35t-1 (doc/size-and-speed.md), above the 50 MHz the core clock needs.

## Scope

Implemented:
- every coprocessor dialog;
- the control registers;
- FMOVEM with static and dynamic lists;
- the conditionals, BSUN included;
- pre-instruction exceptions, and the mid-instruction exception of FMOVE out;
- F-line;
- protocol violations and aborts (in the BIU);
- FSAVE/FRESTORE of null and idle frames;
- since M5, the arithmetic of opclasses 000/010/011: FMOVE, FINT, FINTRZ, FSQRT, FABS, FNEG, FGETEXP, FGETMAN, FDIV, FADD, FMUL, FSGLDIV, FSGLMUL, FSUB, FCMP, FTST and their alias opmodes; the B, W, L, S, D and X formats in both directions; every precision and rounding mode; the exceptions and ETEMP.

Not yet:
- packed decimal, FMOD, FREM, FSCALE and FMOVECR (M6);
- the transcendentals (M7).

Each of these answers F-line until then. A save request while operands are part-way through a transfer is not yet serviced: the busy frame is M8.
