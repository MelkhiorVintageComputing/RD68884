# Size and speed, milestone by milestone

Size is the project's first goal (CLAUDE.md), so every milestone records what it costs. The rules for the figures:
- They are Vivado out-of-context figures on **xc7a35tcsg324-1**, from `make synth` or `make impl`.
- The core clock is constrained at 50 MHz (`scripts/rd68884.xdc`).
- The xc7a35t has 20,800 LUTs, 41,600 flip-flops, 90 DSP48E1 and 50 RAMB36.

| Milestone | Run | LUT | FF | DSP | BRAM tiles | Fmax | Notes |
|---|---|--:|--:|--:|--:|--:|---|
| M1 skeleton | synth | 0 | 0 | 0 | 0 | — | strobe synchronisers only; nothing reads them, so all is optimised away |
| M4 BIU + sequencer + dialog microcode | synth | 936 | 708 | 0 | 2.5 | WNS +9.6 ns at 20 ns | utilisation report; the register file is 1 RAMB36 + 1 RAMB18, the microcode store 1 RAMB36 |
| M3 BIU alone | synth | 242 | 329 | 0 | 0 | WNS +13.8 ns at 20 ns | `make synth TOP=rd68884_biu`, all ports kept; budget was ~600 LUTs |
| M5 arithmetic, first transcription | synth | 4912 | 898 | 4 | 5 | WNS +0.75 ns at 20 ns | an adder per operation; the rounder's masks computed by an increment, on the critical path |
| M5 arithmetic | synth | 4331 | 898 | 4 | 5 | WNS +5.15 ns at 20 ns | one 79-bit adder for every mantissa sum; constant rounding masks |
| M5 arithmetic | impl | 4386 | 898 | 4 | 5 | 56.8 MHz (17.60 ns) | `make impl`; limited by microword → rounding → `A`'s mantissa, 29 levels |

