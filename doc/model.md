# The reference models

`tools/model/` holds the golden models that the RTL and the microcode will be checked against. They are written from the manual alone (CLAUDE.md), in Python.

| Module | What it models |
|---|---|
| `xnum.py` | Exact numbers and the single rounding step: FPU figure 6-3, 6.1.4, 6.1.5 and 4.5.5.2 |
| `formats.py` | B, W, L, S, D and X to and from bits, and the raw 80-bit register |
| `packed.py` | Packed decimal in and out, with the k-factor |
| `arith.py` | Every general instruction: results, the EXC/AEXC/CC/quotient bytes, trap vectors, the exceptional operand |
| `transcend.py` | Mathematical reference values (mpmath) and the FMOVECR constant ROM |
| `cpif.py` | The coprocessor interface at the level of CIR accesses: primitives, dialogs, protocol violations, FSAVE/FRESTORE frames |
| `mpu.py` | The MC68020's side of the interface, for driving `cpif.py` (and, later, the RTL) |

## How they are checked

| Run | Coverage |
|---|---|
| `make model-test` | 64 unit tests, each tied to a passage of the manual; part of `make check` |
| `make testfloat` | `arith.py` against Berkeley TestFloat, 3000 vectors per function and rounding mode |
| `make testfloat-full` | The same with every level-1 vector: about 4.6 million, all passing |

**What TestFloat checks:**
- add, sub, mul, div, rem and sqrt in extended, single and double;
- round-to-integer;
- the conversions between extF80 and f32, f64 and i32, both ways;
- f32/f64 conversions done through the extended registers under single/double rounding precision.

**What TestFloat cannot check**, and so is skipped and covered by the unit tests instead:
- **NaN results.** The MC68881's NaN rules (FPU 4.5.4) differ from SoftFloat's x86 ones.
- **The bottom of the extended range.** On the MC68881, an extended exponent of 0 means 2⁻¹⁶³⁸³ and may hold a normalised number (FPU table 3-3). In extF80 it means 2⁻¹⁶³⁸². Operands and results with a biased exponent below 2 are therefore skipped, and so are extF80 results flushed to zero below SoftFloat's range.
- Single and double precision do share IEEE denormals with the MC68881. Range control (FPU 6.1.7) makes FADD in single precision exactly `f32_add`, denormals included. So that range is checked in full.

**Transcendentals** are exact mathematical values. The hardware will be compared with them in ulps, against the manual's bound (FPU 4.3.2), never bit for bit.

## Model choices

These are places where the manual is silent or contradicts itself. Each is marked `MODEL CHOICE` in the code, or listed in `doc/manual-contradictions.md`. All are to be revisited when an oracle (TME's MC68881, mh882, real hardware) says otherwise.

| Where | Choice |
|---|---|
| Alias opmodes $05, $07, $0B, $13, $17, $1B, $29–$2F, $39, $3B–$3F (table 4-13 note 3) | The neighbouring defined opmode, with the low bit(s) ignored |
| FPCR precision 11 ("reserved") | Extended |
| An enabled SNAN, OPERR or DZ trap suppresses the store | The condition codes are not updated either |
| FABS, FNEG, FSCALE, FMOD, FREM result | Rounded to the selected precision like every result (FPU 2.2.2). Their tables' "INEX2 cleared" holds in extended precision |
| FSGLMUL/FSGLDIV inputs | Significands truncated to 24 bits, as FPU 4.5.5.2 says ("truncated to 23 bits" is the fraction) |
| FSCALE with \|integer part of source\| ≥ 2¹⁴ | Always overflows or underflows, as the FSCALE text says, even where the exact result would fit |
| FMOD/FREM with an infinite source or zero FPn | Quotient byte = sign only, zero bits |
| FMOVE to B/W/L out of range | OPERR alone, no INEX2; saturated result |
| Packed output with k ≤ 0 | At least 1 and at most 17 significant digits |
| Packed rounding | The current rounding mode, applied once to the exact value (correctly rounded; the manual allows 0.97/1.47 ulp) |
| FMOVECR at an undocumented offset | +0.0 |
| FATANH(±1) | DZ and ±∞ with the sign of the source (the manual says the opposite sign; see contradictions) |
| FLOGNP1(−1) | DZ and −∞ (FPU 6.1.6), not the NaN of the table's note |
| Illegal command word | `$1C0B` (table 7-7), not PC = 1 |
| Default NaN | Positive, all-ones significand |
| Infinity | Integer bit written as 0 (it is a don't-care, table 3-3) |
| FMOVE to FPSR | Bits 31–28 and 2–0 written as zero |
| Exception pending with no enabled exception in EXC & ENABLE | Reported as vector 49 (the frame was edited by software, FPU 6.4.2.2) |
| Null, come-again and invalid format words | `$0018`, `$0118` and `$0218`, as in the FSAVE description |

## The frames

**Idle frame `$1F18`.** This uses the MC68881 layout of FPU figure 6-4:

| Offset | Contents |
|---|---|
| $04 | Command/condition word, then `$FFFF` |
| $08–$13 | The exceptional operand |
| $14 | The last operand-CIR long word |
| $18 | The BIU flags |

In the BIU flags:
- Bit 27 is EXC PEND, active low.
- The pending code is 111 (nothing), 011 (a general instruction whose first primitive has not been read) or 001 (the same for a conditional).
- The MC68882-only fields are written as zeros, and bits 15–0 as ones.

On FRESTORE, a pending instruction is restarted from its command word, so the main processor's next response read finds its first primitive again.

**Busy frame `$1FB4`, 180 bytes.** The layout is our own; FPU 6.4.2.3 makes it opaque. It is produced only when a dialog is part-way through transferring operands, for example a page fault in the middle of FMOVEM:

| Long word | Contents |
|---|---|
| 0 | Tag `RD88` |
| 1 | Command word, conditional flag, step index |
| 2 | Long words left to transfer, register mask |
| 3 | The main-processor register received (k-factor or dynamic list) |
| 4 | The response CIR |
| 5 | The BIU flags, with the FMOVE-out trap vector in the low byte |
| 6–8 | The exceptional operand |
| 9 | The number of buffered long words and the released flag |
| 10 onward | The buffered long words, then `$FFFFFFFF` padding |

**While a computation runs**, FSAVE answers come-again until it finishes, then saves an idle frame.
