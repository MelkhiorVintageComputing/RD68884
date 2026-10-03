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

The large powers serve the packed decimal conversions, whose bound is FPU
4.3.3's; 10^-r and 10^(+-64 j) are inexact, 10^r is exact for r <= 31.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'third_party', 'mpmath'))
import mpmath  # noqa: E402

CR_MOVECR, CR_P10, CR_N10, CR_P10H, CR_N10H = 0x000, 0x080, 0x0C0, 0x100, 0x180
DEPTH = 512
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
    return t


def word(entry):
    s, sign, e, m = entry
    return (s << 91) | (sign << 90) | ((e & 0x3FFFF) << 72) | m
