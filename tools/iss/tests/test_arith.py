# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The arithmetic microcode against the golden model, case by case.

Every case runs through the full dialog on both FPUs (tools/model/cpif.FPU881
and iss.Machine), driven by the MC68020 driver:

    FMOVE.L #0,FPCR; FMOVE.X #dst,FPn; FMOVE.L #fpcr,FPCR; FMOVE.L #0,FPSR
    <the operation>
    FSAVE -(A7)                 the idle frame shows ETEMP, and drops a pending trap

after which the registers, FPCR/FPSR, the frame, the main processor's
registers, memory and the exceptions it took must be identical.

The operands are random, drawn towards the cases that matter: the specials,
the ends of each precision's range, and mantissas at rounding boundaries.

    ARITH_N=20000 ARITH_SEED=7 python3 -m unittest iss.tests.test_arith
"""

import os
import random
import unittest

from fractions import Fraction

from model import cpif as C, formats as F, packed as P
from model.cpif import FPU881
from model.mpu import MPU, CPException
from iss.machine import Machine

N = int(os.environ.get('ARITH_N', '300'))
SEED = int(os.environ.get('ARITH_SEED', '1'))

OPS_RR = [0x00, 0x01, 0x03, 0x04, 0x18, 0x1A, 0x1E, 0x1F, 0x20, 0x22, 0x23, 0x24,
          0x27, 0x28, 0x38, 0x3A, 0x05, 0x1B, 0x2B, 0x39, 0x3D, 0x21, 0x25, 0x26]
OUT_FMTS = [F.FMT_L, F.FMT_S, F.FMT_X, F.FMT_W, F.FMT_D, F.FMT_B]
IN_FMTS = OUT_FMTS


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


# ----------------------------------------------------------------- operands
# Interesting biased exponents: the specials' and each precision's limits,
# with the rounding and denormalisation neighbourhood around them.
_EXPS = [0, 1, 2, 0x3FFF, 0x3FFE, 0x4000, 0x7FFE, 0x7FFD,
         0x3FFF - 126, 0x3FFF - 127, 0x3FFF - 149, 0x3FFF - 150, 0x3FFF - 151,
         0x3FFF + 127, 0x3FFF + 128, 0x3FFF - 1022, 0x3FFF - 1023, 0x3FFF - 1074,
         0x3FFF - 1075, 0x3FFF + 1023, 0x3FFF + 1024, 0x3FFF + 31, 0x3FFF + 30,
         0x3FFF + 15, 0x3FFF + 7, 0x3FFF + 62, 0x3FFF + 63, 0x3FFF + 64, 0x3FFF - 63,
         0x3FFF - 64]


def _mant(rng):
    k = rng.randrange(9)
    if k == 0:
        return 1 << 63
    if k == 1:
        return (1 << 64) - 1
    if k == 2:              # a boundary at single, double or extended precision
        lsb = rng.choice([40, 11, 0])
        m = (1 << 63) | (rng.getrandbits(63) >> (lsb + 1) << (lsb + 1))
        return m | rng.choice([0, 1 << lsb >> 1 if lsb else 0, (1 << lsb) - 1 if lsb else 0])
    if k == 3:
        return (1 << 63) | rng.getrandbits(rng.randrange(1, 12))
    return (1 << 63) | rng.getrandbits(63)


def x_operand(rng):
    """A 96-bit extended image."""
    s = rng.getrandbits(1)
    k = rng.randrange(20)
    if k == 0:
        e, m = 0, 0                                         # zero
    elif k == 1:
        e, m = 0x7FFF, 0                                    # infinity
    elif k == 2:
        e, m = 0x7FFF, (1 << 63) | rng.getrandbits(63) | 1  # NaN
    elif k == 3:
        e, m = 0x7FFF, rng.getrandbits(62) | 1              # signalling NaN
    elif k == 4:
        e, m = 0, rng.getrandbits(63) >> rng.randrange(63)  # denormal
    elif k == 5:
        e, m = rng.choice(_EXPS) or 1, rng.getrandbits(63) >> rng.randrange(64)  # unnormal
    elif k < 12:
        e, m = rng.choice(_EXPS), _mant(rng)
    else:
        e, m = 0x3FFF + rng.randrange(-80, 80), _mant(rng)
    if e == 0 and m >> 63:
        m &= (1 << 63) - 1                                  # a denormal has J=0
    return (((s << 15) | e) << 80) | m


def near(rng, x):
    """An operand close to x: cancellation, alignment by a bit or two, ties."""
    s, e, m = x >> 95, (x >> 80) & 0x7FFF, x & ((1 << 64) - 1)
    if e in (0, 0x7FFF) or not m >> 63:
        return x_operand(rng)
    e = min(max(e + rng.randrange(-2, 3), 1), 0x7FFE)
    m ^= rng.getrandbits(rng.randrange(1, 64))
    m |= 1 << 63
    s ^= rng.getrandbits(1)
    return (((s << 15) | e) << 80) | m


def packed_operand(rng):
    """A 96-bit packed image (FPU figure 3-11), towards what matters: the
    exact scales (|exponent - 16| <= 27), the whole range, the specials."""
    sm, se = rng.getrandbits(1), rng.getrandbits(1)
    k = rng.randrange(12)
    if k == 0:                                           # infinity or NaN
        frac = 0 if rng.getrandbits(1) else rng.getrandbits(64)
        return (sm << 95) | (1 << 94) | (3 << 92) | (0xFFF << 80) | frac
    if k == 1:
        e = rng.randrange(1000)
        digits = [0] * 17                                # a zero
    else:
        e = rng.choice([rng.randrange(1000), rng.randrange(40), rng.randrange(16)])
        n = rng.choice([17, 17, rng.randrange(1, 18)])
        digits = [rng.randrange(10) for _ in range(n)] + [0] * (17 - n)
        if rng.randrange(20) == 0:
            digits[rng.randrange(17)] = rng.randrange(10, 16)   # note 2
    ed = [(e // 100) % 10, (e // 10) % 10, e % 10]
    v = (sm << 95) | (se << 94) | (rng.getrandbits(2) << 92 if rng.randrange(8) == 0 else 0)
    v |= (ed[0] << 88) | (ed[1] << 84) | (ed[2] << 80)
    v |= digits[0] << 64
    for i, d in enumerate(digits[1:]):
        v |= d << (4 * (15 - i))
    return v


def ieee_operand(rng, ebits, fbits):
    s = rng.getrandbits(1)
    emax = (1 << ebits) - 1
    k = rng.randrange(10)
    if k == 0:
        e, f = 0, 0
    elif k == 1:
        e, f = emax, 0
    elif k == 2:
        e, f = emax, rng.getrandbits(fbits) | 1
    elif k == 3:
        e, f = 0, rng.getrandbits(fbits) >> rng.randrange(fbits)
    elif k == 4:
        e, f = rng.choice([1, 2, emax - 1, emax >> 1]), rng.getrandbits(fbits)
    else:
        e, f = rng.randrange(1, emax), rng.getrandbits(fbits)
    return (((s << ebits) | e) << fbits) | f


def int_operand(rng, bits):
    k = rng.randrange(5)
    if k == 0:
        return rng.choice([0, 1, (1 << bits) - 1, 1 << (bits - 1), (1 << (bits - 1)) - 1])
    return rng.getrandbits(rng.randrange(1, bits + 1))


def source(rng, fmt):
    """(image as the MPU transfers it, number of bytes)."""
    if fmt == F.FMT_X:
        return x_operand(rng), 12
    if fmt == F.FMT_S:
        return ieee_operand(rng, 8, 23), 4
    if fmt == F.FMT_D:
        return ieee_operand(rng, 11, 52), 8
    if fmt == F.FMT_P:
        return packed_operand(rng), 12
    bits = F.INT_BITS[fmt]
    return int_operand(rng, bits), bits // 8


def fpcr(rng):
    enable = rng.choice([0, 0, 0, 0xFF, rng.getrandbits(8)])
    return (enable << 8) | (rng.randrange(4) << 6) | (rng.randrange(4) << 4)


# ----------------------------------------------------------------- the runs
class Run:
    def __init__(self, fpu):
        self.fpu = fpu
        self.m = MPU(fpu)
        self.m.a[7] = 0x8000
        self.events = []

    def gen(self, c, ea=None):
        try:
            self.m.fgen(c, ea)
            self.events.append(('ok', c))
        except CPException as e:
            self.events.append((e.kind, e.vector, c))

    def case(self, c):
        self.events = []
        self.m.a[7] = 0x8000
        self.m.a[0] = 0x4000
        self.m.mem.b.clear()
        self.gen(cmd(4, 4, 0, 0), ('imm', 0, 4))
        self.gen(cmd(2, F.FMT_X, c['ry'], 0), ('imm', c['dst'], 12))
        self.gen(cmd(4, 4, 0, 0), ('imm', c['fpcr'], 4))
        self.gen(cmd(4, 2, 0, 0), ('imm', 0, 4))
        if 'kreg' in c:
            self.m.d[(c['cmd'] >> 4) & 7] = c['kreg']
        self.gen(c['cmd'], c['ea'])
        try:
            self.events.append(('save', self.m.fsave(('predec', 7))))
        except CPException as e:
            self.events.append((e.kind, e.vector))
        self.fpu.tick(50)
        f = self.fpu
        if isinstance(f, FPU881):
            regs = [f.st.fp[i].bits80() for i in range(8)]
            ctl = (f.st.fpcr, f.st.fpsr, f.st.fpiar)
        else:
            regs = [f.fp_bits80(i) for i in range(8)]
            ctl = (f.fpcr, f.fpsr, f.fpiar)
        return dict(regs=regs, ctl=ctl, d=list(self.m.d), a=list(self.m.a),
                    mem=dict(self.m.mem.b), events=self.events)


def cases(rng, n):
    for i in range(n):
        ry = rng.randrange(8)
        k = rng.randrange(10)
        c = dict(ry=ry, dst=x_operand(rng), fpcr=fpcr(rng))
        if k < 4:                                  # FPm to FPn
            rx = rng.randrange(8)
            op = rng.choice(OPS_RR)
            # FPm is FPn when rx == ry; else load FPm first through the source
            c['cmd'] = cmd(0, rx, ry, op)
            c['ea'] = None
            pre = near(rng, c['dst']) if rng.randrange(3) == 0 else x_operand(rng)
            c['pre'] = (rx, pre) if rx != ry else None
        elif k < 8:                                # <ea> to FPn
            fmt = rng.choice(IN_FMTS + [F.FMT_P, 7])
            c['pre'] = None
            if fmt == 7:                           # FMOVECR
                c['cmd'] = cmd(2, 7, ry, rng.choice([rng.randrange(128), 0, 0x0B, 0x0C,
                                                     0x0D, 0x0E, 0x0F, 0x30, 0x31]
                                                    + list(range(0x32, 0x40))))
                c['ea'] = None
                yield i, c
                continue
            img, nb = source(rng, fmt)
            # Packed sources only through FMOVE: the conversion is held to
            # FPU 4.3.3's bound, not to the bit, and an operation after it
            # would carry a last-place difference anywhere.
            op = 0 if fmt == F.FMT_P else rng.choice(OPS_RR)
            c['cmd'] = cmd(2, fmt, ry, op)
            c['ea'] = ('imm', img, nb)
        else:                                      # FPn to <ea>
            fmt = rng.choice(OUT_FMTS + [F.FMT_P, F.FMT_PDYN])
            kf = rng.choice([rng.randrange(-20, 21), rng.randrange(-64, 64), 17, 0, 1])
            c['cmd'] = cmd(3, fmt, ry, (kf & 0x7F) if fmt == F.FMT_P else
                           (rng.randrange(8) << 4) if fmt == F.FMT_PDYN else 0)
            c['kreg'] = kf & 0xFFFFFFFF
            c['ea'] = ('ind', 0)
            c['pre'] = None
        yield i, c


# ----------------------------------------------------- packed tolerance
def _x_frac(bits80):
    r = F.reg_from_bits96(((bits80 >> 64) << 80) | (bits80 & ((1 << 64) - 1)))
    x = F.decode_x(r)
    return x.frac() if x.kind in ('fin', 'zero') else None


def _adjacent(a80, b80, fpcr_v):
    """Two register images one unit apart in the last place of the
    precision (FPCR bits 7-6): the most two correct conversions can differ."""
    fa, fb = _x_frac(a80), _x_frac(b80)
    if fa is None or fb is None:
        return False
    va, vb = Fraction(*fa), Fraction(*fb)
    if va == 0 or vb == 0 or (va < 0) != (vb < 0):
        return False
    bits = {1: 24, 2: 53}.get((fpcr_v >> 6) & 3, 64)
    m = max(abs(va), abs(vb))
    e = m.numerator.bit_length() - m.denominator.bit_length()
    if Fraction(2) ** e > m:
        e -= 1
    return abs(va - vb) <= Fraction(2) ** (e - bits + 1)


def _packed_value(img):
    v = P.decode_p(img)
    if v.kind == 'zero':
        return Fraction(0)
    if v.kind != 'fin':
        return None
    return Fraction(*v.frac())


def _last_digit(img):
    """The unit of the last of the seventeen digits of a packed image."""
    e = ((img >> 88) & 0xF) * 100 + ((img >> 84) & 0xF) * 10 + ((img >> 80) & 0xF)
    if (img >> 94) & 1:
        e = -e
    return Fraction(10) ** (e - 16)


def packed_close(c, sm, si):
    """A packed conversion where the ISS and the model differ only in the
    last place, as FPU 4.3.3 allows: every other part of the state equal."""
    opclass, fmt = (c['cmd'] >> 13) & 7, (c['cmd'] >> 10) & 7
    if fmt not in (F.FMT_P, F.FMT_PDYN) or opclass not in (2, 3):
        return False
    diff = [k for k in sm if sm[k] != si[k]]
    if opclass == 2:
        if diff != ['regs']:
            return False
        ry = (c['cmd'] >> 7) & 7
        if any(a != b for j, (a, b) in enumerate(zip(sm['regs'], si['regs'])) if j != ry):
            return False
        return _adjacent(sm['regs'][ry], si['regs'][ry], c['fpcr'])
    if diff != ['mem']:
        return False
    a, b = sm['mem'], si['mem']
    if set(a) != set(b):
        return False
    base = 0x4000
    if any(a[q] != b[q] for q in a if not base <= q < base + 12):
        return False
    ia = int.from_bytes(bytes(a[base + i] for i in range(12)), 'big')
    ib = int.from_bytes(bytes(b[base + i] for i in range(12)), 'big')
    va, vb = _packed_value(ia), _packed_value(ib)
    if va is None or vb is None:
        return False
    return abs(va - vb) <= max(_last_digit(ia), _last_digit(ib))


class Arith(unittest.TestCase):

    def test_random(self):
        rng = random.Random(SEED)
        model, iss = Run(FPU881(latency=lambda c: 3)), Run(Machine())
        bad = []
        for i, c in cases(rng, N):
            if c['pre']:
                for r in (model, iss):
                    r.gen(cmd(2, F.FMT_X, c['pre'][0], 0), ('imm', c['pre'][1], 12))
            sm, si = model.case(c), iss.case(c)
            if sm != si and packed_close(c, sm, si):
                # within the bound; start both again so it does not linger
                model, iss = Run(FPU881(latency=lambda c: 3)), Run(Machine())
                continue
            if sm != si:
                diff = {k: (sm[k], si[k]) for k in sm if sm[k] != si[k]}
                bad.append((i, c, diff))
                if len(bad) >= 5:
                    break
                # start both again, so one difference does not cascade
                model, iss = Run(FPU881(latency=lambda c: 3)), Run(Machine())
        msg = []
        for i, c, diff in bad:
            msg.append(f'case {i}: cmd ${c["cmd"]:04X} fpcr ${c["fpcr"]:04X} '
                       f'dst ${c["dst"]:024X} ea {c["ea"]} pre {c["pre"]}')
            for k, (a, b) in diff.items():
                if k == 'regs':
                    for j, (x, y) in enumerate(zip(a, b)):
                        if x != y:
                            msg.append(f'   FP{j}: model ${x:020X} iss ${y:020X}')
                elif k == 'mem':
                    ks = sorted(set(a) | set(b))
                    msg.append('   mem: ' + ' '.join(f'{q:X}:{a.get(q)}/{b.get(q)}'
                                                     for q in ks if a.get(q) != b.get(q)))
                elif k == 'ctl':
                    msg.append(f'   ctl: model {[hex(v) for v in a]} iss {[hex(v) for v in b]}')
                else:
                    msg.append(f'   {k}: model {a} iss {b}')
        self.assertFalse(bad, '\n' + '\n'.join(msg))


if __name__ == '__main__':
    unittest.main()
