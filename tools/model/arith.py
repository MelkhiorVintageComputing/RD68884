# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The arithmetic of every MC68881 general instruction, from the manual.

    st = State()                          # FP0-FP7, FPCR, FPSR, FPIAR
    res = general(st, cmd, src_image)     # opclass 000, 010 or 011
    apply(st, res)                        # registers, FPSR, as the chip would

general() computes; it never changes `st`. Its OpResult says what the
instruction writes: registers (none if an enabled SNAN, OPERR or DZ trap
suppresses the store, FPU 6.1.2/6.1.3/6.1.6), the FPSR exception byte, the
condition codes, the quotient byte, the memory image for FMOVE out, and the
exceptional operand an FSAVE would show (FPU 6.4.2.2).
"""

from dataclasses import dataclass, field
from typing import Optional

from . import xnum as X
from .xnum import XN, fin, zero, inf, DEFAULT_NAN, round_fin, RN, RZ, RM, RP
from . import formats as F
from .formats import Reg, decode_x, encode_x
from . import packed as P
from . import transcend as T

# FPSR / FPCR bit positions, FPU 2.2 and 2.3.
BSUN, SNAN, OPERR, OVFL, UNFL, DZ, INEX2, INEX1 = (1 << b for b in range(7, -1, -1))
EXC_NAMES = [('BSUN', BSUN), ('SNAN', SNAN), ('OPERR', OPERR), ('OVFL', OVFL),
             ('UNFL', UNFL), ('DZ', DZ), ('INEX2', INEX2), ('INEX1', INEX1)]
A_IOP, A_OVFL, A_UNFL, A_DZ, A_INEX = 0x80, 0x40, 0x20, 0x10, 0x08
CC_N, CC_Z, CC_I, CC_NAN = 8, 4, 2, 1

# Opmodes, FPU table 4-13.
OP = dict(FMOVE=0x00, FINT=0x01, FSINH=0x02, FINTRZ=0x03, FSQRT=0x04,
          FLOGNP1=0x06, FETOXM1=0x08, FTANH=0x09, FATAN=0x0A, FASIN=0x0C,
          FATANH=0x0D, FSIN=0x0E, FTAN=0x0F, FETOX=0x10, FTWOTOX=0x11,
          FTENTOX=0x12, FLOGN=0x14, FLOG10=0x15, FLOG2=0x16, FABS=0x18,
          FCOSH=0x19, FNEG=0x1A, FACOS=0x1C, FCOS=0x1D, FGETEXP=0x1E,
          FGETMAN=0x1F, FDIV=0x20, FMOD=0x21, FADD=0x22, FMUL=0x23,
          FSGLDIV=0x24, FREM=0x25, FSCALE=0x26, FSGLMUL=0x27, FSUB=0x28,
          FSINCOS=0x30, FCMP=0x38, FTST=0x3A)
OPNAME = {v: k for k, v in OP.items()}

# FPU table 4-13 note 3: "$05, $07, $0B, $13, $17, $1B, $29-$2F, $39 and
# $3B-$3F" are redundant with valid instructions and do not trap; the manual
# does not say which. MODEL CHOICE, to be checked against an oracle: the
# opmode with its least significant bit(s) ignored, the reading under which
# each listed code lands on a defined neighbour.
ALIAS = {0x05: 0x04, 0x07: 0x06, 0x0B: 0x0A, 0x13: 0x12, 0x17: 0x16,
         0x1B: 0x1A, 0x39: 0x38, 0x3B: 0x3A}
for _c in range(0x29, 0x30):
    ALIAS[_c] = 0x28
for _c in range(0x3C, 0x40):
    ALIAS[_c] = 0x3A
for _c in range(0x31, 0x38):
    ALIAS[_c] = 0x30          # FSINCOS: the low three bits are FPc

DYADIC = {0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x38}


@dataclass
class State:
    fp: list = field(default_factory=lambda: [F.RESET_REG] * 8)
    fpcr: int = 0
    fpsr: int = 0
    fpiar: int = 0

    @property
    def mode(self):
        return (self.fpcr >> 4) & 3

    @property
    def prec(self):
        # FPU figure 2-3: 11 is "undefined, reserved". MODEL CHOICE: extended.
        return X.PRECS.get((self.fpcr >> 6) & 3, X.EXT)

    @property
    def enable(self):
        return (self.fpcr >> 8) & 0xFF


@dataclass
class OpResult:
    regs: dict = field(default_factory=dict)   # register number -> Reg
    exc: int = 0                               # FPSR bits 15-8
    cc: Optional[int] = None                   # FPSR bits 27-24, None = unchanged
    quot: Optional[int] = None                 # FPSR bits 23-16
    mem: Optional[int] = None                  # FMOVE out image
    mem_bytes: int = 0
    etemp: Optional[Reg] = None                # exceptional operand
    fline: bool = False                        # illegal command word
    clear_exc: bool = True                     # FMOVEM/FMOVE FPcr leave EXC alone
    note: str = ''


def cc_of(x):
    """FPU table 2-1 / 4.5.5.1."""
    cc = CC_N if x.sign else 0
    if x.kind == 'zero':
        cc |= CC_Z
    elif x.kind == 'inf':
        cc |= CC_I
    elif x.kind == 'nan':
        cc |= CC_NAN
    return cc


def aexc_of(exc):
    """FPU 2.3.4 / 6.1.10."""
    a = 0
    if exc & (BSUN | SNAN | OPERR):
        a |= A_IOP
    if exc & OVFL:
        a |= A_OVFL
    if exc & UNFL and exc & INEX2:
        a |= A_UNFL
    if exc & DZ:
        a |= A_DZ
    if exc & (INEX1 | INEX2 | OVFL):
        a |= A_INEX
    return a


def trap_vector(exc, enable):
    """The vector of the highest-priority enabled exception, or None.

    FPU 6.1.9 priority; 6.1.10 item 4 for the inexact trap on an overflow whose
    own trap is disabled.
    """
    for bit, vec in ((BSUN, 48), (SNAN, 54), (OPERR, 52), (OVFL, 53),
                     (UNFL, 51), (DZ, 50)):
        if exc & enable & bit:
            return vec
    if ((exc & (OVFL | INEX2)) and enable & INEX2) or (exc & enable & INEX1):
        return 49
    return None


def apply(st, res):
    """Write an OpResult into the state, as the end of an instruction does."""
    for r, v in res.regs.items():
        st.fp[r] = v
    if res.clear_exc:
        st.fpsr = (st.fpsr & ~0xFF00) | (res.exc << 8)
    else:
        st.fpsr |= res.exc << 8
    st.fpsr |= aexc_of(res.exc)
    if res.cc is not None:
        st.fpsr = (st.fpsr & ~0x0F000000) | (res.cc << 24)
    if res.quot is not None:
        st.fpsr = (st.fpsr & ~0x00FF0000) | (res.quot << 16)


# -----------------------------------------------------------------------------
# Rounding to the destination, with the flags and the exceptional operand
# -----------------------------------------------------------------------------
def _wrap_etemp(x, mode, over):
    """FPU 6.1.4/6.1.5, FPn destination: the intermediate result rounded to
    extended precision with the exponent biased by -$6000 (overflow) or
    +$6000 (underflow), and $0000 for a catastrophic one."""
    r = round_fin(x, mode, X.EXT_UNBOUNDED).value
    E = r.E
    m = r.mant << (63 - (r.mant.bit_length() - 1)) if r.mant.bit_length() <= 64 \
        else r.mant >> (r.mant.bit_length() - 64)
    if over:
        e = 0 if E > 0xA000 else (E + 0x3FFF - 0x6000) & 0x7FFF
    else:
        e = 0 if E <= -40959 else (E + 0x3FFF + 0x6000) & 0x7FFF
    return Reg(r.sign, e, m)


def _finish(res, x, mode, prec, dst_regs):
    """Round x to the precision, set OVFL/UNFL/INEX2, store, set the CC."""
    if x.kind == 'nan':
        v = x
    else:
        r = round_fin(x, mode, prec)
        v = r.value
        if r.unfl:
            res.exc |= UNFL
        if r.ovfl:
            res.exc |= OVFL
        if r.inex:
            res.exc |= INEX2
        if r.ovfl or r.unfl:
            res.etemp = _wrap_etemp(x, mode, r.ovfl)
    for d in dst_regs:
        res.regs[d] = encode_x(v)
    res.cc = cc_of(v)
    return v


def _nan_pick(dst, src):
    """FPU 4.5.4: one NaN is returned; with two, the destination's."""
    if dst is not None and dst.kind == 'nan':
        return dst
    return src


