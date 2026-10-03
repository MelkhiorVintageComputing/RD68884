# The system: SunOS 4.1.1 with RD68884 as its FPU (M9)

`make sunos` boots an installed SunOS 4.1.1 on a Sun-3/160 in TME, The Machine Emulator. It does so twice:

| Machine | CPU | FPU |
|---|---|---|
| The reference | TME's own m68020 | TME's MC68881 |
| The one under test | RD68021's core (../RD68021 at the pinned revision, used as it stands) | RD68884, on the core's coprocessor interface |

On each machine `drive.sh` logs in as root and writes a C program with `echo`. It compiles the program with `cc -f68881` on the machine itself, then runs it. The program does double and float arithmetic, sqrt and sin, integer conversions and a floating-point comparison. The two consoles must be identical once time stamps are masked, and RD68884 must have answered coprocessor instructions.

## How

**RD68021's revision.** RD68021's checkout belongs to that project and moves. `make sys`, `make tme` and `make sunos` therefore use one pinned revision, `RD68021_REV` (`6d91ae5`). `make rd68021` exports it with `git archive`, which only reads the repository, to `build/rd68021-<rev>`. That revision is RD68021's `mh882_experiment` branch: master's RTL, plus the TME element's `fpu mh882` mode, which is what lets an RTL FPU inside the model answer the coprocessor interface. Master's element has only `fpu m68881`, a C front end on TME's arithmetic.

It follows RD68021's `make sunos-mh882`, which does the same with the mh882 MC68882. Its machinery is documented in RD68021's `doc/sun3.md`.

| | |
|---|---|
| `Inputs/ref/Run-Sun3-SunOS-4.1.1` | TME, the PROM and the EEPROM: a submodule at the commit RD68021 uses |
| `sim/tme/build.sh` | Copied from RD68021 and adapted. It copies TME to `build/tme` (Inputs/ is immutable), adds RD68021's element `tme/ic/rd68021` and links `tmesh` with the model |
| `sim/tme/rd68021_tme_rd68884.sv` | RD68021's core and RD68884 as one Verilator model. The board side of the FPU is `sys_tb`'s: a 32-bit port and an early chip select for CpID 1 |
| `sim/tme/rd68021_model.cpp` | Copied from RD68021 and adapted: the model behind the element's C interface |

The element's `fpu mh882` mode is its "the RTL in the model answers the interface registers" mode. It is used as is, so its log says `MH882` where it means the model's FPU. The Makefile prints `RD68884`.

**Clocks.** The CPU runs at 60 ns. RD68884's core gets three edges of its clock in each half clock of the CPU, which is 20 ns: 50 MHz against a 16.67 MHz bus. As in RD68021's mh882 run, the FPU's clock runs only while an interface-register cycle is on the bus, and for 64 half clocks of the CPU after it (`RD68021_MH882_TAIL`). A computation the CPU waits for is advanced by the response reads it polls with. A released computation, such as FMOD or a transcendental, advances on the next access. This is an FPU that is slower between conversations, which the protocol allows. The setting `RD68021_MH882_TAIL=0` clocks it always.

The disk image is made on the host by the installer in Run-Sun3-SunOS-4.1.1's `diskimage/` directory. `SUNOS_IMG` names it.

## Result

`make sunos` passes. The console under test is identical to the reference's, 82 lines with time stamps masked:

```
sun3# ./t
0 1.1000000000000001 0.214285716 1.0488088481701516 0.89120736006143542 1
...
7 2487.5081375588893 0.000569056894 49.874924937877246 -0.59176296253781913 2487
big
```

The element's report for the run under test:

| Count | Value |
|---|---|
| Coprocessor-interface accesses | 6874 |
| General instructions | 852 |
| Conditional instructions | 57 |
| FSAVEs | 1252 |
| FRESTOREs | 627 |
| Core clocks | 1.86 × 10⁹ |
| Bus cycles | 2.24 × 10⁸ |
| Faults | 5623 |

The saves and restores are SunOS's context switches, made while the program runs. The whole target takes about a quarter of an hour, most of it in the core.
