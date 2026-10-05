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
| `make sys` | On RD68021 with RD68884 as its coprocessor, for 32-, 16- and 8-bit FPU ports: `sim/programs/fpu_m4.S` (the dialogs); `fpu_m5.S` and `fpu_m6.S` (150 and 120 vectors from the golden model, M5's and M6's operations); `fpu_m7.S` (100 transcendental vectors from the ISS); `fpu_m8.S` (FSAVE in mid-transfer, through injected bus errors and interrupts, and tracing); `fparith.c` (copied from RD68021) bit for bit against the host's x87 and SSE, and its transcendentals reported in ulps against the host's libm; RD68021's `fpu.S`, adapted (its header lists how) |
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
| Abort, AB (RD68885) | A control CIR write with AB that ends the sequencer's own dialog (FPU 7.2.2). It keeps a pending exception | `abort_ab` |

Every trap also clears `RUN` (RD68885, below).

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
| The same split read of the save CIR: begun while come-again, finished after the microcode posted the frame, it raises the save request again. The next read takes the frame, and the stale request then starts a second save nobody asked for | A save read that starts a frame also clears the request. Found by `fpu_m8` at M8, on the 8-bit port |
| RD68885: a CA = 0 transfer is not followed by a response read, so the next command can come right after the last operand long word, before the microcode has taken it | The BIU counts the transfer's long words (`XFER` on the primitive's response write, `ca0`) and expects a command after the last (FPU 7.5.1) |
| RD68885: the MPU reads a primitive asking for the PC in the clock the microcode rewrites the response, which masks the read event | The PC is expected from the value read, not from the event. Found by `fpu_m10` |
| RD68885: the microcode posts its next primitive before the MPU has written the PC | While a PC is owed, a response write sets what is expected after the PC. Found by `fpu_m10` |
| RD68885: the microcode's end-of-instruction write lands while the BIU is in the CU's dialog, or owed a PC | `RESP=WRC` also stands aside for these |
| RD68885: the MPU reads the CU's first primitive in the clock of that end-of-instruction write. A write masks the read event, so the CU never saw its primitive read | Only a write that takes effect masks it; one that stands aside does not. Found by `fparith` |

## The value format

`A`, `RFQ` and the register file hold three fields:

| Field | Width | Contents |
|---|---|---|
| Sign | 1 | |
| Exponent | 18 | Two's complement, unbiased: the biased exponent minus 16383. 16384 marks infinity or NaN |
| Mantissa | 72 | The explicit integer bit at 71, the extended fraction at 70–8, eight extra bits below |

`UNPACKX` and `PACKX` convert to and from the extended memory format (FPU table 3-3). The exponent is copied with the bias removed or added back, and nothing is normalised, so FMOVEM and FMOVE.X move unnormals, denormals and NaN payloads bit for bit. The condition codes are derived from this format (FPU table 2-1).

## The arithmetic

The microcode is in three files:
- `tools/ucode/arith_ucode.py`, for opclasses 000, 010 and 011;
- `tools/ucode/packed_ucode.py`, for the packed decimal conversions;
- `tools/ucode/trans_ucode.py`, for the transcendentals.

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

The microcode's register-file temporaries are entries 16 to 22 (`T_X`, `T_Y`, `T_ILOG`, `T_LEN`, `T_S`, `T_HI`, `T_Q`) and, for the transcendentals, 24 to 59.

### The constant ROM

The constant ROM is `tools/ucode/crom.py`, generated into `rtl/gen/rd68884_crom.sv`. It has 2048 entries in the working format, each truncated to 72 bits with a sticky bit for whatever was cut off. Rounding an entry at 64 bits or fewer is therefore the correct rounding of the true constant. `RF CROM` reads it into `RFQ` at `IMM` plus an offset: the command word's bits 6–0 (FMOVECR), or bits 5–0, 12–6 or 9–0 of `A`'s exponent (the tables).

| Base | Contents |
|---|---|
| `$000` | FMOVECR's 128 offsets; the reserved ones are +0.0 |
| `$080` | 10^r, r = 0..63; exact up to r = 31 |
| `$0C0` | 10^−r, r = 0..63 |
| `$100` | 10^(64j), j = 0..79 |
| `$180` | 10^−(64j), j = 0..79 |
| `$200` | The transcendentals' constants, tables and coefficients, by name (`crom.addr`) |
| `$600` | 2/π, raw: entry 0 is 0, and entry 1 + j holds bits 64j+1 to 64j+64 after the point in mantissa bits 63–0 |