# -----------------------------------------------------------------------------
# The exact operations
# -----------------------------------------------------------------------------
def _add(a, b, mode):
    if a.kind == 'zero' and b.kind == 'zero':
        if a.sign == b.sign:
            return zero(a.sign)
        return zero(1 if mode == RM else 0)
    if a.kind == 'zero':
        return b
    if b.kind == 'zero':
        return a
    e = min(a.exp, b.exp)
    ma = a.mant << (a.exp - e)
    mb = b.mant << (b.exp - e)
    s = (-ma if a.sign else ma) + (-mb if b.sign else mb)
    if s == 0:
        return zero(1 if mode == RM else 0)     # FPU figure 4-2 note 1
    return fin(1 if s < 0 else 0, abs(s), e)


def _mul(a, b):
    sign = a.sign ^ b.sign
    if a.kind == 'zero' or b.kind == 'zero':
        return zero(sign)
    return fin(sign, a.mant * b.mant, a.exp + b.exp)


def _div(a, b):
    """a / b, both finite or a zero."""
    sign = a.sign ^ b.sign
    if a.kind == 'zero':
        return zero(sign)
    v = X.from_frac(a.mant, b.mant)
    return XN(v.kind, sign, v.mant, v.exp + a.exp - b.exp)


def _sqrt(a):
    import math
    # sqrt(m * 2^e): make e even and the radicand wide enough, then isqrt.
    m, e = a.mant, a.exp
    if e & 1:
        m <<= 1
        e -= 1
    sh = max(0, 260 - m.bit_length())
    sh += sh & 1
    m <<= sh
    e -= sh
    r = math.isqrt(m)
    return X.with_sticky(0, r, e // 2, r * r != m)


def _truncate_sgl(x):
    """FSGLMUL/FSGLDIV, FPU 4.5.5.2: the input mantissas are truncated to
    single precision (24 significant bits) with no other checking."""
    if x.kind != 'fin':
        return x
    n = x.mant.bit_length()
    if n <= 24:
        return x
    return fin(x.sign, x.mant >> (n - 24), x.exp + (n - 24))


def _remainder(a, b, nearest):
    """FMOD (round toward zero) and FREM (round to nearest), exact.

    Returns (remainder, quotient sign, quotient low seven bits)."""
    na, da = a.frac()
    nb, db = b.frac()
    num, den = na * db, nb * da          # a/b = num/den
    qsign = 1 if (num < 0) != (den < 0) else 0
    an, ad = abs(num), abs(den)
    q, r = divmod(an, ad)
    if nearest and (2 * r > ad or (2 * r == ad and q & 1)):
        q += 1
    # remainder = a - (+/-q) * b, exactly.
    signed_q = -q if qsign else q
    rn = na * db - signed_q * nb * da
    rd = da * db
    rem = X.from_frac(rn, rd)
    if rem.kind == 'zero':
        rem = zero(a.sign)       # FPU: a zero remainder takes FPn's sign
    return rem, qsign, q & 0x7F


# -----------------------------------------------------------------------------
# The general instructions
# -----------------------------------------------------------------------------
def fetch_source(st, fmt, image, res):
    """Opclass 010: the external operand to an extended value. INEX1 for a
    packed input that does not convert exactly (FPU 6.1.8)."""
    if fmt in F.INT_BITS:
        return F.decode_int(image, F.INT_BITS[fmt])
    if fmt == F.FMT_S:
        return F.decode_s(image)
    if fmt == F.FMT_D:
        return F.decode_d(image)
    if fmt == F.FMT_X:
        return decode_x(F.reg_from_bits96(image))
    if fmt == F.FMT_P:
        v = P.decode_p(image)
        if v.kind == 'fin':
            r = round_fin(v, st.mode, X.EXT)
            if r.inex:
                res.exc |= INEX1
            return r.value
        return v
    raise ValueError(fmt)


def general(st, cmd, image=None, kfactor_reg=None):
    """Execute one opclass 000, 010 or 011 command word.

    image is the external operand for opclass 010 (as an int of the format's
    width), kfactor_reg the main-processor register value for a dynamic
    k-factor. Opclass 011 returns the memory image in res.mem.
    """
    opclass = (cmd >> 13) & 7
    rx = (cmd >> 10) & 7
    ry = (cmd >> 7) & 7
    ext = cmd & 0x7F
    res = OpResult()
    mode, prec, en = st.mode, st.prec, st.enable

    if opclass == 3:
        return _move_out(st, rx, ry, ext, kfactor_reg, res)

    if opclass == 2 and rx == 7:
        return _movecr(st, ry, ext, res)

    if ext & 0x40 or opclass not in (0, 2):
        res.fline = True                       # FPU table 4-13 note 2, 4.7.1.7
        return res

    op = ALIAS.get(ext, ext)
    if opclass == 0:
        src = decode_x(st.fp[rx])
    else:
        src = fetch_source(st, rx, image, res)
    dst = decode_x(st.fp[ry]) if op in DYADIC else None
    src_x = encode_x(src)

    if op == OP['FSINCOS']:
        dsts_cos = ext & 7
    # ---- NaN operands, FPU 4.5.4 ------------------------------------------
    nan_in = [x for x in (dst, src) if x is not None and x.kind == 'nan']
    if nan_in:
        if any(x.is_snan for x in nan_in):
            res.exc |= SNAN
            res.etemp = src_x
        v = _nan_pick(dst, src).quieted()
        if op == OP['FCMP'] or op == OP['FTST']:
            res.cc = cc_of(v)
            return res
        if res.exc & SNAN and en & SNAN:
            return res                         # trap: destination not modified
        if op == OP['FSINCOS']:
            res.regs[dsts_cos] = encode_x(v)
        if op in (OP['FMOD'], OP['FREM']):
            pass                               # quotient byte not affected
        res.regs[ry] = encode_x(v)
        res.cc = cc_of(v)
        return res

    out = _compute(st, op, src, dst, res)
    if out is None:
        return res
    kind, val = out[0], (out[1] if len(out) > 1 else None)

    if kind == 'cc':
        res.cc = val
        return res
    if kind == 'operr':
        res.exc |= OPERR
        res.etemp = src_x
        if en & OPERR:
            return res
        res.regs[ry] = encode_x(DEFAULT_NAN)
        if op == OP['FSINCOS']:
            res.regs[dsts_cos] = encode_x(DEFAULT_NAN)
            res.regs[ry] = encode_x(DEFAULT_NAN)
        res.cc = cc_of(DEFAULT_NAN)
        return res
    if kind == 'dz':
        res.exc |= DZ
        res.etemp = src_x
        if en & DZ:
            return res
        res.regs[ry] = encode_x(val)
        res.cc = cc_of(val)
        return res

    p = X.SGL_XRANGE if op in (OP['FSGLMUL'], OP['FSGLDIV']) else prec
    if op == OP['FSINCOS']:
        cosv = out[2]
        # FPU 4.6 FSINCOS: cosine to FPc first, then sine to FPs, so a shared
        # register ends up holding the sine; the CC follow the sine.
        cres = OpResult()
        _finish(cres, cosv, mode, p, [dsts_cos])
        res.exc |= cres.exc & (INEX2 | OVFL)
        res.regs.update(cres.regs)
        _finish(res, val, mode, p, [ry])
    else:
        _finish(res, val, mode, p, [ry])
    if res.etemp is None and res.exc & SNAN:
        res.etemp = src_x
    return res


def _compute(st, op, src, dst, res):
    """Returns ('val', exact) / ('operr',) / ('dz', value) / ('cc', cc) /
    ('val', sin, cos) for FSINCOS. Sets the quotient byte for FMOD/FREM."""
    mode = st.mode
    k = src.kind
    O = OP

    if op == O['FMOVE']:
        return 'val', src
    if op in (O['FINT'], O['FINTRZ']):
        r, inex = X.round_to_int(src, RZ if op == O['FINTRZ'] else mode)
        if inex:
            res.exc |= INEX2
        return 'val', r
    if op == O['FABS']:
        return 'val', src.abs()
    if op == O['FNEG']:
        return 'val', src.neg()
    if op == O['FSQRT']:
        if src.sign and k != 'zero':
            return ('operr',)
        if k in ('zero', 'inf'):
            return 'val', src
        return 'val', _sqrt(src)
    if op == O['FGETEXP']:
        if k == 'inf':
            return ('operr',)
        if k == 'zero':
            return 'val', src
        return 'val', X.from_int(src.E)
    if op == O['FGETMAN']:
        if k == 'inf':
            return ('operr',)
        if k == 'zero':
            return 'val', src
        return 'val', fin(src.sign, src.mant, -(src.mant.bit_length() - 1))
    if op == O['FTST']:
        return 'cc', cc_of(src)
    if op == O['FCMP']:
        return 'cc', _fcmp(dst, src)

    if op in (O['FADD'], O['FSUB']):
        b = src.neg() if op == O['FSUB'] else src
        if dst.kind == 'inf' and b.kind == 'inf':
            if dst.sign != b.sign:
                return ('operr',)
            return 'val', dst
        if dst.kind == 'inf':
            return 'val', dst
        if b.kind == 'inf':
            return 'val', b
        return 'val', _add(dst, b, mode)

    if op in (O['FMUL'], O['FSGLMUL']):
        a, b = dst, src
        if op == O['FSGLMUL']:
            a, b = _truncate_sgl(a), _truncate_sgl(b)
        sign = a.sign ^ b.sign
        if (a.kind == 'inf' and b.kind == 'zero') or (a.kind == 'zero' and b.kind == 'inf'):
            return ('operr',)
        if a.kind == 'inf' or b.kind == 'inf':
            return 'val', inf(sign)
        return 'val', _mul(a, b)

    if op in (O['FDIV'], O['FSGLDIV']):
        a, b = dst, src
        if op == O['FSGLDIV']:
            a, b = _truncate_sgl(a), _truncate_sgl(b)
        sign = a.sign ^ b.sign
        if (a.kind == 'zero' and b.kind == 'zero') or (a.kind == 'inf' and b.kind == 'inf'):
            return ('operr',)
        if b.kind == 'zero':
            if a.kind == 'inf':
                return 'val', inf(sign)        # no DZ: FPn is not in range
            return 'dz', inf(sign)
        if a.kind == 'inf':
            return 'val', inf(sign)
        if b.kind == 'inf':
            return 'val', zero(sign)
        return 'val', _div(a, b)

    if op in (O['FMOD'], O['FREM']):
        qsign = dst.sign ^ src.sign
        if src.kind == 'zero' or dst.kind == 'inf':
            return ('operr',)
        if dst.kind == 'zero':
            res.quot = qsign << 7
            return 'val', dst
        if src.kind == 'inf':
            # Table note 2: FPn, rounded by the normal termination.
            res.quot = qsign << 7
            return 'val', dst
        rem, qs, q7 = _remainder(dst, src, op == O['FREM'])
        res.quot = (qs << 7) | q7
        return 'val', rem

    if op == O['FSCALE']:
        if src.kind == 'inf':
            return ('operr',)
        if dst.kind in ('zero', 'inf') or src.kind == 'zero':
            return 'val', dst
        n, _ = X.int_value(src, RZ)
        # FPU 4.6 FSCALE: "When the absolute value of the source operand is
        # >= 2^14, an overflow or underflow always results."
        if abs(n) >= 1 << 14:
            n = (1 << 17) if n > 0 else -(1 << 17)
        return 'val', fin(dst.sign, dst.mant, dst.exp + n)

    # ---- transcendentals --------------------------------------------------
    return T.compute(op, src, res)


def _fcmp(dst, src):
    """FPU 4.6 FCMP operation table: Z when equal, N when FPn < source or when
    equal with FPn negative (the -0/-0, -0/+0 and -inf/-inf entries); the I bit
    is always clear."""
    c = X.compare(dst, src)
    if c == 0:
        return CC_Z | (CC_N if dst.sign else 0)
    return CC_N if c < 0 else 0


def _movecr(st, ry, offset, res):
    v = T.rom_constant(offset)
    _finish(res, v, st.mode, st.prec, [ry])
    return res


def _move_out(st, fmt, ry, ext, kreg, res):
    """Opclass 011, FMOVE FPm,<ea>. FPU 4.6 FMOVE register-to-memory.

    Here ry is the *source* register (table 4-11: RX = destination format,
    RY = source FPm). The condition codes are not affected.
    """
    x = decode_x(st.fp[ry])
    mode = st.mode
    res.mem_bytes = F.FMT_BYTES[fmt]
    if x.kind == 'nan' and x.is_snan:
        res.exc |= SNAN
        x = x.quieted()                       # FPU 6.1.2: written with the bit set
    if fmt in F.INT_BITS:
        bits = F.INT_BITS[fmt]
        lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        if x.kind == 'nan':
            if not res.exc & SNAN:
                res.exc |= OPERR
            res.mem = x.mant >> (64 - bits)
        elif x.kind == 'inf':
            res.exc |= OPERR
            res.mem = F.encode_int(lo if x.sign else hi, bits)
        else:
            n, inex = X.int_value(x, mode)
            if n < lo or n > hi:
                # MODEL CHOICE: OPERR alone, no INEX2 (FPU 6.1.3 lists the
                # integer overflow under OPERR only).
                res.exc |= OPERR
                res.mem = F.encode_int(lo if x.sign else hi, bits)
            else:
                if inex:
                    res.exc |= INEX2
                res.mem = F.encode_int(n, bits)
        return res
    if fmt in (F.FMT_S, F.FMT_D):
        p = X.SGL if fmt == F.FMT_S else X.DBL
        enc = F.encode_s if fmt == F.FMT_S else F.encode_d
        if x.kind in ('nan', 'inf', 'zero'):
            res.mem = enc(x)
            return res
        r = round_fin(x, mode, p)
        if r.unfl:
            res.exc |= UNFL
        if r.ovfl:
            res.exc |= OVFL
        if r.inex:
            res.exc |= INEX2
        if r.ovfl or r.unfl:
            # FPU 6.1.4/6.1.5 item 1: mantissa rounded to the destination
            # precision, exponent biased as a normal extended number.
            q = round_fin(x, mode, X.Prec('m', p.bits, -(1 << 40), 1 << 40, -(1 << 41))).value
            res.etemp = Reg(q.sign, (q.E + 16383) & 0x7FFF,
                            q.mant << (63 - (q.mant.bit_length() - 1)))
        res.mem = enc(r.value)
        return res
    if fmt == F.FMT_X:
        if x.kind == 'fin':
            r = round_fin(x, mode, X.EXT)   # exact; flags a denormal as tiny
            if r.unfl:
                res.exc |= UNFL
            x = r.value
        res.mem = encode_x(x).bits96()
        return res
    # Packed, static (011) or dynamic (111) k-factor.
    if fmt == F.FMT_P:
        k = ext
    else:
        k = kreg & 0x7F
    if k & 0x40:
        k -= 0x80
    img, flags = P.encode_p(x, k, mode)
    if 'operr' in flags:
        res.exc |= OPERR
    if 'inex2' in flags:
        res.exc |= INEX2
    res.mem = img
    return res
