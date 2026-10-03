# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The packed decimal conversions, through the microcode, against FPU 4.3.3.

The bound: 0.97 unit in the last place of the destination to nearest, 1.47
in the other modes. And the flags: INEX1 (in) and INEX2 (out) exactly when
the result is not the exact value (FPU 6.1.8, 4.3.3).

    PACKED_N=2000 python3 -m unittest iss.tests.test_packed
"""

import os
import random
import unittest
from fractions import Fraction

from model import formats as F, packed as P
from model.mpu import MPU
from iss.machine import Machine
from iss.tests.test_arith import packed_operand, x_operand

N = int(os.environ.get('PACKED_N', '150'))
BOUND = {0: Fraction(97, 100), 1: Fraction(147, 100), 2: Fraction(147, 100),
         3: Fraction(147, 100)}


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def value80(bits80):
    x = F.decode_x(F.reg_from_bits96(((bits80 >> 64) << 80) | (bits80 & ((1 << 64) - 1))))
    return Fraction(*x.frac()) if x.kind in ('fin', 'zero') else None


def packed_value(img):
    """An output image's value, the fourth exponent digit included (FPU
    6.1.3: decode_p, for input, reads three)."""
    digits = [(img >> 64) & 0xF] + [(img >> (4 * (15 - i))) & 0xF for i in range(16)]
    m = 0
    for d in digits:
        m = m * 10 + d
    e = ((img >> 76) & 0xF) * 1000 + ((img >> 88) & 0xF) * 100 \
        + ((img >> 84) & 0xF) * 10 + ((img >> 80) & 0xF)
    if (img >> 94) & 1:
        e = -e
    v = Fraction(m) * Fraction(10) ** (e - 16)
    return -v if img >> 95 else v


def ulp(v, bits=64):
    v = abs(v)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    if Fraction(2) ** e > v:
        e -= 1
    return Fraction(2) ** (e - bits + 1)


class Packed(unittest.TestCase):

    def setUp(self):
        self.m = MPU(Machine())
        self.m.a[7] = 0x8000
        self.m.a[0] = 0x4000

    def fpcr(self, mode):
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', mode << 4, 4))
        self.m.fgen(cmd(4, 2, 0, 0), ('imm', 0, 4))

    def test_in(self):
        rng = random.Random(11)
        bad = []
        for i in range(N):
            img = packed_operand(rng)
            exact = P.decode_p(img)
            if exact.kind != 'fin':
                continue
            exact = Fraction(*exact.frac())
            mode = rng.randrange(4)
            self.fpcr(mode)
            self.m.fgen(cmd(2, F.FMT_P, 1, 0), ('imm', img, 12))
            self.m.fcond(0)              # FNOP: the conversion runs on after the release
            got80 = self.m.fpu.fp_bits80(1)
            got = value80(got80)
            inex1 = (self.m.fpu.fpsr >> 8) & 1
            err = abs(got - exact) / ulp(exact) if got is not None else Fraction(10 ** 9)
            err = min(err, Fraction(10 ** 9))
            if err > BOUND[mode] or inex1 != int(got != exact):
                bad.append(f'{img:024X} mode {mode}: {min(float(err), 1e9):.3f} ulp, '
                           f'INEX1 {inex1}, got {got80:020X}')
        self.assertFalse(bad, '\n' + '\n'.join(bad[:10]))

    def test_out(self):
        rng = random.Random(12)
        bad = []
        for i in range(N):
            x80 = x_operand(rng)
            xr = F.reg_from_bits96(x80)
            x = F.decode_x(xr)
            if x.kind != 'fin':
                continue
            xv = Fraction(*x.frac())
            k = rng.choice([rng.randrange(-20, 18), 17, 1, 0])
            mode = rng.randrange(4)
            self.fpcr(0)
            self.m.fgen(cmd(2, F.FMT_X, 1, 0), ('imm', x80, 12))
            self.m.fcond(0)
            self.fpcr(mode)
            self.m.fgen(cmd(3, F.FMT_P, 1, k & 0x7F), ('ind', 0))
            img = self.m.mem.read(0x4000, 12)
            exc = (self.m.fpu.fpsr >> 8) & 0xFF
            want, _ = P.encode_p(x, k, mode)
            got = packed_value(img)
            # The model's number of digits, for the size of the last one.
            ilog = P.ilog10(x)
            length = k if k > 0 else max(1, min(17, ilog + 1 - k))
            wexp = ((want >> 88) & 0xF) * 100 + ((want >> 84) & 0xF) * 10 + ((want >> 80) & 0xF) \
                + ((want >> 76) & 0xF) * 1000
            if (want >> 94) & 1:
                wexp = -wexp
            if wexp == ilog + 1 and k <= 0 and length < 17:
                length += 1
            unit = Fraction(10) ** (wexp - length + 1)
            err = min(abs(got - xv) / unit, Fraction(10 ** 9))
            inex2 = (exc >> 1) & 1
            if err > BOUND[mode] or inex2 != int(got != xv):
                bad.append(f'{x80:024X} k {k} mode {mode}: {min(float(err), 1e9) if err < 10 ** 300 else 1e300:.3f} digits, INEX2 {inex2}'
                           f' got {img:024X} model {want:024X}')
        self.assertFalse(bad, '\n' + '\n'.join(bad[:10]))


if __name__ == '__main__':
    unittest.main()
