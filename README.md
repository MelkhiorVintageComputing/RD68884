# RD68884

A SystemVerilog floating-point coprocessor that can stand in for the Motorola MC68881. It is compatible with the original on the bus and in the coprocessor protocol, and is built to be small enough for the smallest Artix-7.

It is the companion of [RD68021](https://github.com/MelkhiorVintageComputing/RD68021), a SystemVerilog MC68020. It also works with a real MC68030.

## Status: complete, and running in a Macintosh

Every instruction of the MC68881 is implemented and every coprocessor dialog works.

**It runs on real hardware.** On the IIsiA7 Mini, a card in a Macintosh IIsi's Processor Direct Slot:
- the IIsi boots with RD68884 as its FPU;
- utilities identify it as an MC68881;
- a quick benchmark puts it roughly on par with the 20 MHz MC68882 usual for that machine (`doc/boards.md`).

**It runs SunOS 4.1.1.** In TME, with RD68021 as the CPU, a C program compiled with `cc -f68881` on the machine gives exactly the console of TME's own MC68881 (`doc/system.md`).

**What it implements:**
- **The bus:**
  - the MC68881's asynchronous bus on 8-, 16- and 32-bit ports, with dynamic DSACK sizing;
  - three-state pins split into `_i`/`_o`/`_oe`, with one data enable per byte lane.
- **The coprocessor interface:**
  - every CIR, primitive and dialog of FPU section 7;
  - pre- and mid-instruction exceptions, BSUN, F-line, protocol violations and aborts;
  - FSAVE/FRESTORE of null, idle and busy frames, so a context switch can happen in the middle of an operand transfer.
- **Arithmetic:**
  - every operation and conversion correctly rounded in every mode and precision, checked against Berkeley TestFloat;
  - packed decimal within FPU 4.3.3's bound;
  - the transcendentals within 0.54 ulp of extended precision against mpmath, far inside the manual's one ulp of double.

**Size and speed** on an Artix-7 xc7a35t-1, after place and route:

| Resource | Used |
|---|---|
| LUTs | 4377 (21%) |
| Flip-flops | 921 |
| DSP | 6 |
| Block RAM | 21 tiles |
| Core clock | 54.9 MHz, for a 50 MHz target |

At 50 MHz it runs alongside a 16.67–33 MHz bus. A slower core only adds wait states (`doc/size-and-speed.md`).

**Clock counts** are measured against FPU section 8 for 264 instructions (`doc/timing-divergences.md`). With the core at three times the bus clock, over the 264 instructions RD68884 on RD68021 takes 28% of the MC68881's bus clocks:
- the transcendentals about a third;
- FDIV and FSQRT a half and two fifths;
- packed decimal input about a sixth;
- the control registers and FMOVEM about the same as the manual;
- the conditionals, FSAVE and FRESTORE still longer: RD68021's handling of the protocol, and FSAVE's come-again.

**Build options:**

| Option | Effect |
|---|---|
| default | The MC68881's asynchronous bus |
| `BUS_SYNC=1` | A same-clock BIU: the core on the main processor's own CLK, with no wait states, or one for an external chip |
| `DSACK_NEGATE=1` | DSACK actively negated before release, as the MC68881 does |

## How it is checked

| Target | What it runs |
|---|---|
| `make check` | The gate: microcode check, lint under iverilog, Verilator and yosys, the reset audit, the golden models' tests, the ISS tests and the BIU testbench in every build |
| `make sys` | RD68021 with RD68884 on all three port widths, running the system test programs, with the RTL held to the instruction-set simulator clock by clock |
| `make iss-arith`, `make iss-testfloat`, `make iss-trans` | The microcode against the golden models and TestFloat |
| `make sunos` | SunOS 4.1.1 in TME |
| `make cycles` | The clock counts, frozen as a regression |
| `make synth`, `make impl`, `make quartus`, `make lint-questa` | Vivado, Quartus and Questa |
| `make board-iisia7` | The IIsiA7 Mini's bitstream and flash image |

`make help` lists every target, and `CLAUDE.md` the tools and the rules.

## Documents

| Document | Contents |
|---|---|
| [`doc/architecture.md`](doc/architecture.md) | The design, its reasons and its history |
| [`doc/microcode.md`](doc/microcode.md) | The microcode machine and the algorithms |
| [`doc/bus-timing.md`](doc/bus-timing.md) | The bus, the same-clock BIU, the response hold-off |
| [`doc/pinout.md`](doc/pinout.md) | Ports and parameters |
| [`doc/divergences.md`](doc/divergences.md) | Where it deliberately differs from the MC68881 |
| [`doc/model.md`](doc/model.md) | The golden models |
| [`doc/size-and-speed.md`](doc/size-and-speed.md) | Utilisation and Fmax |
| [`doc/timing-divergences.md`](doc/timing-divergences.md) | Clock counts |
| [`doc/system.md`](doc/system.md) | SunOS in TME |
| [`doc/boards.md`](doc/boards.md) | The IIsiA7 Mini |
| [`doc/manual-contradictions.md`](doc/manual-contradictions.md) | Where the manual disagrees with itself |
| [`doc/coding-standard.md`](doc/coding-standard.md) | The portable SystemVerilog subset |

**After a fresh clone:** `git submodule update --init`. That fetches mpmath, SoftFloat and TestFloat, the SunOS machine and the IIsiA7 Mini's sources.

## Licence

The RTL is under the CERN Open Hardware Licence Version 2, Strongly Reciprocal (`CERN-OHL-S-2.0`); see `LICENSE`.
