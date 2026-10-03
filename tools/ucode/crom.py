# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The constant ROM: the FMOVECR constants and the powers of ten.

Every entry is a value in the working format (doc/microcode.md) -- sign,
unbiased exponent, a 72-bit mantissa with the integer bit at 71 -- TRUNCATED
to 72 bits, and a sticky bit that says whether anything was cut off. Rounding
such an entry at 64 bits or fewer (FMOVECR, any precision) is the correct
rounding of the true constant.

Layout (the microcode reads it at IMM + an offset, doc/microcode.md):

    CR_MOVECR  $000   FMOVECR's 128 offsets (FPU 4.6 FMOVECR, the offset list);
                      the reserved ones are +0.0 (doc/model.md)
    CR_P10     $080   10^r,        r = 0..63
    CR_N10     $0C0   10^-r,       r = 0..63
    CR_P10H    $100   10^(64 j),   j = 0..79
    CR_N10H    $180   10^-(64 j),  j = 0..79

    T_BASE     $200   the transcendentals' tables, constants and coefficients,
                      by name in TRANS (tools/ucode/trans_ucode.py)
    CR_2OPI    $600   2/pi in raw 64-bit chunks for Payne-Hanek reduction:
                      entry 0 is 0 (the integer part), entry 1 + j the bits
                      64 j + 1 .. 64 j + 64 after the point, in mantissa bits
                      63-0 (not normalised)

The large powers serve the packed decimal conversions, whose bound is FPU
4.3.3's; 10^-r and 10^(+-64 j) are inexact, 10^r is exact for r <= 31.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'third_party', 'mpmath'))
import mpmath  # noqa: E402

CR_MOVECR, CR_P10, CR_N10, CR_P10H, CR_N10H = 0x000, 0x080, 0x0C0, 0x100, 0x180
T_BASE, CR_2OPI = 0x200, 0x600
N_2OPI = 262             # chunks: covers bit 64 x 262 = 16768 > 16383 + 4 x 64
DEPTH = 2048
EXP_ZERO = -16383
WIDTH = 92               # sticky, sign, exponent[17:0], mantissa[71:0]


def _rational(num, den):
    """num/den > 0 as (sticky, exp, mant72), truncated."""
    e = num.bit_length() - den.bit_length()
    if (num << max(0, -e)) < (den << max(0, e)):
        e -= 1
    sh = 71 - e
    if sh >= 0:
        m, r = divmod(num << sh, den)
    else:
        m, r = divmod(num, den << -sh)
    assert m >> 71 == 1, (num, den)
    return int(r != 0), e, m


def _irrational(f):
    with mpmath.workprec(400):
        v = f()
        e = int(mpmath.floor(mpmath.log(v, 2)))
        m = int(mpmath.floor(v * mpmath.mpf(2) ** (71 - e)))
        if m >> 72:
            e += 1
            m = int(mpmath.floor(v * mpmath.mpf(2) ** (71 - e)))
        if not m >> 71:
            e -= 1
            m = int(mpmath.floor(v * mpmath.mpf(2) ** (71 - e)))
        assert m >> 71 == 1
        return 1, e, m          # irrational: never exact


ZERO = (0, 0, EXP_ZERO, 0)


def _mp(v, bits=None):
    """An mpf (or int / Fraction-like) value as (sticky, sign, exp, mant72),
    truncated; with bits, truncated to that many significant bits instead
    (the result then exact, sticky 0)."""
    with mpmath.workprec(800):
        v = mpmath.mpf(v)
        if v == 0:
            return (0, 0, EXP_ZERO, 0)
        sign = int(v < 0)
        v = abs(v)
        e = int(mpmath.floor(mpmath.log(v, 2)))
        for _ in range(3):
            m = int(mpmath.floor(v * mpmath.mpf(2) ** (71 - e)))
            if m >> 72:
                e += 1
            elif not m >> 71:
                e -= 1
            else:
                break
        assert m >> 71 == 1
        if bits is not None:
            m = m >> (72 - bits) << (72 - bits)
            return (0, sign, e, m)
        exact = v * mpmath.mpf(2) ** (71 - e) == m
        return (int(not exact), sign, e, m)


def _value(entry):
    s, sign, e, m = entry
    with mpmath.workprec(800):
        v = mpmath.mpf(m) * mpmath.mpf(2) ** (e - 71)
        return -v if sign else v


