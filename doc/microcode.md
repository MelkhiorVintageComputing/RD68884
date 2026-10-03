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
| `tools/iss/tests/test_packed.py` (part of `iss-test`; `PACKED_N` cases) | The packed conversions against the exact value: FPU 4.3.3's bound, and INEX1/INEX2 exactly when inexact |
| `make sys` | On RD68021 with RD68884 as its coprocessor, for 32-, 16- and 8-bit FPU ports: `sim/programs/fpu_m4.S` (the dialogs); `fpu_m5.S` and `fpu_m6.S` (150 and 120 vectors from the golden model, M5's and M6's operations); `fparith.c` (copied from RD68021) bit for bit against the host's x87 and SSE; RD68021's `fpu.S`, adapted (its header lists how) |
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
| On an 8-bit port the MPU reads the response in two byte cycles, and the microcode can write a new primitive between them, even in the clock of the first byte | The first byte snapshots the response, and a write after the snapshot marks it stale. The second byte then returns the snapshot and raises no read event, so a one-shot primitive the MPU has not seen is kept. A write in the clock of the first byte counts as after it (found by `fpu_m6` at M6) |

## The value format

`A`, `RFQ` and the register file hold three fields:

| Field | Width | Contents |
|---|---|---|
| Sign | 1 | |
| Exponent | 18 | Two's complement, unbiased: the biased exponent minus 16383. 16384 marks infinity or NaN |
| Mantissa | 72 | The explicit integer bit at 71, the extended fraction at 70–8, eight extra bits below |

`UNPACKX` and `PACKX` convert to and from the extended memory format (FPU table 3-3). The exponent is copied with the bias removed or added back, and nothing is normalised, so FMOVEM and FMOVE.X move unnormals, denormals and NaN payloads bit for bit. The condition codes are derived from this format (FPU table 2-1).

## The arithmetic

The microcode is `tools/ucode/arith_ucode.py`, for opclasses 000, 010 and 011, and `tools/ucode/packed_ucode.py`, for the packed decimal conversions.

### Registers

| Register | Width | Contents |
|---|---|---|
| `A` | 91 | The working value: the source, then the result |
| `B` | 91 | The destination of a dyadic operation; the divisor; the radicand; the saved intermediate result |
| `C` | 88 | The multiplier's accumulator, the quotient, the root; `C[6:0]` is FMOD/FREM's quotient byte |
| `RX` | 4 | The bits above `A`'s mantissa: a carry, a borrow, a partial remainder's top |
| `STK`, `STKB` | 1 each | The sticky bit below `A`'s mantissa, and `B`'s copy of it |
| `SA` | 7 | A shift amount, clamped to 0..127 |
| `RINEX` | 1 | The last rounding was inexact |
| `PSR` | 2 | The precision: X, S, D, or SGLX (single mantissa, extended range, for FSGLMUL/FSGLDIV) |
| `RMR` | 2 | The rounding mode |
| `QSTK` | 1 | The sticky bit of the constant in `RFQ`; `ASRC RFQ` ORs it into `STK` |

Every instruction loads `PSR` and `RMR` from the FPCR once, and clears `STK`.

The microcode's register-file temporaries are entries 16 to 22 (`T_X`, `T_Y`, `T_ILOG`, `T_LEN`, `T_S`, `T_HI`, `T_Q`).

### The constant ROM

The constant ROM is `tools/ucode/crom.py`, generated into `rtl/gen/rd68884_crom.sv`. It has 512 entries in the working format, each truncated to 72 bits with a sticky bit for whatever was cut off. Rounding an entry at 64 bits or fewer is therefore the correct rounding of the true constant. `RF CROM` reads it into `RFQ` at `IMM` plus an offset: the command word's bits 6–0 (FMOVECR), or bits 5–0 or 12–6 of `A`'s exponent (the powers of ten).

