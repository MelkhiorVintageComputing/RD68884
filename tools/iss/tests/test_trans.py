# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The transcendental microcode against the golden model, to FPU 4.3.2.

Each case runs through the full dialog on both FPUs, as test_arith does;
the two must agree on everything except:
  - the result (and FSINCOS's cosine), which may differ by one unit in the
    last place of double, or of the selected precision when that is coarser
    (FPU 4.3.2; a single- or double-precision result may round the other way
    near a tie);
  - the exceptional operand in the FSAVE frame, held to the same;
  - INEX2 (and the accrued inexact) where the model's result is exact: the
    manual says the functions do not check for exact cases (FPU 4.3.2).

    TRANS_N=2000 TRANS_SEED=3 python3 -m unittest iss.tests.test_trans
"""

import os
import random
import unittest
from fractions import Fraction

from model import formats as F
from model.cpif import FPU881
from iss.machine import Machine
from iss.tests.test_arith import Run, cmd, fpcr, x_operand

N = int(os.environ.get('TRANS_N', '200'))
SEED = int(os.environ.get('TRANS_SEED', '1'))
ONLY = os.environ.get('TRANS_ONLY')

OPS = {'fsinh': 0x02, 'flognp1': 0x06, 'fetoxm1': 0x08, 'ftanh': 0x09, 'fatan': 0x0A,
       'fasin': 0x0C, 'fatanh': 0x0D, 'fsin': 0x0E, 'ftan': 0x0F, 'fetox': 0x10,
       'ftwotox': 0x11, 'ftentox': 0x12, 'flogn': 0x14, 'flog10': 0x15, 'flog2': 0x16,
       'fcosh': 0x19, 'facos': 0x1C, 'fcos': 0x1D, 'fsincos': 0x30}
# Where each function is interesting: a range of binary exponents.
RANGE = {'fsinh': (-70, 15), 'flognp1': (-70, 30), 'fetoxm1': (-70, 15), 'ftanh': (-70, 7),
         'fatan': (-70, 70), 'fasin': (-70, 0), 'fatanh': (-70, 0), 'fsin': (-70, 100),
         'ftan': (-70, 100), 'fetox': (-70, 15), 'ftwotox': (-70, 15), 'ftentox': (-70, 14),
         'flogn': (-16000, 16000), 'flog10': (-16000, 16000), 'flog2': (-16000, 16000),
         'fcosh': (-70, 15), 'facos': (-70, 0), 'fcos': (-70, 100), 'fsincos': (-70, 100)}


def operand(rng, name):
    k = rng.randrange(12)
    if k == 0:
        return x_operand(rng)                       # anything, specials too
    lo, hi = RANGE[name]
    if k == 1 and name in ('fsin', 'fcos', 'ftan', 'fsincos'):
        lo, hi = 20, 16383                          # Payne and Hanek
    e = rng.randrange(lo, hi + 1)
    s = rng.getrandbits(1)
    m = (1 << 63) | rng.getrandbits(63)
    if k == 2:
        m = 1 << 63                                 # powers of two, and one
        e = rng.choice([e, 0, 1, -1])
    if name in ('flogn', 'flog10', 'flog2') and rng.randrange(3):
        s = 0
    if name in ('fasin', 'facos', 'fatanh', 'flognp1') and k == 3:
        e, m = -1, (1 << 64) - 1 - rng.getrandbits(8)     # just below 1
    return (((s << 15) | ((e + 16383) & 0x7FFF)) << 80) | m


def value(bits80):
    x = F.decode_x(F.reg_from_bits96(((bits80 >> 64) << 80) | (bits80 & ((1 << 64) - 1))))
    if x.kind in ('fin', 'zero'):
        return Fraction(*x.frac()), x.kind
    return None, x.kind


def close(a80, b80, fpcr_v):
    if a80 == b80:
        return True
    va, ka = value(a80)
    vb, kb = value(b80)
    if va is None or vb is None:
        return False
    m = max(abs(va), abs(vb))
    if m == 0:
        return True
    if va != 0 and vb != 0 and (va < 0) != (vb < 0):
        return False
    e = m.numerator.bit_length() - m.denominator.bit_length()
    if Fraction(2) ** e > m:
        e -= 1
    bits = {1: 24, 2: 53}.get((fpcr_v >> 6) & 3, 53)
    tol = Fraction(2) ** (e - bits + 1)
    tol = max(tol, Fraction(2) ** (-16383 - 63))           # the denormal grid
    return abs(va - vb) <= tol


FRAME = 0x8000 - 28                                          # FSAVE -(A7), idle frame


def etemp(mem):
    b = bytes(mem.get(FRAME + 8 + i, 0) for i in range(12))
    v = int.from_bytes(b, 'big')
    return ((v >> 80) << 64) | (v & ((1 << 64) - 1))


def agree(c, sm, si):
    """None if the ISS's state is as good as the model's, else why not."""
    ry = (c['cmd'] >> 7) & 7
    regs = [ry]
    if (c['cmd'] & 0x78) == 0x30:
        regs.append(c['cmd'] & 7)
    for k in ('d', 'a', 'events'):
        if sm[k] != si[k]:
            return k
    for j in range(8):
        if j not in regs and sm['regs'][j] != si['regs'][j]:
            return f'FP{j}'
    for j in regs:
        if not close(sm['regs'][j], si['regs'][j], c['fpcr']):
            return f'FP{j} value'
    fm, fi = sm['ctl'][1], si['ctl'][1]
    if sm['ctl'][0] != si['ctl'][0] or sm['ctl'][2] != si['ctl'][2]:
        return 'FPCR/FPIAR'
    inex = 0x0200 | 0x0008
    if (fm & ~inex & ~0x0F000000) != (fi & ~inex & ~0x0F000000):
        return 'FPSR exceptions'
    if (fm & inex) != (fi & inex) and fm & 0x0200:
        return 'FPSR INEX2 (the model says inexact)'
    if (fm ^ fi) & 0x0F000000:
        # The condition codes follow the value; equal values must agree.
        if all(sm['regs'][j] == si['regs'][j] for j in regs):
            return 'FPSR condition codes'
    mm, mi = sm['mem'], si['mem']
    if set(mm) != set(mi):
        return 'memory'
    for q in mm:
        if mm[q] != mi[q] and not FRAME + 8 <= q < FRAME + 20:
            return 'memory'
    if not close(etemp(mm), etemp(mi), 0):
        return 'ETEMP'
    return None


class Trans(unittest.TestCase):

    def test_random(self):
        rng = random.Random(SEED)
        names = [ONLY] if ONLY else list(OPS)
        bad = []
        model, iss = Run(FPU881(latency=lambda c: 3)), Run(Machine())
        for i in range(N):
            name = rng.choice(names)
            op = OPS[name]
            if name == 'fsincos':
                op |= rng.randrange(8)
            ry = rng.randrange(8)
            c = dict(ry=ry, dst=x_operand(rng), fpcr=fpcr(rng), pre=None)
            src = operand(rng, name)
            if rng.randrange(2):
                c['cmd'] = cmd(2, F.FMT_X, ry, op)
                c['ea'] = ('imm', src, 12)
            else:
                rx = rng.randrange(8)
                c['cmd'] = cmd(0, rx, ry, op)
                c['ea'] = None
                c['pre'] = (rx, src)
                if rx == ry:
                    c['dst'] = src
            if c['pre']:
                for r in (model, iss):
                    r.gen(cmd(2, F.FMT_X, c['pre'][0], 0), ('imm', c['pre'][1], 12))
            sm, si = model.case(c), iss.case(c)
            why = agree(c, sm, si) if sm != si else None
            if why:
                rgs = [(j, sm['regs'][j], si['regs'][j]) for j in range(8)
                       if sm['regs'][j] != si['regs'][j]]
                bad.append(f'{name} cmd ${c["cmd"]:04X} fpcr ${c["fpcr"]:04X} src ${src:024X}: '
                           f'{why}; fpsr model ${sm["ctl"][1]:08X} iss ${si["ctl"][1]:08X}; '
                           + ' '.join(f'FP{j} ${a:020X}/${b:020X}' for j, a, b in rgs))
                if len(bad) >= 12:
                    break
            if sm != si:
                model, iss = Run(FPU881(latency=lambda c: 3)), Run(Machine())
        self.assertFalse(bad, '\n' + '\n'.join(bad))


if __name__ == '__main__':
    unittest.main()
