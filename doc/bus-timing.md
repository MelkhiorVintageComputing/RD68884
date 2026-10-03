# The bus: how the BIU meets the MC68881's AC specifications

`rtl/rd68884_biu.sv` answers every bus cycle asynchronously (FPU 10.4.2/10.4.3). The specification numbers below refer to `MC68881UM_split/ac-electrical-specifications.csv`.

## The specifications the FPU drives

| Spec | Limit (16.67 MHz) | How RD68884 meets it |
|---|---|---|
| 14 | CS, DS asserted to data valid, 80 ns max | **Not as an absolute time** (wait states; see below) |
| 15 | DS negated to data invalid, 0 ns min | Data comes from a register that changes only on the next access |
| 16 | DS negated to data high impedance, 50 ns max | The data enables are gated combinationally by the raw START term: one LUT and the pad |
| 19 | START to DSACK asserted, 50 ns max | **Not as an absolute time** (wait states; see below) |
| 19A | DSACK0/DSACK1 skew, ±15 ns | Both come from one register through identical gating |
| 20 | DSACK asserted to data valid, 50 ns max | Data and DSACK are enabled by the same gate, from registers loaded together |
| 21 | START false to DSACK negated, 50 ns max | DSACK is released combinationally by START (see "DSACK release" below) |
| 22 | START false to DSACK high impedance, 70 ns max | As 21 |
| 23–27 | Synchronous read cycle | Not used: every cycle is asynchronous (FPU 10.4) |

`sim/tb/biu_tb.sv` checks 15, 16, 19A, 20, 21 and 22 at every strobe change and every core-clock edge, with no delay allowed. That check is structural only; pad and routing delays are added by the board.

### Specifications 14 and 19: wait states

The core samples the strobes through two synchroniser stages. It then decodes the access and loads the answer, so DSACK appears three to four core clocks after START.

| Core clock | DSACK after START |
|---|---|
| 50 MHz | about 60–80 ns |
| 16.67 MHz (core = bus) | up to 240 ns |

This exceeds the 50 ns of specification 19 at 16.67 MHz. The protocol tolerates it: the main processor simply waits for DSACK. FPU 10.5 itself withholds DSACK when the FPU is not ready, and note 2 of the AC table scopes specification 19 to an FPU that is not busy.

The cost is one or two wait states per CIR access, which is performance, not compatibility. On a board where the core runs on the main processor's own clock, the same-clock BIU removes them (below). Specification 20, the one the MC68020 depends on to latch read data, is met with no margin needed.

### DSACK release

FPU 9.8: "DSACK1 and DSACK0 lines are actively pulled up (negated) by the FPCP following the rising edge of AS or DS, and ... then three-stated." RD68884 three-states them at once, without the actively driven high phase. The board's pull-ups then raise the lines. `doc/divergences.md` records this.

## Inputs

The address, R/W and the SIZE/A0 straps are sampled once START has passed the synchronisers. Write data is sampled once DS has passed them. By then the inputs have been stable for at least two core clocks: specification 6 puts the address before AS, and specification 17 puts the data before DS. So sampling these asynchronous inputs directly is safe, and they need no synchronisers of their own.

## The shortest gap between cycles

Specification 13 lets DS stay negated for only 40 ns (23 ns at 33 MHz). A core clock slower than that could miss the end of a cycle. Two things prevent it:

- **The stale guard.** A flip-flop is set *asynchronously* the moment START falls, and cleared by the core only once it has retired the cycle.
  - The DSACK and data enables need it clear, so a new cycle can never be acknowledged by the old cycle's still-armed request.
  - The core takes the guard's synchronised copy as the end of the cycle, so even a gap shorter than one core clock is seen.
- **The other side of the same mechanism.** A new cycle is not decoded until the guard has been cleared and seen clear, so two cycles cannot merge.

The result: any core clock works, and a slower one only adds wait states. The testbench runs a 61.3 ns core against a 60 ns bus, and a 13 ns core against a 30 ns bus. The 50 MHz constraint is about the datapath (doc/architecture.md), not about the bus.

## Port size

FPU table 9-2 and figure 10-2 define the port size. Each CIR byte always travels on its natural lane of the 32-bit register, so the BIU has no data multiplexer. It enables lanes per access and completes a register on the access that carries its least significant byte. All three port sizes are exercised against an MC68020 bus model that follows UM tables 5-4 and 5-5, with the board's lane ties modelled and contention checked.

## The same-clock BIU (`BUS_SYNC = 1`)

**What it is for.** On a board where the core runs on the main processor's own CLK, with no PLL, the asynchronous front end's synchronisers cost one or two wait states on every CIR access. Everything above assumes an asynchronous bus. The same-clock front end answers on the MC68020's clock edges instead.
- It is a build choice: `BUS_SYNC`, with `BUS_SYNC_WAIT` 0 or 1, parameters of `rd68884_top` and `rd68884_biu`.
- Only the front end differs: the strobes, the stale guard, and where the cycle state machine starts and ends. The CIR registers, the protocol checks and the events are the same code.
- RESET keeps its synchroniser.

**The edges it answers on.** These are the MC68020's states (UM 5.3; RD68021's `rtl/rd68021_biu.sv` alike):

