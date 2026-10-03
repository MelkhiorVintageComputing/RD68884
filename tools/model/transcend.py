# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Reference values for the transcendental instructions and the constant ROM.

These are the *mathematical* answers, computed with mpmath far beyond extended
precision and then rounded once like any other result. The MC68881 is not
correctly rounded here -- FPU 4.3.2 promises one ulp of double at worst --
so the hardware is compared against these in ulps, never bit for bit.

The special operands follow each instruction's operation table in FPU 4.6.
mpmath is the third_party/mpmath submodule; the Makefile puts it on PYTHONPATH.
"""

import mpmath
from mpmath import mp

from .xnum import XN, fin, zero, inf, with_sticky

WP = 320        # working precision, bits

_O = dict(FSINH=0x02, FLOGNP1=0x06, FETOXM1=0x08, FTANH=0x09, FATAN=0x0A,
          FASIN=0x0C, FATANH=0x0D, FSIN=0x0E, FTAN=0x0F, FETOX=0x10,
          FTWOTOX=0x11, FTENTOX=0x12, FLOGN=0x14, FLOG10=0x15, FLOG2=0x16,
          FCOSH=0x19, FACOS=0x1C, FCOS=0x1D, FSINCOS=0x30)


def to_mpf(x):
    with mp.workprec(max(WP, x.mant.bit_length() + 8)):
        v = mpmath.ldexp(mpmath.mpf(x.mant), x.exp)
        return -v if x.sign else v


def from_mpf(v, exact=False):
    """An mpf to an XN, sticky-tagged unless the value is known exact."""
    if v == 0:
        return zero(1 if mpmath.sign(v) < 0 else 0)
    s, man, exp, _ = v._mpf_
    return with_sticky(s, int(man), int(exp), not exact)


def _eval(fn, x, exact=False):
    """fn at the exact argument, rounded once later. exact says the result is
    known to be representable (2^n, 10^n, log2 of 2^k, ...): by Lindemann's
    theorem none of these functions is otherwise exact at a non-zero
    argument, so the caller says when, rather than precision guessing it
    (a heuristic that took expm1(-443) for -1)."""
    with mp.workprec(WP):
        a = fn(to_mpf(x))
    if a == 0:
        return zero(x.sign)
    return from_mpf(a, exact)


def _is_pow10(n):
    while n > 1 and n % 10 == 0:
        n //= 10
    return n == 1


def _is_int(x):
    return x.kind == 'fin' and x.exp >= 0


def _pow2_k(x):
    """k if x = 2^k exactly, else None."""
    if x.kind == 'fin' and not x.sign and x.mant & (x.mant - 1) == 0:
        return x.E
    return None


def _const(fn):
    with mp.workprec(WP):
        return from_mpf(fn())



def _pi_over_2(sign):
    v = _const(lambda: mp.pi / 2)
    return XN(v.kind, sign, v.mant, v.exp)


def _huge(sign_of_overflow):
    """A value certainly beyond the 17-bit intermediate exponent: the
    catastrophic over/underflow of FPU 6.1.4/6.1.5."""
    return fin(0, 1, (1 << 20) if sign_of_overflow else -(1 << 20))


def compute(op, x, res):
    """('val', XN), ('val', sin, cos), ('operr',) or ('dz', XN)."""
    O = _O
    k = x.kind
    one = fin(0, 1, 0)

    def mag_gt_1():
        return k == 'inf' or (k == 'fin' and (x.E > 0 or (x.E == 0 and x.mant & (x.mant - 1))))

    def is_one():
        return k == 'fin' and x.E == 0 and x.mant & (x.mant - 1) == 0

    if op == O['FSINH']:
        if k in ('zero', 'inf'):
            return 'val', x
        if x.E > 16:
            return 'val', XN('fin', x.sign, 1, 1 << 20)
        return 'val', _eval(mpmath.sinh, x)
    if op == O['FCOSH']:
        if k == 'zero':
            return 'val', one
        if k == 'inf':
            return 'val', inf(0)
        if x.E > 16:
            return 'val', _huge(True)
        return 'val', _eval(mpmath.cosh, x)
    if op == O['FTANH']:
        if k == 'zero':
            return 'val', x
        if k == 'inf':
            return 'val', fin(x.sign, 1, 0)
        return 'val', _eval(mpmath.tanh, x)
    if op in (O['FETOX'], O['FTWOTOX'], O['FTENTOX']):
        if k == 'zero':
            return 'val', one
        if k == 'inf':
            return 'val', zero(0) if x.sign else inf(0)
        if x.E > 16:
            return 'val', _huge(not x.sign)
        fn = {O['FETOX']: mpmath.exp,
              O['FTWOTOX']: lambda v: mpmath.power(2, v),
              O['FTENTOX']: lambda v: mpmath.power(10, v)}[op]
        # 2^n for an integer n; 10^n for 0 <= n <= 27 (5^27 < 2^64).
        exact = _is_int(x) and (op == O['FTWOTOX'] or
                                (op == O['FTENTOX'] and not x.sign and x.mant << x.exp <= 27))
        return 'val', _eval(fn, x, exact)
    if op == O['FETOXM1']:
        if k == 'zero':
            return 'val', x
        if k == 'inf':
            return 'val', fin(1, 1, 0) if x.sign else inf(0)
        if x.E > 16:
            return 'val', fin(1, 1, 0) if x.sign else _huge(True)
        return 'val', _eval(mpmath.expm1, x)
    if op in (O['FLOGN'], O['FLOG10'], O['FLOG2']):
        if k == 'zero':
            return 'dz', inf(1)                 # FPU 6.1.6: minus infinity
        if x.sign:
            return ('operr',)
        if k == 'inf':
            return 'val', x
        fn = {O['FLOGN']: mpmath.ln, O['FLOG10']: mpmath.log10,
              O['FLOG2']: lambda v: mpmath.log(v, 2)}[op]
        # Exact: log2 of 2^k, log10 of 10^n (n >= 0), and ln 1 (= +0).
        exact = (op == O['FLOG2'] and _pow2_k(x) is not None) or \
            (op == O['FLOG10'] and _is_int(x) and not x.sign and
             _is_pow10(x.mant << x.exp))
        return 'val', _eval(fn, x, exact)
    if op == O['FLOGNP1']:
        if k == 'zero':
            return 'val', x
        if k == 'inf':
            return ('operr',) if x.sign else ('val', x)
        if x.sign and is_one():
            # FPU 6.1.6 and table 6-3: DZ, and "for the FLOGx instructions,
            # return minus infinity". The FLOGNP1 table's note 1 says a NaN;
            # doc/manual-contradictions.md.
            return 'dz', inf(1)
        if x.sign and mag_gt_1():
            return ('operr',)
        return 'val', _eval(mpmath.log1p, x)
    if op in (O['FSIN'], O['FTAN'], O['FCOS'], O['FSINCOS']):
        if k == 'inf':
            return ('operr',)
        if k == 'zero':
            if op == O['FCOS']:
                return 'val', one
            if op == O['FSINCOS']:
                return 'val', x, one
            return 'val', x
        if op == O['FSIN']:
            return 'val', _eval(mpmath.sin, x)
        if op == O['FCOS']:
            return 'val', _eval(mpmath.cos, x)
        if op == O['FTAN']:
            return 'val', _eval(mpmath.tan, x)
        return 'val', _eval(mpmath.sin, x), _eval(mpmath.cos, x)
    if op == O['FATAN']:
        if k == 'zero':
            return 'val', x
        if k == 'inf':
            return 'val', _pi_over_2(x.sign)
        return 'val', _eval(mpmath.atan, x)
    if op in (O['FASIN'], O['FACOS']):
        if mag_gt_1():
            return ('operr',)
        if k == 'zero':
            return 'val', x if op == O['FASIN'] else _pi_over_2(0)
        fn = mpmath.asin if op == O['FASIN'] else mpmath.acos
        if op == O['FACOS'] and is_one() and not x.sign:
            return 'val', zero(0)
        return 'val', _eval(fn, x)
    if op == O['FATANH']:
        if k == 'zero':
            return 'val', x
        if is_one():
            # MODEL CHOICE: the mathematical sign, atanh(+1) = +inf. FPU 4.6
            # FATANH and 6.1.6 both say the opposite; recorded in
            # doc/manual-contradictions.md, to be settled by an oracle.
            return 'dz', inf(x.sign)
        if mag_gt_1():
            return ('operr',)
        return 'val', _eval(mpmath.atanh, x)
    raise ValueError(f'opmode ${op:02X}')


# -----------------------------------------------------------------------------
# The constant ROM, FPU 4.6 FMOVECR
# -----------------------------------------------------------------------------
def rom_constant(offset):
    def ten(n):
        return fin(0, 10 ** n, 0)
    table = {
        0x00: lambda: _const(lambda: mp.pi),
        0x0B: lambda: _const(lambda: mpmath.log10(2)),
        0x0C: lambda: _const(lambda: mp.e),
        0x0D: lambda: _const(lambda: mpmath.log(mp.e, 2)),
        0x0E: lambda: _const(lambda: mpmath.log10(mp.e)),
        0x0F: lambda: zero(0),
        0x30: lambda: _const(lambda: mpmath.ln(2)),
        0x31: lambda: _const(lambda: mpmath.ln(10)),
    }
    for i, n in enumerate([0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]):
        table[0x32 + i] = (lambda n=n: ten(n))
    # The other offsets are "reserved ... may be different on various mask
    # sets". MODEL CHOICE: +0.0.
    return table.get(offset, lambda: zero(0))()
