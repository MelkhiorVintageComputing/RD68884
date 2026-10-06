# The reference models

`tools/model/` holds the golden models that the microcode and, through lockstep, the RTL are checked against. They are written from the manual alone (CLAUDE.md), in Python.

| Module | What it models |
|---|---|
| `xnum.py` | Exact numbers and the single rounding step: FPU figure 6-3, 6.1.4, 6.1.5 and 4.5.5.2 |
| `formats.py` | B, W, L, S, D and X to and from bits, and the raw 80-bit register |
| `packed.py` | Packed decimal in and out, with the k-factor |
| `arith.py` | Every general instruction: results, the EXC/AEXC/CC/quotient bytes, trap vectors, the exceptional operand |
| `transcend.py` | Mathematical reference values (mpmath) and the FMOVECR constant ROM |
| `cpif.py` | The coprocessor interface at the level of CIR accesses: primitives, dialogs, protocol violations, FSAVE/FRESTORE frames. `FPU881`, and `FPU882` for RD68885 (below) |
| `mpu.py` | The MC68020's side of the interface, for driving `cpif.py` and the ISS through the same dialogs |

## How they are checked

| Run | Coverage |
|---|---|
| `make model-test` | 80 unit tests, each tied to a passage of the manual; part of `make check` |
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

**Transcendentals** are exact mathematical values. The microcode is compared with them in ulps, against the manual's bound (FPU 4.3.2), never bit for bit.

## Model choices

