# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The MC68881 data formats, FPU section 3, to and from bits.

Memory images are Python ints, most significant byte first as on the bus:
B 8 bits, W 16, L 32, S 32, D 64, X and P 96. A floating-point register is held
as its raw 80-bit image, Reg(sign, e, m), so that FMOVEM moves it bit for bit
(FPU 4.6 FMOVEM: "No conversion or rounding is performed").
"""

from dataclasses import dataclass

from .xnum import XN, fin, zero, inf, DEFAULT_NAN

# Source/destination format field, FPU tables 4-14 and 4-15.
FMT_L, FMT_S, FMT_X, FMT_P, FMT_W, FMT_D, FMT_B, FMT_PDYN = range(8)
FMT_NAME = {FMT_L: 'L', FMT_S: 'S', FMT_X: 'X', FMT_P: 'P',
            FMT_W: 'W', FMT_D: 'D', FMT_B: 'B', FMT_PDYN: 'P'}
FMT_BYTES = {FMT_L: 4, FMT_S: 4, FMT_X: 12, FMT_P: 12,
             FMT_W: 2, FMT_D: 8, FMT_B: 1, FMT_PDYN: 12}


@dataclass(frozen=True)
class Reg:
    """A floating-point data register: sign, 15-bit biased exponent, 64-bit
    significand with the explicit integer bit (FPU 2.1, table 3-3)."""
    sign: int
    e: int
    m: int

    def bits80(self):
        return (self.sign << 79) | (self.e << 64) | self.m

    def bits96(self):
        # FPU table 3-3: 16 reserved bits between exponent and significand,
        # written as zeros.
        return (self.sign << 95) | (self.e << 80) | self.m


RESET_REG = Reg(0, 0x7FFF, (1 << 64) - 1)   # FPU 2.1: positive nonsignalling NaN


def reg_from_bits96(v):
    return Reg((v >> 95) & 1, (v >> 80) & 0x7FFF, v & ((1 << 64) - 1))


# -----------------------------------------------------------------------------
# Extended
# -----------------------------------------------------------------------------
def decode_x(r):
    """A register or X memory image to its value.

    FPU table 3-3. Exponent $7FFF: infinity if the fraction (bits 62-0) is zero,
    the integer bit being a don't-care, otherwise a NaN. Any other exponent with
    a zero significand is zero (FPU 3.5.1: an unnormalised zero becomes a
    normalised zero). Everything else is m * 2^(e - 16383 - 63), which covers
    normals, denormals (e = 0, value 2^-16383 * 0.f) and unnormals alike.
    """
    if r.e == 0x7FFF:
        if r.m & ((1 << 63) - 1) == 0:
            return inf(r.sign)
        return XN('nan', r.sign, r.m, 0)
    if r.m == 0:
        return zero(r.sign)
    return fin(r.sign, r.m, r.e - 16383 - 63)


def encode_x(x):
    """A value that is representable in extended to its canonical image."""
    if x.kind == 'zero':
        return Reg(x.sign, 0, 0)
    if x.kind == 'inf':
        return Reg(x.sign, 0x7FFF, 0)
    if x.kind == 'nan':
        return Reg(x.sign, 0x7FFF, x.mant)
    E = x.E
    if E >= -16383:
        be = E + 16383
        assert be <= 0x7FFE, 'not representable: overflow'
        sh = 63 - (x.mant.bit_length() - 1)
        assert sh >= 0 or x.mant & ((1 << -sh) - 1) == 0, 'not exact'
        m = x.mant << sh if sh >= 0 else x.mant >> -sh
        return Reg(x.sign, be, m)
    sh = x.exp - (-16383 - 63)
    assert sh >= 0, 'not representable: below the smallest denormal'
    return Reg(x.sign, 0, x.mant << sh)


# -----------------------------------------------------------------------------
# Single and double, FPU tables 3-1 and 3-2
# -----------------------------------------------------------------------------
def _decode_ieee(v, ebits, fbits):
    sign = (v >> (ebits + fbits)) & 1
    e = (v >> fbits) & ((1 << ebits) - 1)
    f = v & ((1 << fbits) - 1)
    bias = (1 << (ebits - 1)) - 1
    if e == (1 << ebits) - 1:
        if f == 0:
            return inf(sign)
        # FPU 4.6 FMOVE / 3.2.5: the fraction is left-justified into the
        # 63-bit extended fraction, the integer bit set, so the SNaN bit
        # (fraction MSB) lands on bit 62.
        return XN('nan', sign, (1 << 63) | (f << (63 - fbits)), 0)
    if e == 0:
        return fin(sign, f, 1 - bias - fbits) if f else zero(sign)
    return fin(sign, (1 << fbits) | f, e - bias - fbits)


def _encode_ieee(x, ebits, fbits):
    """A value already rounded to the format's precision and range."""
    bias = (1 << (ebits - 1)) - 1
    emax_field = (1 << ebits) - 1
    if x.kind == 'zero':
        return x.sign << (ebits + fbits)
    if x.kind == 'inf':
        return (x.sign << (ebits + fbits)) | (emax_field << fbits)
    if x.kind == 'nan':
        # Truncated to the format's fraction, FPU 6.1.2.
        f = (x.mant >> (63 - fbits)) & ((1 << fbits) - 1)
        return (x.sign << (ebits + fbits)) | (emax_field << fbits) | f
    E = x.E
    if E >= 1 - bias:
        sh = fbits - (x.mant.bit_length() - 1)
        assert sh >= 0 or x.mant & ((1 << -sh) - 1) == 0, 'not exact'
        m = x.mant << sh if sh >= 0 else x.mant >> -sh
        be = E + bias
        assert 0 < be < emax_field
        return (x.sign << (ebits + fbits)) | (be << fbits) | (m & ((1 << fbits) - 1))
    tz = (x.mant & -x.mant).bit_length() - 1     # drop trailing zeros first
    mant, exp = x.mant >> tz, x.exp + tz
    sh = exp - (1 - bias - fbits)
    assert sh >= 0, 'not representable: below the smallest denormal'
    return (x.sign << (ebits + fbits)) | (mant << sh)


def decode_s(v):
    return _decode_ieee(v, 8, 23)


def decode_d(v):
    return _decode_ieee(v, 11, 52)


def encode_s(x):
    return _encode_ieee(x, 8, 23)


def encode_d(x):
    return _encode_ieee(x, 11, 52)


# -----------------------------------------------------------------------------
# Integers, FPU 3.1
# -----------------------------------------------------------------------------
INT_BITS = {FMT_B: 8, FMT_W: 16, FMT_L: 32}


def decode_int(v, bits):
    if v >> (bits - 1) & 1:
        v -= 1 << bits
    if v == 0:
        return zero()
    return fin(1 if v < 0 else 0, abs(v), 0)


def encode_int(n, bits):
    return n & ((1 << bits) - 1)
