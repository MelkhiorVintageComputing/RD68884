# RD68884

A SystemVerilog floating-point coprocessor that can stand in for the Motorola MC68881. It is compatible with the original on the bus and in the coprocessor protocol, and is built to be small enough for the smallest Artix-7.

It is the companion of [RD68021](https://github.com/MelkhiorVintageComputing/RD68021), a SystemVerilog MC68020.

**Status: milestone 2, the reference models.**
- Milestone 1: the repository, the coding standard, lint, the reset audit and the vendor scripts are in place, and the top level has its final port list. There is no logic behind it yet.
- Milestone 2: Python golden models of the arithmetic and of the coprocessor interface, written from the manual and checked against TestFloat (`doc/model.md`).

The design and its milestones are in [`doc/architecture.md`](doc/architecture.md).

## Licence

The RTL is under the CERN Open Hardware Licence Version 2, Strongly Reciprocal (`CERN-OHL-S-2.0`); see `LICENSE`.
