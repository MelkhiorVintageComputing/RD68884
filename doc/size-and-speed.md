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

Budget (doc/architecture.md): about 3.5–4.5K LUTs, 1.3K flip-flops, 3–4 DSP and 10–15 RAMB36 for the complete design, with Fmax of 45–60 MHz.
