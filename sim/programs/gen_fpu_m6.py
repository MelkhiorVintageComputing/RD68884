#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The vectors of sim/programs/fpu_m6.S, from the golden model: FMOD, FREM,
FSCALE, FMOVECR and packed decimal in and out (static and dynamic k).

    python3 sim/programs/gen_fpu_m6.py OUT.S [N] [SEED]

The packed vectors keep to the scales the conversions handle exactly (|s| <=
27, tools/ucode/packed_ucode.py), where the microcode and the model agree to
the bit; the rest of the range is held to FPU 4.3.3's bound by
tools/iss/tests/test_packed.py instead. FMOD/FREM operands are kept within a
few hundred binary orders of each other, so a vector takes thousands of
clocks, not tens of thousands.
"""

import random
import sys

from model import formats as F
from iss.tests.test_arith import x_operand, fpcr
from gen_fpu_m5 import cmd, record, write


def finite(rng, lo, hi):
    """An extended image with a biased exponent in [lo, hi]."""
    s = rng.getrandbits(1)
    e = rng.randrange(lo, hi + 1)
    return (((s << 15) | e) << 80) | (1 << 63) | rng.getrandbits(63)


def packed_exact(rng):
    """A packed image whose scale (exponent - 16) is within +-27."""
    sm, e = rng.getrandbits(1), rng.randrange(-11, 44)
    se = int(e < 0)
    e = abs(e)
    n = rng.randrange(1, 18)
    digits = [rng.randrange(10) for _ in range(n)] + [0] * (17 - n)
    v = (sm << 95) | (se << 94) | (((e // 100) % 10) << 88) | (((e // 10) % 10) << 84) \
        | ((e % 10) << 80) | (digits[0] << 64)
    for i, d in enumerate(digits[1:]):
        v |= d << (4 * (15 - i))
    return v


def vector(rng):
    fc = fpcr(rng)
    k = rng.randrange(6)
    if k == 0:                                   # FMOD, FREM, FSCALE register to register
        dst = finite(rng, 0x3FFF - 200, 0x3FFF + 200) if rng.randrange(4) else x_operand(rng)
        op = rng.choice([0x21, 0x25, 0x26])
        if op == 0x26:
            src = finite(rng, 0x3FFF - 4, 0x3FFF + 16) if rng.randrange(4) else x_operand(rng)
        else:
            src = finite(rng, 0x3FFF - 200, 0x3FFF + 200) if rng.randrange(4) else x_operand(rng)
            if (src >> 80) & 0x7FFF in (0, 0x7FFF) or (dst >> 80) & 0x7FFF in (0, 0x7FFF):
                pass
            elif abs(((dst >> 80) & 0x7FFF) - ((src >> 80) & 0x7FFF)) > 400:
                src = finite(rng, 0x3FFF - 200, 0x3FFF + 200)
                dst = finite(rng, 0x3FFF - 200, 0x3FFF + 200)
        c = cmd(0, 2, 1, op)
        return [], [0xF200, c], record(c, dst, src.to_bytes(12, 'big'), fc)
    if k == 1:                                   # FMOVECR
        off = rng.choice([0x00, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x30, 0x31] + list(range(0x32, 0x40))
                         + [rng.randrange(128)])
        c = cmd(2, 7, 1, off)
        return [], [0xF200, c], record(c, x_operand(rng), bytes(12), fc)
    if k == 2:                                   # FMOVE.P in
        img = packed_exact(rng)
        c = cmd(2, F.FMT_P, 1, 0)
        return [], [0xF22A, c, 12], record(c, x_operand(rng), img.to_bytes(12, 'big'), fc, img)
    # FMOVE.P out: a value with 1 to 17 digits about the decimal point, so
    # that the scale stays within +-27.
    dst = finite(rng, 0x3FFF - 30, 0x3FFF + 50)
    kf = rng.randrange(-8, 18)
    if k == 3:
        c = cmd(3, F.FMT_P, 1, kf & 0x7F)
        return [], [0xF213, c], record(c, dst, bytes(12), fc)
    c = cmd(3, F.FMT_PDYN, 1, 3 << 4)            # k in D3
    return [f'move.l  #{kf}, %d3'], [0xF213, c], record(c, dst, bytes(12), fc, None, kf & 0xFFFFFFFF)


def main():
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    rng = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 6)
    write(sys.argv[1], [vector(rng) for _ in range(n)])


if __name__ == '__main__':
    main()