def trans_table():
    """[(name, entry)]: the transcendentals' constants, in order from T_BASE.
    Split constants (NAME_1 + NAME_2) give a product with a small integer
    exactly in the first part (doc/microcode.md)."""
    mp = mpmath.mp
    out = []
    with mpmath.workprec(800):
        def put(name, v, bits=None):
            out.append((name, _mp(v, bits)))

        def split(name, v, bits1):
            a = _mp(v, bits1)
            out.append((name + '_1', a))
            out.append((name + '_2', _mp(v - _value(a))))

        ln2, ln10 = mpmath.ln(2), mpmath.ln(10)
        put('ONE', 1)
        put('TWO', 2)
        put('C64_LN2', 64 / ln2)
        split('LN2_64', ln2 / 64, 48)
        put('LN2', ln2)
        put('C64_LOG2_10', 64 * mpmath.log(10, 2))
        split('LOG10_2_64', mpmath.log10(2) / 64, 48)
        put('LN10', ln10)
        split('LN2K', ln2, 56)
        split('LOG10_2K', mpmath.log10(2), 56)
        put('LOG2E', 1 / ln2)
        put('LOG10E', 1 / ln10)
        put('PI', mp.pi)
        put('PI_2', mp.pi / 2)
        put('C2_PI', 2 / mp.pi)
        put('PI_4', mp.pi / 4)
        # pi/2 in three parts for |x| < 2^20 (Cody and Waite): N x part 1
        # and N x part 2 exact for N < 2^20.
        a = _mp(mp.pi / 2, 52)
        b = _mp(mp.pi / 2 - _value(a), 52)
        c = _mp(mp.pi / 2 - _value(a) - _value(b))
        out += [('PIO2_1', a), ('PIO2_2', b), ('PIO2_3', c)]
        for j in range(64):
            put(f'EXP2T{j}', mpmath.mpf(2) ** (mpmath.mpf(j) / 64))
        for j in range(65):
            put(f'LNF{j}', mpmath.ln(1 + mpmath.mpf(j) / 64))
        for j in range(17):
            put(f'ATANT{j}', mpmath.atan(mpmath.mpf(j) / 16))
        for k in range(17):
            put(f'FACT{k}', 1 / mpmath.factorial(k))                    # 1/k!
        for k in range(1, 12):
            put(f'LNC{k}', mpmath.mpf((-1) ** (k + 1)) / k)              # ln(1+u)
        for k in range(9):
            put(f'ODD{k}', mpmath.mpf(1) / (2 * k + 1))                  # atanh
            put(f'ATANC{k}', mpmath.mpf((-1) ** k) / (2 * k + 1))        # atan
        for k in range(12):
            put(f'SINC{k}', mpmath.mpf((-1) ** k) / mpmath.factorial(2 * k + 1))
            put(f'COSC{k}', mpmath.mpf((-1) ** k) / mpmath.factorial(2 * k))
    return out


TRANS = {}


def entries():
    t = [ZERO] * DEPTH

    def put(addr, v):
        s, e, m = v
        t[addr] = (s, 0, e, m)

    mp = mpmath.mp
    for off, f in ((0x00, lambda: mp.pi), (0x0B, lambda: mpmath.log10(2)),
                   (0x0C, lambda: mp.e), (0x0D, lambda: mpmath.log(mp.e, 2)),
                   (0x0E, lambda: mpmath.log10(mp.e)), (0x30, lambda: mpmath.ln(2)),
                   (0x31, lambda: mpmath.ln(10))):
        put(CR_MOVECR + off, _irrational(f))
    for i, n in enumerate([0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]):
        put(CR_MOVECR + 0x32 + i, _rational(10 ** n, 1))
    for r in range(64):
        put(CR_P10 + r, _rational(10 ** r, 1))
        put(CR_N10 + r, _rational(1, 10 ** r))
    for j in range(80):
        put(CR_P10H + j, _rational(10 ** (64 * j), 1))
        put(CR_N10H + j, _rational(1, 10 ** (64 * j)))
    for i, (name, e) in enumerate(trans_table()):
        assert T_BASE + i < CR_2OPI, 'transcendental table overflow'
        t[T_BASE + i] = e
        TRANS[name] = T_BASE + i
    with mpmath.workprec(17200):
        g = int(mpmath.floor(2 / mpmath.mp.pi * mpmath.mpf(2) ** (64 * N_2OPI)))
    for j in range(N_2OPI):
        t[CR_2OPI + 1 + j] = (0, 0, 0, (g >> (64 * (N_2OPI - 1 - j))) & ((1 << 64) - 1))
    assert CR_2OPI + 1 + N_2OPI <= DEPTH
    return t


def addr(name):
    if not TRANS:
        entries()
    return TRANS[name]


def word(entry):
    s, sign, e, m = entry
    return (s << 91) | (sign << 90) | ((e & 0x3FFFF) << 72) | m