### Mantissa operations (`MOP`)

| Operation | Effect |
|---|---|
| `ADD`, `SUB` | `A ± B` (`SUB` also subtracts `STK`, the bits shifted out of `B`); carry or borrow into `RX` |
| `NEG` | `0 − A` |
| `SHRA`, `SHRB` | `A` or `B` right by `SA`, the bits lost ORed into `STK` |
| `NORM` | `A` left until its bit 71 is set, the exponent down by as much; nothing if `A` is zero |
| `RSH1` | If `RX` holds a carry: `A` right by one with the carry in, the exponent up |
| `ROUND`, `ROUNDX` | Round `A` at the precision's LSB (bit 8, 19 or 48), or at extended's, in `RMR`'s mode; a carry out renormalises; `RINEX` says whether it was inexact |
| `CMPM` | `A`'s mantissa against `B`'s, through the adder: `RX[0]` = less, `RX[1]` = equal. With `AE_LT_B` and `AE_EQ_B` it compares magnitudes (`tools/ucode/arith_ucode_cmp.py`) |
| `MULSTEP` | `C = C/2¹⁶ + A × B[15:0]`, then `B` right by 16 with the 16 bits `C` drops entering at its top. Five steps make the 72 × 72 product: `C` keeps its top 80 bits, and `B[71:8]` its low 64 |
| `MULFIN` | The product's top 72 bits into `A`, its exponent up by one, the rest into `STK`. A `NORM` follows: when the top bit is clear, the bit it moves into `STK` lies below any rounding's guard bit |
| `DIVSTEP` | One restoring-division step: trial-subtract `B` from `{RX, A}`, the quotient bit into `C` |
| `DIVFIN` | 72 quotient bits into `A`; the rest and a non-zero remainder into `STK`; a `NORM` follows, as for `MULFIN` |
| `SQSTEP` | One bit of the square root: the radicand's next two bits from `B`, trial-subtract `4·root + 1` |
| `SQFIN` | The root into `A`; a non-zero remainder into `STK` |
| `EXPF` | `A` becomes its own exponent as a value (FGETEXP) |
| `QUIET`, `INFA`, `ZEROM` | A quiet NaN, an infinity, a zero |
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
| `FLAG` | `SET_STK`, `CLR_STK` | `STK` = 1, or 0 |
| `EOP` | `LDM` | `A`'s exponent = the signed integer at mantissa bits 25–8 (after a shift to bit 8) |
| | `LO6` | `A`'s exponent & 63 |
| `RMR` | `RN`, `RM` | Round to nearest, or down, for the microcode's own integer roundings |
| `COND` | `A_POW2` | `A`'s mantissa is exactly 1 |
| `RFA` | `FPC` | The register file at the command word's bits 2–0 (FSINCOS's cosine) |
| | `PSR` | For the constant ROM: the offset is `PSR`. The largest number of each precision, FPU 6.1.4's untrapped overflow toward zero, is read this way |
| `COND` | `AE_EQ_B`, `RX1` | The exponents equal; `CMPM`'s equality |

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
- **FSGLMUL/FSGLDIV:** the operands are truncated to 24 bits by `trunc2`: shifted down by 48, the dropped bits discarded, and normalised back. A tiny result has 24 significant bits, but none finer than the extended quantum (doc/model.md). Up to a 40-bit denormalisation the microcode rounds first and then shifts, which is exact; beyond that, it shifts and then rounds at extended.
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

## The busy frame (M8)

The busy frame is `tools/ucode/busy.py`. FSAVE can come while an instruction is part-way through moving operands:
- when a bus error stops an operand transfer, and the handler saves the FPU (UM 7.5.2.8);
- when an interrupt is taken while the main processor polls a null primitive with CA and IA set (FPU 7.5.4.3, UM 7.4.1).

Every wait for an operand (`OPW_VALID`, or `!OPR_VALID`) is therefore a loop that also looks for a save request:

