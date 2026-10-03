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

M5 notes:
- The utilisation-by-hierarchy report puts most of these LUTs in `u_rom`. Vivado has optimised across the ROM's boundary, so its outputs are decoded control rather than the 116 stored bits: the instance has 613 output pins and no flip-flops of its own besides the 3 RAMB36. Only the total is meaningful.
- **About 3,400 LUTs are the M5 datapath**, about 16% of the 35T. That is over the budget's share for this point. The candidates for the next reduction are:
  - the operand multiplexer into `A`'s mantissa: about 20 sources, 72 bits wide;
  - the 90-bit comparator FCMP uses, which the adder could replace;
  - the unpackers, which could move into microcode.
- The microcode store is 944 words of 116 bits: 3 RAMB36 at 1K × 36, and the remaining bits in logic. The register file is 1 RAMB36 + 1 RAMB18.

Budget (doc/architecture.md): about 3.5–4.5K LUTs, 1.3K flip-flops, 3–4 DSP and 10–15 RAMB36 for the complete design, with Fmax of 45–60 MHz.