| Base | Contents |
|---|---|
| `$000` | FMOVECR's 128 offsets; the reserved ones are +0.0 |
| `$080` | 10^r, r = 0..63; exact up to r = 31 |
| `$0C0` | 10^−r, r = 0..63 |
| `$100` | 10^(64j), j = 0..79 |
| `$180` | 10^−(64j), j = 0..79 |

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
| `MULSTEP` | `C = C/2¹⁶ + A × B[15:0]`, then `B` right by 16: five steps make the 72 × 72 product, of which `C` keeps the top 80 bits |
| `MULFIN` | The product's top 72 bits into `A`, the rest into `STK` |
| `DIVSTEP` | One restoring-division step: trial-subtract `B` from `{RX, A}`, the quotient bit into `C` |
| `DIVFIN` | 72 quotient bits into `A`; the rest and a non-zero remainder into `STK` |
| `SQSTEP` | One bit of the square root: the radicand's next two bits from `B`, trial-subtract `4·root + 1` |
| `SQFIN` | The root into `A`; a non-zero remainder into `STK` |
| `EXPF` | `A` becomes its own exponent as a value (FGETEXP) |
| `QUIET`, `INFA`, `MAXA`, `ZEROM` | A quiet NaN, an infinity, the largest number of the precision, a zero |
| `MUL10`, `ADDDIG` | `A = 10·A`; `A = A +` the digit at `XI0[3:0]` (packed in) |
| `QINC` | `C[6:0] + 1`: the quotient of an FREM rounded up |

### The other operations of M6

| Field | Value | Effect |
|---|---|---|
| `EOP` | `LDB` | `A`'s exponent = `B`'s |
| | `ADDBI` | `A`'s exponent ± `B[23:8]`, the integer FSCALE takes from its source |
| | `NEGE` | `A`'s exponent negated |
| | `EXP10` | `A`'s exponent × 10 + the digit at `XI0[27:24]` (packed in) |
| | `LOG10` | `A`'s exponent `E` becomes ⌊E·log₁₀2⌋, computed as `(E × $4D104D42) >> 32` |
| | `LDK`, `SUBK` | `A`'s exponent = k, or minus k: the k-factor in `MASK[6:0]` |
| `XOP` | `DIGL`, `EDIGL` | Shift the mantissa digits `{XI0[3:0], XI1, XI2}`, or the exponent digits `XI0[27:16]`, left by one digit |
| | `DIGR`, `EDIGR`, `EDIG3` | Shift one digit in from the left, or put it in `XI0[15:12]`: the remainder `{RX[0], A[71:69]}` of a division by ten |
| | `PINIT` | `XI` = 0, except SM = `A`'s sign and SE = `A`'s exponent's sign |
| `SGN` | `XI0` | `A`'s sign = `XI0[31]` (SM) |
| `FPSR` | `QSIGN`, `QBITS` | The quotient byte: its sign = `A`'s sign xor `B`'s; its seven bits = `C[6:0]` |
| `CTR` | `LOADE` | `CTR` = `A`'s exponent, for FMOD/FREM's step count |
| `FLAG` | `SET_STK` | `STK` = 1 |

The `LOG10` constant is accurate to about 2⁻³² relative. That is exact for every exponent of the range: |E| < 2¹⁵, and E·log₁₀2 never comes within 3·10⁻⁵ of an integer there (the convergent 4004/13301 is the closest).

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
- **FMOVECR:** the constant from the ROM, with its sticky bit, rounded by `post` like any result.
- **FSCALE:** the source's integer part, truncated, added to FPn's exponent (`ADDBI`), then `post`. From |source| ≥ 2¹⁴ the exponent goes out by ±65534, so the overflow or underflow is certain and its ETEMP catastrophic, as the FSCALE text says.
- **FMOD/FREM:**
  1. Restoring division of |x| by |y|, one quotient bit a clock. There are as many steps as the exponents differ plus one, so up to about 32,800 clocks.
  2. `{RX, A}` is then twice the remainder, in units of y's LSB.
  3. FREM rounds the quotient to nearest: if 2r > |y|, or 2r = |y| with the quotient odd, the remainder becomes |y| − r. That subtraction is exact. The sign flips and `QINC` adds one to the quotient.
  4. The remainder takes x's sign, and a zero remainder is a zero of that sign. It is rounded as any result. The quotient byte is sign(x) xor sign(y), and the quotient's seven low bits.