| M6 FMOD/FREM, FSCALE, FMOVECR, packed decimal | synth | 4763 | 906 | 6 | 11 | WNS +4.53 ns at 20 ns | +432 LUTs: the constant ROM's address and RFQ multiplexer, the digit shifts, the exponent operations; `LOG10`'s product in 2 DSPs; the constant ROM and a microcode store past 1K words in block RAM |
| M6 | impl | 4744 | 906 | 6 | 11 | 58.1 MHz (17.23 ns) | `make impl`; limited by microword → `A`'s exponent, 31 levels |
| M7 transcendentals | synth | 5171 | 911 | 6 | 20 | WNS +4.76 ns at 20 ns | +408 LUTs: MULSTEP's capture into B, MULHI, LDM, A_POW2, the 12-bit micro-address, the 2K constant ROM's address. Block RAM: the microcode store is 2744 words of 122 bits, the constant ROM 2K × 92 |
| M7 | impl | 5341 | 911 | 6 | 20 | 53.7 MHz (18.63 ns) | `make impl`; still above the 50 MHz the core needs, with less margin; limited by microword → `A`'s exponent, 31 levels |
| M7 size pass | synth | 4380 | 911 | 6 | 20 | WNS +5.31 ns at 20 ns | −791 LUTs, see below |
| M7 size pass | impl | 4363 | 911 | 6 | 20 | 54.5 MHz (18.35 ns) | limited by microword → `A`'s exponent, 34 levels |
| M8 busy frames | synth | 4441 | 915 | 6 | 21 | WNS +5.69 ns at 20 ns | +61 LUTs: `SEQST` (the return stack's top into TBUS, and the push), `REST_BUSY`; 3128 microcode words |
| M9 | impl | 4349 | 915 | 6 | 21 | 55.4 MHz (18.04 ns) | the final design; limited by microword → `A`'s exponent, 36 levels. 21% of the 35T's LUTs, 2% of its flip-flops, 7% of its DSPs, 42% of its block RAM |
| M9 | Quartus fit | 5,770 ALMs | 1,189 | 5 | 64 M10K | — | `make quartus`, for portability: 18% of the device |
| same-clock BIU, zero wait | impl | 4358 | 904 | 6 | 21 | 16.67 MHz bus = core; pins met with strobes ≤ ~18 ns after ↓ (not a real MC68020's 30 ns) | `make impl BUS_SYNC=1`, doc/bus-timing.md |
| same-clock BIU, one wait | impl | 4356 | 908 | 6 | 21 | 33.33 MHz bus = core, pins met against the MC68020's 33 MHz specs; core paths 19.95 ns | `make impl BUS_SYNC=1 BUS_SYNC_WAIT=1 SYNC_CLK_NS=30 ...` |
| IIsiA7 Mini board | impl, xc7a50tftg256-1 | 4447 | 949 | 6 | 21 | 50 MHz met, +2.09 ns | `make board-iisia7`: the whole board top, real I/O, DSACK_NEGATE; doc/boards.md |

M5 notes:
- The utilisation-by-hierarchy report puts most of these LUTs in `u_rom`. Vivado has optimised across the ROM's boundary, so its outputs are decoded control rather than the 116 stored bits: the instance has 613 output pins and no flip-flops of its own besides the 3 RAMB36. Only the total is meaningful.
- **About 3,400 LUTs are the M5 datapath**, about 16% of the 35T. That is over the budget's share for this point. The candidates for the next reduction are:
  - the operand multiplexer into `A`'s mantissa: about 20 sources, 72 bits wide;
  - the 90-bit comparator FCMP uses, which the adder could replace;
  - the unpackers, which could move into microcode.
- The microcode store is 944 words of 116 bits: 3 RAMB36 at 1K × 36, and the remaining bits in logic. The register file is 1 RAMB36 + 1 RAMB18.

Budget (doc/architecture.md): about 3.5–4.5K LUTs, 1.3K flip-flops, 3–4 DSP and 10–15 RAMB36 for the complete design, with Fmax of 45–60 MHz.

## The M7 size pass

The method: Vivado synthesis of variants with one group of operations disabled (each `case` label made unreachable), all from the same baseline. The difference is that group's cost. The noise is about ±100 LUTs.

Cost of each group in the 5171-LUT M7 design:

| Group | LUTs |
|---|--:|
| DIVSTEP, DIVFIN, SQSTEP, SQFIN | 516 |
| RSH1, TRUNCA/B, EXPF, QUIET, MAXA, QINC, NEG, CLRAM | 434 |
| MULSTEP, MULFIN, MULHI | 410 |
| PACKS, PACKD, PACKI, PACKNI, PACKSAT | 349 |
| UNPACKS, UNPACKD, UNPACKL/W/B | 122 |
| The less common exponent operations | 114 |
| FCMP's 90-bit comparator | 108 |
| NORM | 67 |
| ROUND | 57 |
| SHRA, SHRB | 51 |

**The lesson:** each 72-bit source into `A`'s or `B`'s next state costs tens of LUTs, whatever the operation does. Operations that are cheap but wide therefore moved into microcode:
- `CLRAM`: `ZEROM` with `HALF` in the same word.
- `TRUNCA`/`TRUNCB`: shift down, normalise back.
- `MAXA`: three constants in the ROM, read by `PSR`.
- `MULFIN`/`DIVFIN`: one alignment each, then `NORM`.
- `MULHI`: `SQFIN`.
- The comparator: `CMPM`, through the adder, and `AE_EQ_B`.

The saving was 791 LUTs, and Fmax rose slightly.

**What is left**, re-measured on the new baseline:

| Candidate | LUTs | How |
|---|--:|---|
| The divide and square-root steps | about 290 | One shift-then-subtract step for both |
| The exponent operations, with `LOG10`'s two DSPs | about 230 | One shared exponent adder |
| The single, double and integer unpackers | about 220 | Microcode, given a raw load and field extraction |
| The S, D and integer packers | about 160 | The same |