These are places where the manual is silent or contradicts itself. Each is marked `MODEL CHOICE` in the code, or listed in `doc/manual-contradictions.md`. All are to be revisited when an oracle (TME's MC68881, real hardware) says otherwise.

| Where | Choice |
|---|---|
| Alias opmodes $05, $07, $0B, $13, $17, $1B, $29–$2F, $39, $3B–$3F (table 4-13 note 3) | The neighbouring defined opmode, with the low bit(s) ignored |
| FPCR precision 11 ("reserved") | Extended |
| An enabled SNAN, OPERR or DZ trap suppresses the store | The condition codes are not updated either |
| FABS, FNEG, FSCALE, FMOD, FREM result | Rounded to the selected precision like every result (FPU 2.2.2). Their tables' "INEX2 cleared" holds in extended precision |
| FSGLMUL/FSGLDIV inputs | Significands truncated to 24 bits, as FPU 4.5.5.2 says ("truncated to 23 bits" is the fraction) |
| FSGLMUL/FSGLDIV tiny results | 24 significant bits, but never finer than the extended denormal quantum 2⁻¹⁶⁴⁴⁶: single precision with the extended exponent range, denormalised at the extended minimum |
| FSCALE with \|integer part of source\| ≥ 2¹⁴ | Always overflows or underflows, as the FSCALE text says, even where the exact result would fit |
| FMOD/FREM with an infinite source or zero FPn | Quotient byte = sign only, zero bits |
| FMOVE to B/W/L out of range | OPERR alone, no INEX2; saturated result |
| Packed output with k ≤ 0 | At least 1 and at most 17 significant digits |
| Transcendental exactness | Exact (no INEX2) only where the result is representable by rule: 2ⁿ, 10ⁿ for 0 ≤ n ≤ 27, log₂ 2^k, log₁₀ 10ⁿ, ln 1, acos 1. By Lindemann's theorem nothing else is exact at a non-zero argument. Until M7 the model guessed from two precisions, which took expm1(−443) for −1 |
| Packed rounding | The current rounding mode, applied once to the exact value (correctly rounded; the manual allows 0.97/1.47 ulp). The microcode meets the manual's bound and is correctly rounded wherever the result can be exact (doc/microcode.md); the tests compare the two to the bound |
| FMOVECR at an undocumented offset | +0.0 |
| FATANH(±1) | DZ and ±∞ with the sign of the source (the manual says the opposite sign; see contradictions) |
| FLOGNP1(−1) | DZ and −∞ (FPU 6.1.6), not the NaN of the table's note |
| Illegal command word | `$1C0B` (table 7-7), not PC = 1 |
| Default NaN | Positive, all-ones significand |
| Infinity | Integer bit written as 0 (it is a don't-care, table 3-3) |
| FMOVE to FPSR | Bits 31–28 and 2–0 written as zero |
| Exception pending with no enabled exception in EXC & ENABLE | Reported as vector 49 (the frame was edited by software, FPU 6.4.2.2) |
| A conditional's result primitive (`$0800`/`$0801`) | Stays in the response CIR once read, rather than reverting to `$0802` |
| Null, come-again and invalid format words | `$0018`, `$0118` and `$0218`, as in the FSAVE description |

## The frames

**Idle frame `$1F18`.** This uses the MC68881 layout of FPU figure 6-4:

| Offset | Contents |
|---|---|
| $04 | Command/condition word, then `$FFFF` |
| $08–$13 | The exceptional operand |
| $14 | All ones: the operand register image, whose byte-valid flags are all zero |
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

The microcode's busy frame has its own layout (doc/microcode.md); the frame is opaque, so the two are compared by what they do, not by their bytes (`tools/iss/tests/test_busy.py`).

## The MC68882 (`FPU882`)

`FPU882` is `FPU881` with a conversion unit (CU) beside the APU (FPU 5.1.1.2). It models stage A of doc/rd68885.md. Stage B, the FMOVEs the CU completes by itself (table 5-5), changes only when things happen, not what the program sees, so the model leaves it out. The ISS and the RTL have it, and `tools/iss/tests/test_overlap.py` holds them to this model.

**What it does, from the manual:**
- **The CU.** While the APU computes, the CU takes the next instruction if it is register to register (FMOVECR included) or opclass 010 with a B, W, L, S, D or X source. It runs that instruction's dialog with the main processor, then waits to hand it to the APU.
- **S, D and X sources** come in with the CA = 0 evaluate-EA primitive (`$1504`, `$1608`, `$160C`; `$55xx` with PC), whether the APU is busy or not (figure 7-19). The main processor goes on after the operand, without reading the response again.
- **B, W and L sources** are fetched with CA = 1. The null (CA = 1, IA = 1) primitive follows until the APU takes the instruction.
- **Everything else** is latched and answered with the null (CA = 1, IA = 1) primitive until it can start (FPU 7.2.6): packed sources, FMOVE out, FMOVEM, the control registers, the conditionals, and a third instruction while the CU is occupied. A conditional starts only when the APU and the CU are both empty (table 5-6).
- **Exceptions persist.** The exception acknowledge (XA) leaves a floating-point exception reported. FSAVE clears it, or a frame restored with EXC PEND inactive (FPU 7.2.2, 7.4.2.5). If the main processor starts an instruction again without clearing it, it is told again.
- **Mid-instruction reports.** An exception from the APU's instruction is reported mid-instruction (`$1D3x`) by an instruction in the CU whose dialog is not finished (FPU 6.1, the FMUL.B example). Otherwise, the next instruction reports it pre-instruction.
- **The abort bit.** AB (bit 0 of the control CIR) aborts only the last instruction received; the APU's goes on (FPU 7.2.2).
- **The PC is mandatory.** Once a primitive asks for it, any other access in the lists of FPU 6.1.12 is a protocol violation, and so is an unasked write to the instruction address CIR.
- **FPIAR** changes when an instruction reaches the APU, not when its PC is passed (FPU 7.2.10).

**Model choices:**

| Where | Choice |
|---|---|
| An exception raised while the CU holds an instruction | The CU does not hand it over until FSAVE takes it away, or a frame with EXC PEND inactive is restored. The handler runs before the next instruction does, and the order is kept (FPU 6.1: "one at a time") |
| FMOVEM or a control-register move while an exception holds an instruction in the CU | Reports the exception, as an arithmetic instruction would. Going ahead would show it the registers before the CU's instruction has run. With the CU empty they do not report, as on the MC68881 (FPU 6.4.2.2) |
| The idle response | `$0802` (PF = 1) only when the APU and the CU are both empty; `$0900` otherwise |
| AB outside its window (FPU 7.2.2: "undefined") | The BIU returns to idle; the APU and a released CU instruction are untouched |
| A take-exception primitive at FSAVE | Not saved as a pending instruction once the main processor has read it: it starts the instruction again after the handler. One not yet read is saved as the pending instruction, as `FPU881` does |
| The extended store (FMOVE.X FPm,`<ea>`, FPU 7.5.1.3) | CA = 0 (`$320C`, figure 7-21) for a normal, zero or infinite source; a NaN, unnormal or denormal one takes figure 7-20's dialog |
| The fully concurrent FMOVEs (table 5-5), in the ISS and RTL | Also only when no exception is enabled. The older instruction in the APU cannot trap after the younger FMOVE has completed, and no PC is passed. Their FPSR effects (condition codes, the cleared exception byte) wait for the APU's instruction to end, so FPSR follows program order |

**Idle frame `$1F38`.** The MC68881's layout, with the CU's eight long words after the command word (FPU figure 6-5):

| Offset | Contents |
|---|---|
| $04 | Command/condition word, then `$FFFF` |
| $08 | CU: its command word (31–16); bit 15 is 0 if the CU holds an instruction; bit 14 set if it has released the main processor; bit 13 set if its PC was passed; bits 12–8 ones; bits 7–0 the operand long words still to come |
| $0C | CU: the PC passed, or all ones |
| $10–$1B | CU: the operand long words received, then all ones |
| $1C–$27 | All ones |
| $28–$33 | The exceptional operand |
| $34 | All ones: the operand register image |
| $38 | The BIU flags |

An empty CU is eight long words of ones. FSAVE finds an instruction in the CU only when an exception holds it there; otherwise FSAVE answers come-again until the CU's instruction has gone through the APU.

**Busy frame `$1FD4`, 212 bytes.** This is opaque, like the MC68881's. The CU's eight long words come first (FPU 6.4.2), then the tag, the response CIR, the dialog, the exceptional operand and the buffered long words. The BIU flags are the last long word, where figure 5-6's handler sets EXC PEND (`BSET #3,(SP,D0)`, with D0 the frame's size). A busy frame is taken when a dialog is part-way through, either the APU's or the CU's. The CU's case includes the mid-instruction report above.

**How it is checked** (`tools/model/tests/test_882.py`):
- the dialogs, primitive by primitive, against the passages above;
- 600 random programs of 30 instructions each, run on `FPU881` and on `FPU882` with an APU slow enough that every instruction overlaps the one before. Half run with exceptions enabled, and each exception goes through figure 5-6's handler. The two runs must end with the same registers, memory, values stored, conditions, and exceptions (vector and FPIAR).
