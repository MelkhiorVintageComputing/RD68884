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

The cost is one or two wait states per CIR access, which is performance, not compatibility. Specification 20, the one the MC68020 depends on to latch read data, is met with no margin needed.

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