| Edge | Main processor | Zero wait (`BUS_SYNC_WAIT = 0`) | One wait (`BUS_SYNC_WAIT = 1`) |
|---|---|---|---|
| ↓ entering S1 | AS asserted, and DS on a read (UM spec 9) | | |
| ↑ entering S2 (E2) | write data driven (UM 5.1.4, spec 23) | A read is taken from the raw pins, with DSACK and data registered. A ready write gets DSACK. | CS, AS, DS, R/W enter one register rank |
| ↓ entering S3 | DSACK sampled (spec 47A) | DSACK seen: no wait state | |
| ↑ entering S4 (E4), or the first wait state | | The write is taken | Every access is taken from the rank |
| ↓ next | DSACK sampled again | | DSACK seen: one wait state |
| ↓ entering S5 | read data latched (spec 27); AS, DS negated | DSACK and data released at once by the raw START, as in the asynchronous BIU | as zero wait |

**Writes are taken by timing, not by DS.** The data has been on the bus since the edge entering S2, so a write is taken a clock later without waiting for DS.

With zero wait, a write is acknowledged at E2 and taken at E4. It is still ready at E4:
- the only write that can be refused is an operand write while the previous one is untaken (`opw_valid_q`), and only the bus sets that;
- a protocol violation is always ready.

E2 and E4 are consecutive rising edges.

**No stale guard.** Between two cycles AS is negated across at least one rising edge, and the acknowledge is dropped on it. With one wait, the rank's START also gates DSACK. Otherwise, in the clock before the rank catches up, a new cycle could see the previous cycle's acknowledge.

**A late strobe.** With one wait, a strobe that misses E2 is seen a clock later: one more wait state, not a wrong answer. The rank has a whole clock to settle before anything reads it. `biu_tb` runs that case with the strobes 0.7 of a clock after the falling edge, past E2.

### The pins' timing

The input budget for zero wait is half a clock: AS comes up to UM spec 9 after a falling edge and decides the answer at the next rising one. That includes the take, its events and the sequencer's next microcode address.
- **A real MC68020 does not meet it at worst case.** Spec 9 is 30 ns at 16.67 MHz, the whole half period. FPU 10.4.1 says the same of the MC68881's own synchronous read cycles.
- **RD68021 in the same FPGA meets it easily.** Its strobes come from falling-edge flops.

The one-wait build gives the strobes a clock and keeps every other path as it is. So:
- zero wait suits RD68021 on chip;
- one wait suits a real MC68020 on a board.

`make impl BUS_SYNC=1` times the pins against `scripts/rd68884_sync.xdc.in`. The Makefile fills it in from `SYNC_CLK_NS`, `SYNC_IN_NS` (strobes after a falling edge), `SYNC_AIN_NS` and `SYNC_DIN_NS` (address and write data after a rising edge) and `SYNC_SETUP_NS`. The template explains each constraint and its two exceptions:
- R/W reaches DSACK through START, a clock-and-a-half path;
- the hold checks of the inputs that change only on edges where nothing is taken.

xc7a35t, out of context:

| Build | Clock | Strobes after ↓ | Pins | Worst pin path |
|---|---|---|---|---|
| zero wait | 60 ns | 30 ns (MC68020, spec 9) | **not met**, −6.3 ns | AS → the microcode ROM's address |
| zero wait | 60 ns | 8 ns (on chip) | met, +9.9 ns | DS → the microcode ROM's address |
| one wait | 60 ns | 30 ns (MC68020) | met, +16.1 ns | A3 → the microcode ROM's address |
| one wait | 30 ns (33.33 MHz) | 15 ns (MC68020 at 33.33 MHz; specs 6, 23: 21, 18 ns) | met, +7.3 ns in, +5.1 ns out | DS → DSACK, through START |

The two zero-wait rows bound the budget: at 16.67 MHz it closes with the strobes up to about 18 ns after the falling edge. With one wait state, a real MC68020 is met at every speed grade up to 33.33 MHz. The core's own paths need about 20 ns there, inside the 30 ns clock.

| Build (xc7a35t, implemented) | LUTs | FFs | DSP | BRAM |
|---|---|---|---|---|
| asynchronous (the default, M9) | 4349 | 915 | 6 | 21 |
| same clock, zero wait | 4358 | 904 | 6 | 21 |
| same clock, one wait | 4356 | 908 | 6 | 21 |

The same-clock front end trades the synchronisers and the stale guard (11 flip-flops) for the pin multiplexer in front of the decode. The LUT counts are within place-and-route noise of each other.

### Checked by

- **`biu_tb`**, built with `BIU_SYNC`, runs every test on all three port widths on a bus model that follows the MC68020's edges on the BIU's own clock, at 16.67, 20, 25 and 33.33 MHz. Each cycle is held to an exact count: its wait states are the rising edges before the BIU took or acknowledged it, and on each of those the BIU must have been unable to. A BIU that acknowledges a ready write a clock late fails it (checked by mutation).
- **`make sys BUS_SYNC=1`** runs every program on RD68021 with the FPU on the same clock, on all three ports, in lockstep with the ISS.
- **`make cycles`** measures the result: `doc/timing-divergences.md`.
