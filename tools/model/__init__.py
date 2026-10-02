# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Reference models of the MC68881, written from the manual alone.

    xnum      exact numbers and the rounding core (FPU 6.1.4-6.1.7, figure 6-3)
    formats   the seven data formats to and from bits (FPU section 3)
    packed    packed decimal in and out (FPU 3.3, 4.3.3, FMOVE)
    arith     every general instruction's arithmetic, flags and condition codes
    cpif      the coprocessor interface: CIRs, primitives, dialogs, frames
    mpu       an MC68020-side driver that runs instructions against cpif

Nothing here is derived from another implementation of the chip (CLAUDE.md);
where the manual is silent the choice is marked MODEL CHOICE and listed in
doc/model.md, so it can be revisited when an oracle disagrees.
"""
