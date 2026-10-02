# Where the manual disagrees with itself

Each entry gives the conflicting passages, the reading this design takes, and the reason. `FPU` means `Inputs/doc/MC68030_Doc_More_Readable/MC68881UM_split/`. That directory's `README.md` lists more errata in the source scan, notably in the AC tables.

## 1. Table 7-7, the null primitive `$0801`

- **The conflict:** the table labels `$0801` with TF = 0. Bit 0 *is* TF, so `$0801` has TF = 1.
- **Reading:** `$0800` means the condition is false and `$0801` means it is true (FPU 7.4.2.1 text).

## 2. Table 7-7, `$3104` / `$3208` / `$320C`

- **The conflict:** the table labels these PC = 1, but bit 14 of each is 0.
- **Reading:** they are MC68882-only evaluate-EA primitives with CA = 0. They are not used, because the personality is MC68881.

## 3. Offset `$08`: the operation-word CIR or reserved

- **The conflict:** table 7-2 names it the operation-word CIR, and table 9-1 calls it reserved.
- **Reading:** the MC68881 does not implement it (FPU 7.2.5). Writes are ignored and reads return all ones, which is the same either way.

## 4. Register-select CIR, low byte

- **The conflict:** FPU 10.1.1 says the low bits are "driven high"; FPU 7.2.9 says they "read as zeros".
- **Reading:** zeros (7.2.9, which describes the register itself). The main processor ignores these bits.

## 5. The F-line primitive, PC bit

- **The conflict:** FPU 7.4.2.5 says an unimplemented command word is answered with a take-pre-instruction-exception primitive with PC = 1. Table 7-7 lists `$1C0B`, which has PC = 0.
- **Reading:** `$1C0B`, the table's value, in the model (`tools/model/cpif.py`). RD68021 serves the PC bit either way, so the choice does not decide whether it works with that core; it can be revisited when an oracle shows the real chip's value.

## 6. Table 4.4.2, the predicate codes

- **The conflict:** the OCR'd predicate codes in table 4.4.2 are wrong.
- **Reading:** table 4-22, checked against the page image, is the one used.

## 7. FLOGNP1 of −1

- **The conflict:** the FLOGNP1 operation table's note 1 says a source of −1 sets DZ "and returns a NAN". FPU 6.1.6 and table 6-3 say DZ, and "for the FLOGx instructions, return minus infinity".
- **Reading:** −∞, which is also the mathematical limit.

## 8. FATANH of ±1

- **The conflict:** both the FATANH description and FPU 6.1.6 say the result is −∞ for +1 and +∞ for −1. That is the opposite of atanh's limits.
- **Reading:** the mathematical sign, +∞ for +1. This is a deliberate departure from two consistent passages. It is to be confirmed against an oracle before the microcode is written, and flipped if the oracle agrees with the manual.

## 9. "INEX2 cleared" for FABS, FNEG, FSCALE and FREM

- **The conflict:** these operation tables say INEX2 is cleared. But FPU 2.2.2 rounds every result stored in a register to the selected precision, and the FMOD/FREM/FSCALE notes themselves say the result "is processed by the normal instruction termination procedure ... an inexact result may occur".
- **Reading:** every result is rounded. The tables are right for extended precision, where these operations are exact.

## 10. Conditional predicates 1xxxxx

- **The conflict:** none in the manual. It is noted here because a natural assumption is wrong: table 4-20 note 3 says 1xxxxx is redundant with 0xxxxx and causes no F-line trap.
- **Reading:** the high bit is ignored.

## 11. Abort and pending exceptions

- **The conflict:** FPU 6.2.2 says an MPU-detected illegal effective address aborts the FPU "without disturbing ... pending coprocessor exceptions". FPU 7.2.2 says any control write on the MC68881 clears pending exceptions.
- **Reading:** 7.2.2, the MC68881-specific statement. An abort clears them. A pending exception would in any case have been reported before the primitive that requested the effective address.
