#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The vectors of sim/programs/fpu_m7.S: the transcendental instructions.

    python3 sim/programs/gen_fpu_m7.py OUT.S [N] [SEED]

The transcendentals are held to the golden model within FPU 4.3.2's bound,
not to the bit (tools/iss/tests/test_trans.py), so the wanted results here
come from the ISS, which is the definition of the microcode: run on
RD68021, the RTL must reproduce them bit for bit (and the lockstep says it
does clock by clock).
"""

import random
import sys

from model import formats as F
from model.mpu import MPU, CPException
from iss.machine import Machine
from iss.tests.test_arith import x_operand, fpcr
from iss.tests.test_trans import OPS, operand
from gen_fpu_m5 import cmd, write, PATTERN


def record_iss(c, dst, src, fc):
    """What sim/programs/fpu_m5.S's pre, the instruction and post see, on
    the ISS: the same record layout as gen_fpu_m5.record()."""
    m = MPU(Machine())
    m.a[7] = 0x8000
    m.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))
    m.fgen(cmd(2, F.FMT_X, 1, 0), ('imm', dst, 12))
    m.fgen(cmd(2, F.FMT_X, 2, 0), ('imm', src, 12))
    m.fgen(cmd(4, 4, 0, 0), ('imm', fc, 4))
    m.fgen(cmd(4, 2, 0, 0), ('imm', 0, 4))
    m.fgen(c, ('imm', src, 12) if (c >> 13) == 2 else None)
    m.fgen(cmd(5, 2, 0, 0), ('dn', 2))
    fpsr = m.d[2]
    vec = 0
    try:
        m.fcond(0)
    except CPException as e:
        vec = e.vector
        m.fcond(0)
    m.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))
    m.a[1] = 0x5000
    m.fgen(cmd(3, F.FMT_X, 1, 0), ('ind', 1))
    dump = m.mem.read(0x5000, 12)
    return dst.to_bytes(12, 'big') + src.to_bytes(12, 'big') + fc.to_bytes(4, 'big') \
        + dump.to_bytes(12, 'big') + PATTERN.to_bytes(4, 'big') * 3 \
        + fpsr.to_bytes(4, 'big') + vec.to_bytes(4, 'big')


def vector(rng):
    name = rng.choice(list(OPS))
    op = OPS[name] | (3 if name == 'fsincos' else 0)
    src = operand(rng, name)
    fc = fpcr(rng)
    dst = x_operand(rng)
    if rng.randrange(2):
        c = cmd(0, 2, 1, op)
        return [], [0xF200, c], record_iss(c, dst, src, fc)
    c = cmd(2, F.FMT_X, 1, op)
    return [], [0xF22A, c, 12], record_iss(c, dst, src, fc)              # (12,A2)


def main():
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 7)
    write(sys.argv[1], [vector(rng) for _ in range(n)])


if __name__ == '__main__':
    main()