```
w:    BR cond -> on;   BR !SAVE_REQ -> w
      CALL busy_save            the return address is the resume token
stub: <replay>                  writes back what the BIU held here
      JUMP w
on:
```

`busy_save` sends 45 long words (format word `$1FB4`). The layout is our own; FPU 6.4.2.3 makes it opaque. FRESTORE receives them in the reverse of FSAVE's order:

| Received | Contents |
|---|---|
| 1 | `SEQST`: the resume address (from the return stack), `MASK`, `RN`, `IS_COND`, `EXC_PEND`, and the `RESP_READ` and `RSEL_READ` events |
| 2 | The command word |
| 3–5 | ETEMP, through `XI` |
| 6–8 | `XI`: the operand being transferred |
| 9 | The BIU flags |
| 10–45 | Ones |

`rest_busy` loads these, pushes the resume address and RETs into the stub. The stub writes the response back:
- **Usually** `$8900`, with the expectation the transfer had: operand writes, or operand reads with the operand register written again.
- **FMOVE out's first wait** may be interrupted before its primitive was read. Its stub then looks at the `RESP_READ` event and writes the primitive itself again.

At any wait only `XI`, `MASK`, `RN`, the command word and the flags are live. Every computation has finished, or not yet started. FSAVE during a computation is answered come-again by the BIU until the microcode reaches a wait, or finishes and saves an idle frame.

A protocol violation pending makes an idle frame, as the model has it.

The checks:
- `tools/iss/tests/test_busy.py`, part of `iss-test`, runs on the model and on the ISS:
  - FSAVE part-way through FMOVEM in and out, FMOVE.X in and out, and a control-register FMOVEM;
  - an interrupt while FMOVE out polls;
  - in each case a kernel that saves the user's registers and runs other FPU work before FRESTORE. Everything but the frame bytes must agree.
