# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Exact numbers and the MC68881 rounding core.

An XN is a value the chip can hold or compute, kept exactly:

    kind 'fin'   sign, mant > 0, exp: the value (-1)^sign * mant * 2^exp
    kind 'zero'  sign
    kind 'inf'   sign
    kind 'nan'   sign, mant = the 64-bit significand, integer bit included

A finite XN is not normalised; mant can be any positive integer, so every
intermediate result of the four operations is exact and rounding happens once,
in round_fin(). Quotients, square roots and transcendentals that are not dyadic
are represented by a truncated mantissa with a final sticky 1 bit appended far
below any rounding position (with_sticky()), which rounds identically to the
real number for every rounding position above it.
"""

from dataclasses import dataclass

# FPCR mode byte fields, FPU 2.2.2 figure 2-3.
RN, RZ, RM, RP = 0, 1, 2, 3
PREC_X, PREC_S, PREC_D = 0, 1, 2


@dataclass(frozen=True)
class XN:
    kind: str
    sign: int = 0
    mant: int = 0
    exp: int = 0

    # -- classification -------------------------------------------------------
    @property
    def is_nan(self):
        return self.kind == 'nan'

    @property
    def is_snan(self):
        # FPU 3.2.5: the MSB of the fraction (bit 62 of the extended
        # significand) clear means signalling.
        return self.kind == 'nan' and not (self.mant >> 62) & 1

    @property
    def is_inf(self):
        return self.kind == 'inf'

    @property
    def is_zero(self):
        return self.kind == 'zero'

    @property
    def is_fin(self):
        return self.kind == 'fin'

    # -- normalised view of a finite value -----------------------------------
    @property
    def E(self):
        """Exponent of the leading bit: the value is in [2^E, 2^(E+1))."""
        assert self.kind == 'fin'
        return self.exp + self.mant.bit_length() - 1

    def neg(self):
        return XN(self.kind, self.sign ^ 1, self.mant, self.exp)

    def abs(self):
        return XN(self.kind, 0, self.mant, self.exp)

    def quieted(self):
        assert self.kind == 'nan'
        return XN('nan', self.sign, self.mant | (1 << 62), 0)

    def frac(self):
        """The exact value as (numerator, denominator), finite or zero only."""
        if self.kind == 'zero':
            return 0, 1
        assert self.kind == 'fin'
        n = -self.mant if self.sign else self.mant
        if self.exp >= 0:
            return n << self.exp, 1
        return n, 1 << -self.exp


def fin(sign, mant, exp):
    """A finite value, or a signed zero if mant is 0.

    Canonical: trailing zero bits are moved into the exponent, so two XNs of
    the same value compare equal.
    """
    if mant == 0:
        return XN('zero', sign)
    assert mant > 0
    tz = (mant & -mant).bit_length() - 1
    return XN('fin', sign, mant >> tz, exp + tz)


def zero(sign=0):
    return XN('zero', sign)


def inf(sign=0):
    return XN('inf', sign)


# FPU 3.2.5: "When NANs are created by the FPCP ... all bits of the mantissa are
# ones". The sign is not stated; FPU 2.1 says reset leaves *positive* NaNs.
DEFAULT_NAN = XN('nan', 0, (1 << 64) - 1, 0)


def from_int(n):
    return fin(1 if n < 0 else 0, abs(n), 0)


def from_frac(num, den, guard_bits=0):
    """An exact rational as an XN: exact if dyadic, otherwise sticky-tagged.

    guard_bits is how many significant bits the truncated quotient keeps
    before the sticky bit; 200 is far more than any rounding position used.
    """
    if num == 0:
        return zero()
    sign = 1 if (num < 0) != (den < 0) else 0
    num, den = abs(num), abs(den)
    # Exact if den is a power of two.
    if den & (den - 1) == 0:
        return fin(sign, num, -(den.bit_length() - 1))
    keep = max(guard_bits, 200)
    shift = keep - (num.bit_length() - den.bit_length()) + 1
    if shift >= 0:
        q, r = divmod(num << shift, den)
    else:
        q, r = divmod(num, den << -shift)
    return with_sticky(sign, q, -shift, r != 0)


def with_sticky(sign, mant, exp, sticky):
    """mant * 2^exp, with a sticky 1 appended below it when sticky is set."""
    if not sticky:
        return fin(sign, mant, exp)
    return fin(sign, (mant << 2) | 1, exp - 2)


# -----------------------------------------------------------------------------
# Rounding precisions, FPU 2.2.2 and 6.1.7. Each is (mantissa bits, minimum
# normal exponent, maximum exponent, LSB exponent of the smallest denormal).
#
# Extended differs from the IEEE/x87 convention: FPU table 3-3 gives the
# extended denormal "bias of e +16383", value 2^-16383 * 0.f, and a normalised
# number may have e = 0 with j = 1. So the smallest normal is 2^-16383 and the
# denormal LSB is 2^(-16383-63). Single and double are the IEEE ones (table 3-1:
# denormal bias 126, so 2^-126 * 0.f).
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class Prec:
    name: str
    bits: int
    emin: int
    emax: int
    denorm_lsb: int


EXT = Prec('X', 64, -16383, 16383, -16383 - 63)
SGL = Prec('S', 24, -126, 127, -126 - 23)
DBL = Prec('D', 53, -1022, 1023, -1022 - 52)
# FSGLMUL/FSGLDIV, FPU 4.5.5.2: single-precision mantissa, extended range.
SGL_XRANGE = Prec('SX', 24, -16383, 16383, -16383 - 63)
# Unbounded 64-bit rounding, for the wrapped exceptional operand (FPU 6.1.4).
EXT_UNBOUNDED = Prec('Xu', 64, -(1 << 40), 1 << 40, -(1 << 41))

PRECS = {PREC_X: EXT, PREC_S: SGL, PREC_D: DBL}


@dataclass
class RoundResult:
    value: XN
    inex: bool
    unfl: bool      # tiny before rounding (FPU 6.1.5, the EXC-byte definition)
    ovfl: bool
    tiny_E: int = 0  # the unrounded exponent, for the exceptional operand


def max_finite(sign, p):
    return fin(sign, (1 << p.bits) - 1, p.emax - p.bits + 1)


def min_denorm(sign, p):
    return fin(sign, 1, p.denorm_lsb)


def round_fin(x, mode, p):
    """FPU figure 6-3 with the underflow/overflow handling of 6.1.4/6.1.5.

    Order (FPU 4.5.5.2): check for underflow on the exact intermediate result,
    denormalise, round at the selected precision, then check for overflow.
    """
    if x.kind != 'fin':
        return RoundResult(x, False, False, False)
    sign, mant, exp = x.sign, x.mant, x.exp
    E = exp + mant.bit_length() - 1
    tiny = E < p.emin
    lsb = max(E - p.bits + 1, p.denorm_lsb)
    if exp >= lsb:
        q, inex = mant << (exp - lsb), False
    else:
        sh = lsb - exp
        q = mant >> sh
        rem = mant & ((1 << sh) - 1)
        half = 1 << (sh - 1)
        inex = rem != 0
        if mode == RN:
            if rem > half or (rem == half and q & 1):
                q += 1
        elif mode == RM:
            if sign and rem:
                q += 1
        elif mode == RP:
            if not sign and rem:
                q += 1
    if q == 0:
        return RoundResult(zero(sign), inex, tiny, False, E)
    Er = lsb + q.bit_length() - 1
    if Er > p.emax:
        # FPU 6.1.4 trap-disabled results.
        if mode == RN:
            v = inf(sign)
        elif mode == RZ:
            v = max_finite(sign, p)
        elif mode == RM:
            v = inf(1) if sign else max_finite(0, p)
        else:
            v = inf(0) if not sign else max_finite(1, p)
        return RoundResult(v, True, tiny, True, E)
    return RoundResult(fin(sign, q, lsb), inex, tiny, False, E)


def round_to_int(x, mode):
    """Round a finite value to an integer value (FINT), returning (XN, inexact)."""
    if x.kind != 'fin':
        return x, False
    if x.exp >= 0:
        return x, False
    sh = -x.exp
    q = x.mant >> sh
    rem = x.mant & ((1 << sh) - 1)
    half = 1 << (sh - 1)
    if mode == RN:
        if rem > half or (rem == half and q & 1):
            q += 1
    elif mode == RM:
        if x.sign and rem:
            q += 1
    elif mode == RP:
        if not x.sign and rem:
            q += 1
    return fin(x.sign, q, 0), rem != 0


def int_value(x, mode):
    """The integer a finite or zero XN rounds to, as a Python int."""
    if x.kind == 'zero':
        return 0, False
    r, inex = round_to_int(x, mode)
    if r.kind == 'zero':
        return 0, inex
    v = r.mant << r.exp
    return (-v if r.sign else v), inex


def compare(a, b):
    """-1, 0, 1 for two non-NaN XNs (zeros equal regardless of sign)."""
    def key(x):
        if x.kind == 'inf':
            return None
        return x.frac()
    if a.kind == 'inf' or b.kind == 'inf':
        va = (1 if not a.sign else -1) if a.kind == 'inf' else 0
        vb = (1 if not b.sign else -1) if b.kind == 'inf' else 0
        if va != vb:
            return -1 if va < vb else 1
        if va != 0:
            return 0
    na, da = key(a)
    nb, db = key(b)
    lhs, rhs = na * db, nb * da
    return (lhs > rhs) - (lhs < rhs)
