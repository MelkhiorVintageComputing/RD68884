# RD68884 — SystemVerilog MC68881

A from-scratch SystemVerilog floating-point coprocessor that can be used in place of a Motorola MC68881. It is meant for an MC68020: either a real chip, or the RD68021 replica in `../RD68021`. The target is FPGA.

## Goals

1. **Bus compatibility** with the MC68881/MC68882:
   - the asynchronous bus, with 8-, 16- and 32-bit ports and dynamic DSACK sizing;
   - three-state pins split into `_i`/`_o`/`_oe`, with the three-state pad itself external.
2. **Coprocessor-interface compatibility:** the CIRs, the response primitives, the dialogs, and the FSAVE/FRESTORE frames. The device presents itself as an **MC68881**.
3. **Size first.** It must fit stand-alone in an Artix-7 35T with margin, or a 50T at worst.
   - Count LUTs; DSP blocks and block RAM are plentiful.
   - Cycles per instruction are secondary.
4. At least 16.67 MHz on the bus, preferably 20 MHz or more. The core clock is separate and faster: see `doc/architecture.md`.
5. **Numerical behaviour:**
   - Arithmetic operations and conversions are correctly rounded, exactly as FPU 4.3 requires.
   - Transcendentals meet the manual's accuracy: at most 1 ulp of double, worst case.

## Hard rules

These are not style preferences. Breaking one is a bug.

### `Inputs/` is immutable

Nothing under `Inputs/` may be modified, ever.

### `../RD68021` is read-only

Its tools, documents and test programs may be read, run, and copied into this repository with adaptation. Nothing in that repository is ever changed from here. The system targets use a pinned revision of it (`RD68021_REV` in the Makefile), exported by `git archive` to `build/rd68021-<rev>`, never its working tree.

### Reference implementations are oracles, not sources

You may run these and compare against them, but you may **not** read them to work out how to write RD68884's RTL or microcode:
- mh882 (`../mh882`)
- Musashi
- TME and its MC68881
- QEMU
- MAME and WinUAE
- Berkeley SoftFloat/TestFloat

What may be consulted when designing:
- the Motorola manuals in `Inputs/doc/`;
- published papers and books (for example Tang's table-driven algorithms);
- the algorithms and tables of Motorola's FPSP floating-point software package.

Cite any such source in a comment or in `doc/`.

### No initialisation outside reset

- No `initial` blocks in `rtl/`.
- No declaration-site initialisers on anything that infers a register.
- Every register gets its value from the reset branch of its `always_ff`.
- `make audit` proves this in the source *and* in the yosys netlist. Any exemption is named individually in `tools/reset_audit.py` with the argument for it.

### Split I/O pins

Bidirectional and three-state pins become `_i` / `_o` / `_oe`, where `_oe` high means this chip drives. The data bus has one `_oe` per byte lane. See `doc/pinout.md`.

### Portable SystemVerilog only

- The RTL must elaborate under **iverilog, Verilator, yosys, Vivado, Quartus and Questa**.
- The permitted subset is in `doc/coding-standard.md`. yosys is the strictest of the six, so it defines the subset. Note in particular: no `import`, and no package names in port connections.
- `make lint` is the gate for the three that need no vendor installation. `make synth`, `make lint-quartus` and `make lint-questa` cover the other three.

## Documentation map

Everything authoritative is in `Inputs/doc/MC68030_Doc_More_Readable/MC68881UM_split/`. Its `README.md` lists the manual's own errata.

| Need | Read |
|---|---|
| Programming model: FPCR, FPSR, FPIAR | `06-section-02-programming-model.pdf` |
| Data formats, packed decimal, the intermediate format | `07-section-03-operand-data-formats.pdf` |
| Instruction semantics, accuracy (4.3), predicates (4.4), encodings (4.7) | `08-section-04-instruction-set.pdf` |
| Exceptions, rounding, FSAVE/FRESTORE frames | `10-section-06-exception-processing.pdf` |
| CIRs, response primitives, dialogs | `11-section-07-coprocessor-interface.pdf` |
| Pins, port sizing, bus cycles | `13-section-09-…pdf`, `14-section-10-bus-operation.pdf` |
| AC limits | `ac-electrical-specifications.csv`, `figure-12-*.md` |
| The CPU's side of the coprocessor interface | `MC68020UM_split/11-section-07-coprocessor-interface.pdf` |

Citation conventions:
- In a comment, `FPU` means `MC68881UM_split` and `UM` means `MC68020UM_split`.
- Specification numbers refer to `MC68881UM_split/ac-electrical-specifications.csv`.

Read PDFs with `pdftotext -layout <file> -`. The OCR is unreliable for numerals, so check tables against the page image.

Project documents:

| Document | Contents |
|---|---|
| `doc/architecture.md` | The design and its reasons |
| `doc/pinout.md` | Ports |
| `doc/coding-standard.md` | The SystemVerilog subset |
| `doc/manual-contradictions.md` | Where the manual disagrees with itself, and the reading chosen |
| `doc/size-and-speed.md` | Utilisation and Fmax, milestone by milestone |
| `doc/bus-timing.md` | How the BIU meets the AC specifications, and the stale guard |
| `doc/divergences.md` | Deliberate, protocol-permitted differences from the MC68881 |
| `doc/microcode.md` | The microcode machine: fields, timing rules, traps, the races the BIU settles |
| `doc/model.md` | The Python reference models, how they are checked, and every choice they make where the manual is silent |
| `doc/system.md` | SunOS 4.1.1 in TME with RD68021 and RD68884 (`make sunos`) |

## Building and checking

```sh
make lint     # every rtl module under iverilog, Verilator and yosys
make audit    # prove no register initialises outside reset
make check    # the gate: ucode-check, lint, audit, model-test, iss-test, sim
make sim      # the directed testbenches in sim/tb
make ucode    # regenerate rtl/gen/ from tools/ucode/ (ucode-check is part of check)
make sys      # the system with RD68021 as the MC68020, and the RTL/ISS lockstep
make sunos    # SunOS 4.1.1 in TME on RD68021 + RD68884, against TME's m68020/MC68881 (~15 min)
make model-test      # the reference models' unit tests (doc/model.md)
make testfloat       # the arithmetic model against TestFloat (make oracles builds it)
make iss-arith       # the microcode against the model, ARITH_N random cases
make iss-testfloat   # TestFloat's vectors through the microcode
make iss-trans       # the transcendentals against the model, to FPU 4.3.2's bound
make trans-accuracy  # their error in ulps, against mpmath
make synth    # Vivado out-of-context synthesis, xc7a35t
make impl     # Vivado place and route, xc7a35t
make lint-quartus / make quartus / make lint-questa
```

`make help` lists every target.

## Tooling notes

| Tool | Version / location |
|---|---|
| iverilog | 12.0 (13 in `/usr/local/iverilog-13_0`) |
| Verilator | 5.032 |
| yosys | 0.52 |
| Vivado | 2025.2, in `/opt/Xilinx` |
| Quartus Prime Lite and Questa | in `/opt/Altera`; Questa has no `vsim` licence |
| m68k cross-compiler | `m68k-linux-gnu-gcc` 14.2 |

There is no gtkwave.

Python has no pip/venv here. mpmath, SoftFloat and TestFloat are git submodules in `third_party/`; the Makefile puts mpmath on `PYTHONPATH`, and `make oracles` builds SoftFloat and TestFloat out of tree into `build/oracles/`. After a fresh clone, run `git submodule update --init`.