- `sim/programs/fpu_m8.S` runs on RD68021 with injected bus errors (`sys_tb`'s RES+$84) and interrupts (RES+$80):
  - bus errors on FMOVEM in and out, FMOVE.X out, FMOVE.D in and a control-register FMOVEM;
  - an interrupt while FMOVE.P out computes (a busy frame) and one while FMOD computes (an idle frame);
  - tracing through polling instructions.

## The conversion unit (RD68885)

The MC68882 build (doc/rd68885.md) adds the conversion unit to the BIU. It adds these microword values; the MC68881 program uses none of them, and its ROM keeps its bits:

| Field | Value | What it does |
|---|---|---|
| `FLAG` | `SET_RUN`, `CLR_RUN` | `RUN`: the sequencer computes a released instruction, so the CU may take the next (the BIU's `apu_run`). Set where an instruction releases the main processor; cleared at `idle` and by every trap |
| `COND` | `CU_READY` | The CU's instruction can go to the APU: its operand is in, its PC (if asked) written, and it is released or a B, W or L source |
| `COND` | `CU_VALID`, `CU_MID` | The CU holds an instruction; its dialog is not over |
| `COND` | `PCODE` | The pending code is set. FSAVE saves an unread take-exception primitive's instruction as pending (both builds) |
| `BIU` | `CU_TAKE` | `CMD` and `XI` from the CU, its PC to FPIAR; a B, W or L source is released (`$0900`) |
| `BIU` | `CU_RESUME` | After FRESTORE of a busy frame: the CU's dialog's response and expectation again |
| `BIU` | `RELATCH` | `CMD` back into the command latch, with `$8900` |
| `TSRC`, `TDST` | `CU` | The CU's frame word `IMM[2:0]` (doc/model.md): out for FSAVE, in for FRESTORE. Loading word 0 with ones empties the CU |
| `XFER` | on a response write | The long words of a CA = 0 transfer |

The paths are `cu_chk`, `cu_take` and the `t_cu`/`t_cufmt` tables, `cu_bsave` and `rest_relatch` in `program.py`, and `cu_rr`/`cu_mem` in `arith_ucode.py`. The MC68882's busy frame (`busy.py`) has the CU's eight long words first and the BIU flags last.

## The transcendentals (M7)

Every function is computed in the 72-bit working format, truncating, and the result is then rounded by `post` like any other, with `STK` forced on. The internal error is a few units of 2⁻⁷¹. `make trans-accuracy` measures the result against mpmath: no worse than 0.54 ulp of extended at round-to-nearest, close to correctly rounded. FPU 4.3.2 allows 2048 ulp, one ulp of double.

Internal arithmetic is done by subroutines that allow zeros: `mulz`, `addz`, `divz` and `sqrtz`, and `rintn` and `rintm` to round to an integer. Polynomials are Taylor series evaluated by Horner's rule, with the coefficients in the constant ROM.

| Functions | Method |
|---|---|
| FETOX, FTWOTOX, FTENTOX | Tang's table method. N = round(x·64/ln 2), and r = x − N·ln2/64 using a two-part constant whose first product is exact. Then 2^(N/64) = 2^M·2^(j/64), with 64 table entries, and e^r − 1 has 7 terms. 2ⁿ for an integer n, and 10ⁿ for an integer 0 ≤ n ≤ 31 (from the constant ROM), are exact cases |
| FETOXM1 | 15 Taylor terms below 1/4, else e^x − 1 |
| FSINH, FCOSH, FTANH | From e^x − 1 or e^x: (E + E/(E+1))/2, (e + 1/e)/2, E/(E+2). From \|x\| = 64, e^\|x\|/2 or ±1 |
| FLOGN, FLOG2, FLOG10 | Tang's table method. x = 2^k·m, F = 1 + j/64, u = (m − F)/F, so ln x = k·ln 2 + ln F + ln(1 + u), with 10 terms. j = 64 moves to the next binade, so that no ln 2 cancels near 1. log₂ of 2^k and log₁₀ of 10ⁿ are exact |
| FLOGNP1, FATANH | Below 1/16, 2·atanh(z/(2 + z)) with 8 odd terms; else ln(1 + z). atanh x = ln(1 + 2x/(1 − x))/2 |
| FATAN, FASIN, FACOS | atan y = atan(c) + atan((y − c)/(1 + yc)), with c = round(16y)/16 and 9 terms; π/2 − atan(1/y) above 1. asin x = atan(x/√((1 − x)(1 + x))), and acos x = 2·atan(√((1 − x)/(1 + x))) |
| FSIN, FCOS, FTAN, FSINCOS | \|x\| is reduced to r in [−π/4, π/4] and a quadrant, then 10 sine or 11 cosine terms are applied and chosen by the quadrant. FTAN is sin/cos. FSINCOS writes the cosine to FPc first, then the sine |

**Argument reduction.** The reduction is exact for every argument:
- **Below 2²⁰:** Cody and Waite, with π/2 in three parts. N·P1 and N·P2 are exact for N < 2²⁰.
- **From 2²⁰ up:** Payne and Hanek.
  1. x = M·2^E. Only four 64-bit chunks of 2/π matter to x·2/π mod 4, from chunk idx = (E + 62) >> 6, and t = ((E + 62) & 63) − 62 places them.
  2. Each M·G is exact: `SQFIN` takes its high half from `C`, and `B` holds its low half.
  3. The words V_i = (L_i + H_(i+1))·2^(t − 64i) are exact sums. With V0 = K + F0, the quadrant is round(K mod 4 + F0 + V1).
  4. r = (((K mod 4) − n) + F0 + V1 + V2)·π/2, the small terms added last so that their error is relative to r.

  The test arguments include the extended numbers nearest to large multiples of π/2.

**Checks.**
- `tools/iss/tests/test_trans.py` (`make iss-trans`; 200 cases in `iss-test`) compares with the golden model. It allows FPU 4.3.2's bound on the value and on ETEMP, and INEX2 only where the model is exact. Every other part of the state must match.
- `sim/programs/fpu_m7.S` runs 100 vectors on RD68021, with expectations from the ISS, which the model holds to the bound.
- RD68021's `fpu.S` (all 54 checks) and `fparith.c`'s transcendentals are run too.

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
- since M6, FMOD, FREM (with the quotient byte), FSCALE, FMOVECR, and the packed decimal format in both directions, with static and dynamic k-factors;
- since M7, every transcendental instruction (FPU table 4-13), with the alias opmodes $07, $0B, $13, $17 and $31–$37.

Every general instruction of the MC68881 is implemented. Since M8, an FSAVE with operands part-way through a transfer takes a busy frame (below).