- **Packed decimal in** (FPU 3.3, 6.1.8):
  1. The 17 digits are accumulated as M = 10·M + d (`MUL10`, `ADDDIG`), with non-decimal digits taken positionally as the manual says. The exponent is accumulated the same way with `EXP10`.
  2. The value is M × 10^(e − 16), scaled by `scale10` (below).
  3. It is rounded to extended whatever the precision, INEX1 if inexact, and then goes on as any source.
  4. SE, YY and $FFF together are an infinity or a NaN: `UNPACKX` takes that image as it stands.
- **Packed decimal out** (FPU 4.6 FMOVE):
  1. **The digit count.** k > 17 is an OPERR, and is then taken as 17. `LOG10` estimates ilog = ⌊log₁₀|x|⌋; the estimate is low by at most one. The digit count is k, or for k ≤ 0 the digits left of 10^k, clamped to 1..17 (doc/model.md).
  2. **The scaling.** Y = x·10^(len − 1 − ilog). If |Y| ≥ 10^len, the estimate was low: ilog goes up by one and len is worked out again.
  3. **The rounding.** Y is rounded to an integer q in the current mode, INEX2 if inexact. If q = 10^len, a carry happened: ilog goes up by one, len too if k ≤ 0 (up to 17), and the scaling is redone.
  4. **The digits.** q × 10^(17 − len) is divided by ten 17 times, with `dig10` (69 `DIVSTEP`s against 10·2⁶⁸) and `SQFIN`. Each remainder is shifted in with `DIGR`. The exponent's digits follow the same way; a fourth one is an OPERR (FPU 6.1.3).
  5. **The specials.** Infinities, NaNs and zeros are exactly `PACKX` of the value.
- **`scale10`:** A = x × 10^s.
  - For 0 ≤ s ≤ 27 it is one multiply by the exact 10^s; for −27 ≤ s < 0, one division by the exact 10^|s|. Both are correctly rounded once the result is rounded.
  - Otherwise it multiplies by 10^(±(|s| mod 64)) and then by 10^(±64·⌊|s|/64⌋), both truncated entries, and sets `STK`. The scaled value is within about 2⁻⁶⁹ of the truth, a small fraction of the 0.47 units of slack the bound leaves.
  - Only at |s| ≤ 27 can a conversion's result be exact: for |s| ≥ 28, x·10^s would need 5^|s| to divide a 64-bit significand, or to fit in one. So the inexact flags are exact too.
  - The golden model converts exactly. `tools/iss/tests/test_packed.py` holds the microcode to FPU 4.3.3's 0.97/1.47 ulp and to exact INEX flags. `tools/iss/tests/test_arith.py` accepts a last-place difference from the model in a packed conversion, and nothing else.

### In the RTL

- **One 79-bit adder:** `X + Y + CIN` serves `ADD`, `SUB`, `NEG`, `ROUND`, `DIVSTEP`, `SQSTEP`, `MUL10` and `ADDDIG`.
- **One 72-bit right shifter** serves `SHRA` and `SHRB`, and `NORM` as a right shift of the bit-reversed mantissa by the leading-zero count. The count is taken per byte, then from the highest non-zero byte.
- **The rounding masks are constants** chosen by `PSR`.
- **The multiplier** is a 72 × 16 product added to `C`/2¹⁶, in DSP48E1 slices. `LOG10`'s 18 × 32 product takes two more.
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
- since M5, the arithmetic of opclasses 000/010/011: FMOVE, FINT, FINTRZ, FSQRT, FABS, FNEG, FGETEXP, FGETMAN, FDIV, FADD, FMUL, FSGLDIV, FSGLMUL, FSUB, FCMP, FTST and their alias opmodes; the B, W, L, S, D and X formats in both directions; every precision and rounding mode; the exceptions and ETEMP;
- since M6, FMOD, FREM (with the quotient byte), FSCALE, FMOVECR, and the packed decimal format in both directions, with static and dynamic k-factors.

Not yet: the transcendentals (M7), which answer F-line until then. A save request while operands are part-way through a transfer is not yet serviced: the busy frame is M8.
