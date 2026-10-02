# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Packed decimal real, FPU 3.3, table 3-4 and figure 3-11.

The 96-bit image, most significant first:

    95 SM  94 SE  93-92 YY  91-80 EXP2 EXP1 EXP0  79-76 EXP3  75-68 x
    67-64 MANT16 (the integer digit)  63-0 MANT15..MANT0

The reference model converts exactly and rounds once, so it is correctly
rounded. FPU 4.3.3 only promises 0.97 ulp (RN) and 1.47 ulp otherwise, within
double range; the hardware is held to the manual, the model to exactness.
"""

from .xnum import XN, fin, zero, inf, from_frac, round_fin, EXT, RN, RM, RP

INEX = 'inex'


def _digits(v, n):
    """n hex nibbles of v, most significant first."""
    return [(v >> (4 * (n - 1 - i))) & 0xF for i in range(n)]


def decode_p(v):
    """A packed image to an exact XN (or sticky-tagged), before rounding.

    Returns (value, exact_rational_or_None). The caller rounds to extended in
    the current mode and sets INEX1 if inexact (FPU 6.1.8).
    """
    sm = (v >> 95) & 1
    se = (v >> 94) & 1
    yy = (v >> 92) & 3
    exp_field = (v >> 80) & 0xFFF
    mant16 = (v >> 64) & 0xF
    frac = v & ((1 << 64) - 1)
    # Table 3-4: SE and both Y bits set with exponent $FFF is an infinity or NaN.
    if se and yy == 3 and exp_field == 0xFFF:
        if frac == 0:
            return inf(sm)
        # Note 1: the fraction moves bit for bit into the extended significand.
        return XN('nan', sm, frac, 0)
    digits = [mant16] + _digits(frac, 16)
    # Note 2: nondecimal digits are converted "in the same manner as decimal
    # digits", i.e. positionally times ten.
    mant = 0
    for d in digits:
        mant = mant * 10 + d
    if mant == 0:
        # Table 3-4: a zero, whatever its exponent digits (note 2).
        return zero(sm)
    e = 0
    for d in _digits(exp_field, 3):
        e = e * 10 + d
    if se:
        e = -e
    # 17 digits with the point after the first: value = mant * 10^(e - 16).
    p = e - 16
    if p >= 0:
        num, den = mant * 10 ** p, 1
    else:
        num, den = mant, 10 ** -p
    if sm:
        num = -num
    return from_frac(num, den)


def ilog10(x):
    """floor(log10(|x|)) for a finite XN, exactly."""
    num, den = x.frac()
    num = abs(num)

    def at_least(k):            # |x| >= 10^k
        return num >= den * 10 ** k if k >= 0 else num * 10 ** -k >= den

    # Estimate from the bit lengths, then step to the exact answer.
    k = int((num.bit_length() - den.bit_length()) * 0.30102999566398120)
    while not at_least(k):
        k -= 1
    while at_least(k + 1):
        k += 1
    return k


def _round_div(num, den, mode, negative):
    """Round num/den (both positive) to an integer in the given mode."""
    q, r = divmod(num, den)
    if r:
        if mode == RN:
            if 2 * r > den or (2 * r == den and q & 1):
                q += 1
        elif mode == RM:
            if negative:
                q += 1
        elif mode == RP:
            if not negative:
                q += 1
    return q, r != 0


def encode_p(x, k, mode):
    """FMOVE.P out, FPU 4.6 FMOVE (register-to-memory) and 6.1.3.

    Returns (image, flags) with flags a set of 'operr', 'inex2'.
    k is the 7-bit two's-complement k-factor already sign-extended.
    """
    flags = set()
    if x.kind == 'inf':
        return ((x.sign << 95) | (1 << 94) | (3 << 92) | (0xFFF << 80)), flags
    if x.kind == 'nan':
        return ((x.sign << 95) | (1 << 94) | (3 << 92) | (0xFFF << 80)
                | (x.mant & ((1 << 64) - 1))), flags
    if x.kind == 'zero':
        return x.sign << 95, flags
    # FPU 4.6: "+18 to +63 -- sets the OPERR bit ... treated as +17".
    if k > 17:
        flags.add('operr')
        k = 17
    ilog = ilog10(x)
    if k <= 0:
        length = ilog + 1 - k
    else:
        length = k
    # MODEL CHOICE: at least one and at most seventeen digits (the string has
    # seventeen; the manual does not say what k <= 0 gives for a value with
    # fewer than one digit left of the requested decimal place).
    length = max(1, min(17, length))
    num, den = x.frac()
    num = abs(num)
    while True:
        scale = ilog + 1 - length         # value = D * 10^scale
        if scale >= 0:
            q, inexact = _round_div(num, den * 10 ** scale, mode, x.sign)
        else:
            q, inexact = _round_div(num * 10 ** -scale, den, mode, x.sign)
        if q >= 10 ** length:
            # Rounding carried into a new digit (9.99 -> 10.0).
            ilog += 1
            if k <= 0 and length < 17:
                length += 1
            continue
        break
    if inexact:
        flags.add('inex2')
    digits = [int(c) for c in str(q).rjust(length, '0')]
    digits += [0] * (17 - length)
    exp = ilog
    if abs(exp) > 999:
        # FPU 6.1.3: four exponent digits, the fourth in EXP3.
        flags.add('operr')
    ed = abs(exp)
    exp3, exp2, exp1, exp0 = (ed // 1000) % 10, (ed // 100) % 10, (ed // 10) % 10, ed % 10
    v = (x.sign << 95) | ((1 if exp < 0 else 0) << 94)
    v |= (exp2 << 88) | (exp1 << 84) | (exp0 << 80) | (exp3 << 76)
    v |= digits[0] << 64
    for i, d in enumerate(digits[1:]):
        v |= d << (4 * (15 - i))
    return v, flags


def round_p_input(x, mode):
    """Round a decoded packed value to extended, FPU 6.1.8: extended precision
    regardless of the precision bits, in the current rounding mode."""
    r = round_fin(x, mode, EXT)
    return r.value, r.inex
