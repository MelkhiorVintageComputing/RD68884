#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""How accurate the transcendental microcode is, function by function.

    python3 tools/iss/trans_accuracy.py [N]

Each function runs on the ISS at extended precision, to nearest, on N
arguments over its useful range, and the result is compared with mpmath's
value at 320 bits: the error in units in the last place of extended. FPU
4.3.2 allows 2048 (one of double); a correctly rounded result is within 0.5.
The trigonometric functions also get the extended numbers nearest to large
multiples of pi/2, where the reduction is hardest.
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import mpmath  # noqa: E402
from model import formats as F  # noqa: E402
from model.mpu import MPU  # noqa: E402
from iss.machine import Machine  # noqa: E402

mp = mpmath.mp
FN = {'fsinh': (0x02, mpmath.sinh, -40, 13), 'flognp1': (0x06, mpmath.log1p, -40, 40),
      'fetoxm1': (0x08, mpmath.expm1, -40, 13), 'ftanh': (0x09, mpmath.tanh, -40, 5),
      'fatan': (0x0A, mpmath.atan, -40, 40), 'fasin': (0x0C, mpmath.asin, -40, -1),
      'fatanh': (0x0D, mpmath.atanh, -40, -1), 'fsin': (0x0E, mpmath.sin, -40, 64),
      'ftan': (0x0F, mpmath.tan, -40, 64), 'fetox': (0x10, mpmath.exp, -40, 13),
      'ftwotox': (0x11, lambda v: mpmath.power(2, v), -40, 13),
      'ftentox': (0x12, lambda v: mpmath.power(10, v), -40, 12),
      'flogn': (0x14, mpmath.ln, -16000, 16000), 'flog10': (0x15, mpmath.log10, -16000, 16000),
      'flog2': (0x16, lambda v: mpmath.log(v, 2), -16000, 16000),
      'fcosh': (0x19, mpmath.cosh, -40, 13), 'facos': (0x1C, mpmath.acos, -40, -1),
      'fcos': (0x1D, mpmath.cos, -40, 64)}
POSITIVE = {'flogn', 'flog10', 'flog2'}


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def x96(sign, e, m):
    return (((sign << 15) | (e + 16383)) << 80) | m


def as_mpf(bits80):
    s, e, m = bits80 >> 79, (bits80 >> 64) & 0x7FFF, bits80 & ((1 << 64) - 1)
    v = mpmath.ldexp(mpmath.mpf(m), e - 16383 - 63)
    return -v if s else v


def near_multiples(rng, n):
    """Extended numbers nearest to k pi/2 for large k: the hard reductions."""
    out = []
    with mp.workprec(20000):
        for _ in range(n):
            e = rng.choice([rng.randrange(20, 64), rng.randrange(64, 1000),
                            rng.randrange(1000, 16380)])
            k = (1 << (e - 1)) + rng.getrandbits(max(1, e - 2))
            v = k * mp.pi / 2
            ex = int(mpmath.floor(mpmath.log(v, 2)))
            m = int(mpmath.nint(v * mpmath.mpf(2) ** (63 - ex)))
            if m >> 64:
                m >>= 1
                ex += 1
            out.append(x96(0, ex, m))
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    rng = random.Random(7)
    m = MPU(Machine())
    m.a[7] = 0x8000
    m.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))           # extended, to nearest
    worst_all = 0
    for name, (op, fn, lo, hi) in FN.items():
        args = []
        for _ in range(n):
            s = 0 if name in POSITIVE else rng.getrandbits(1)
            args.append(x96(s, rng.randrange(lo, hi + 1), (1 << 63) | rng.getrandbits(63)))
        if name in ('fsin', 'fcos', 'ftan'):
            args += near_multiples(rng, max(10, n // 10))
        hist = {}
        worst = 0
        for a in args:
            m.fgen(cmd(2, F.FMT_X, 1, op), ('imm', a, 12))
            m.fcond(0)
            got = m.fpu.fp_bits80(1)
            with mp.workprec(400 + 2 * max(0, ((a >> 80) & 0x7FFF) - 16383)):
                x = as_mpf(((a >> 80) << 64) | (a & ((1 << 64) - 1)))
                if name == 'flognp1' and x <= -1:
                    continue
                want = fn(x)
                # Only results in the normal extended range: an overflow,
                # an underflow or a denormal is the rounding's, not the
                # function's, and an ulp means nothing there.
                if want == 0 or not mpmath.isfinite(want) or \
                        not mpmath.ldexp(1, -16383) <= abs(want) < mpmath.ldexp(1, 16384):
                    continue
                e = int(mpmath.floor(mpmath.log(abs(want), 2)))
                ulps = float(abs(as_mpf(got) - want) / mpmath.ldexp(1, e - 63))
            worst = max(worst, ulps)
            b = 0 if ulps <= 0.5 else 1 if ulps <= 1 else 2 if ulps <= 4 else 3 if ulps <= 64 else 4
            hist[b] = hist.get(b, 0) + 1
        worst_all = max(worst_all, worst)
        h = ' '.join(f'{lab}:{hist.get(i, 0)}' for i, lab in
                     enumerate(['<=0.5', '<=1', '<=4', '<=64', '>64']))
        print(f'  {name:8s} {len(args):5d} args, worst {worst:8.3f} ulp of extended   {h}')
    print(f'worst {worst_all:.3f} ulp of extended; FPU 4.3.2 allows 2048 (one of double)')
    return 0 if worst_all <= 2048 else 1


if __name__ == '__main__':
    sys.exit(main())
