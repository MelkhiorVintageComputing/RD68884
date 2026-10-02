# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The arithmetic model against statements in the manual, one test each.

Every test names the passage it checks. TestFloat covers the bulk of the
IEEE arithmetic (tools/model/check_testfloat.py); these cover what TestFloat
cannot: the MC68881's own extended range, NaN rules, range control, the
non-IEEE instructions, packed decimal and the exception byte.
"""

import decimal
import random
import unittest

from model import arith as A, formats as F, xnum as X, packed as P
from model.arith import OP
from model.xnum import fin, zero, inf, RN, RZ, RM, RP


def st_with(*vals, fpcr=0):
    st = A.State()
    st.fpcr = fpcr
    for i, v in enumerate(vals):
        st.fp[i] = v if isinstance(v, F.Reg) else F.encode_x(v)
    return st


def reg_op(st, op, src, dst):
    """Opclass 000: FPsrc <op> FPdst -> FPdst."""
    res = A.general(st, (src << 10) | (dst << 7) | op)
    A.apply(st, res)
    return res


def mem_in(st, fmt, image, op=0, dst=0):
    res = A.general(st, (2 << 13) | (fmt << 10) | (dst << 7) | op, image)
    A.apply(st, res)
    return res


def move_out(st, fmt, src, k=0):
    res = A.general(st, (3 << 13) | (fmt << 10) | (src << 7) | (k & 0x7F))
    A.apply(st, res)
    return res


def val(st, i):
    return F.decode_x(st.fp[i])


def fcr(prec=0, mode=0, enable=0):
    return (enable << 8) | (prec << 6) | (mode << 4)


class ExtendedRange(unittest.TestCase):
    """FPU table 3-3 and the footnote to 6.1.5."""

    def test_exponent_zero_with_integer_bit_is_normal(self):
        r = F.Reg(0, 0, 1 << 63)
        self.assertEqual(F.decode_x(r), fin(0, 1, -16383))
        st = st_with(r)
        res = reg_op(st, OP['FMOVE'], 0, 1)
        self.assertEqual(res.exc, 0, 'no underflow at the extended minimum exponent')
        self.assertEqual(st.fp[1], r)

    def test_denormal_is_tiny_but_exact(self):
        r = F.Reg(0, 0, 1 << 62)                       # 2^-16384
        st = st_with(r)
        res = reg_op(st, OP['FMOVE'], 0, 1)
        self.assertEqual(res.exc, A.UNFL)              # FMOVE table: UNFL for a denormal
        self.assertFalse(st.fpsr & A.A_UNFL, 'AEXC UNFL needs INEX2 too (6.1.10)')
        self.assertEqual(st.fp[1], r)

    def test_smallest_denormal(self):
        st = st_with(fin(0, 1, -16446), fin(0, 1, -1))
        res = reg_op(st, OP['FMUL'], 1, 0)              # 2^-16447: half the LSB
        self.assertEqual(val(st, 0), zero(0))           # RN tie to even -> 0
        self.assertEqual(res.exc, A.UNFL | A.INEX2)
        st = st_with(fin(0, 1, -16446), fin(0, 1, -1), fpcr=fcr(mode=RP))
        reg_op(st, OP['FMUL'], 1, 0)
        self.assertEqual(val(st, 0), fin(0, 1, -16446))  # 6.1.5: RP gives the smallest


class RangeControl(unittest.TestCase):
    """FPU 2.2.2 and the note in 6.1.7."""

    def test_single_overflow_rz_gives_largest_single(self):
        st = st_with(fin(0, 1, 100), fin(0, 1, 100), fpcr=fcr(prec=1, mode=RZ))
        res = reg_op(st, OP['FMUL'], 1, 0)
        self.assertTrue(res.exc & A.OVFL)
        self.assertEqual(st.fp[0], F.Reg(0, 127 + 16383, 0xFFFFFF0000000000))

    def test_single_overflow_rn_gives_infinity(self):
        st = st_with(fin(0, 1, 100), fin(0, 1, 100), fpcr=fcr(prec=1))
        reg_op(st, OP['FMUL'], 1, 0)
        self.assertEqual(val(st, 0), inf(0))

    def test_single_denormal_range(self):
        st = st_with(fin(0, 1, -100), fin(0, 3, -50), fpcr=fcr(prec=1))
        res = reg_op(st, OP['FMUL'], 1, 0)             # 3 * 2^-150 -> single denormal
        self.assertTrue(res.exc & A.UNFL and res.exc & A.INEX2)
        self.assertEqual(val(st, 0), fin(0, 1, -148))  # 1.5 * 2^-149 rounds to 2^-148

    def test_fsglmul_keeps_extended_exponent(self):
        st = st_with(fin(0, 1, 100), fin(0, 1, 100), fpcr=fcr(prec=1))
        res = reg_op(st, OP['FSGLMUL'], 1, 0)
        self.assertEqual(res.exc & A.OVFL, 0)          # 6.1.4 note
        self.assertEqual(val(st, 0), fin(0, 1, 200))

    def test_fsglmul_truncates_inputs(self):
        a = fin(0, (1 << 30) | 1, -30)                 # 1 + 2^-30: truncated to 1
        st = st_with(a, fin(0, 3, 0))
        res = reg_op(st, OP['FSGLMUL'], 1, 0)
        self.assertEqual(val(st, 0), fin(0, 3, 0))
        self.assertEqual(res.exc, 0)


class Specials(unittest.TestCase):
    """FPU 4.6 operation tables and 4.5.4."""

    def test_inf_minus_inf(self):
        st = st_with(inf(0), inf(1))
        res = reg_op(st, OP['FADD'], 1, 0)
        self.assertEqual(res.exc, A.OPERR)
        self.assertEqual(st.fp[0], F.Reg(0, 0x7FFF, (1 << 64) - 1))  # 3.2.5

    def test_operr_trap_suppresses_store(self):
        st = st_with(inf(0), inf(1), fpcr=fcr(enable=A.OPERR))
        res = reg_op(st, OP['FADD'], 1, 0)
        self.assertEqual(res.regs, {})
        self.assertEqual(A.trap_vector(res.exc, st.enable), 52)

    def test_exact_zero_sum_sign(self):
        for mode, sign in ((RN, 0), (RZ, 0), (RP, 0), (RM, 1)):
            st = st_with(fin(0, 5, 0), fin(1, 5, 0), fpcr=fcr(mode=mode))
            reg_op(st, OP['FADD'], 1, 0)
            self.assertEqual(val(st, 0), zero(sign))   # figure 4-2 note 1

    def test_divide_by_zero(self):
        st = st_with(fin(1, 3, 0), zero(0))
        res = reg_op(st, OP['FDIV'], 1, 0)
        self.assertEqual(res.exc, A.DZ)
        self.assertEqual(val(st, 0), inf(1))
        st = st_with(inf(0), zero(1))
        res = reg_op(st, OP['FDIV'], 1, 0)
        self.assertEqual(res.exc, 0)                   # DZ only if FPn in range
        self.assertEqual(val(st, 0), inf(1))
        st = st_with(zero(0), zero(0))
        self.assertEqual(reg_op(st, OP['FDIV'], 1, 0).exc, A.OPERR)

    def test_two_nans_return_destination(self):
        qa = F.Reg(0, 0x7FFF, 0xC000000000000001)
        qb = F.Reg(1, 0x7FFF, 0xC000000000000002)
        st = st_with(qa, qb)
        reg_op(st, OP['FADD'], 1, 0)                   # FP0 = FP1 + FP0: FP0 is dest
        self.assertEqual(st.fp[0], qa)

    def test_snan_is_quieted(self):
        s = F.Reg(1, 0x7FFF, 0x8000000000000001)
        st = st_with(fin(0, 1, 0), s)
        res = reg_op(st, OP['FADD'], 1, 0)
        self.assertEqual(res.exc, A.SNAN)
        self.assertEqual(st.fp[0], F.Reg(1, 0x7FFF, 0xC000000000000001))
        st = st_with(fin(0, 1, 0), s, fpcr=fcr(enable=A.SNAN))
        res = reg_op(st, OP['FADD'], 1, 0)
        self.assertEqual(res.regs, {})

    def test_fcmp_table(self):
        cases = [(zero(0), zero(1), A.CC_Z), (zero(1), zero(0), A.CC_Z | A.CC_N),
                 (inf(1), inf(1), A.CC_Z | A.CC_N), (inf(0), inf(0), A.CC_Z),
                 (fin(0, 1, 0), fin(0, 2, 0), A.CC_N), (fin(0, 2, 0), fin(0, 1, 0), 0),
                 (zero(0), inf(0), A.CC_N), (inf(1), fin(0, 1, 0), A.CC_N)]
        for dst, src, cc in cases:
            st = st_with(dst, src)
            reg_op(st, OP['FCMP'], 1, 0)
            self.assertEqual((st.fpsr >> 24) & 0xF, cc, (dst, src))

    def test_fint_examples(self):
        x = X.round_fin(X.from_frac(13757, 100), RN, X.EXT).value   # FPU 4.6 FINT
        for mode, want in ((RZ, 137), (RM, 137), (RN, 138), (RP, 138)):
            st = st_with(x, fpcr=fcr(mode=mode))
            res = reg_op(st, OP['FINT'], 0, 1)
            self.assertEqual(val(st, 1), fin(0, want, 0))
            self.assertEqual(res.exc, A.INEX2)
        st = st_with(x, fpcr=fcr(mode=RP))
        reg_op(st, OP['FINTRZ'], 0, 1)
        self.assertEqual(val(st, 1), fin(0, 137, 0))

    def test_getexp_getman(self):
        st = st_with(fin(0, 12, 0))
        reg_op(st, OP['FGETEXP'], 0, 1)
        reg_op(st, OP['FGETMAN'], 0, 2)
        self.assertEqual(val(st, 1), fin(0, 3, 0))
        self.assertEqual(val(st, 2), fin(0, 3, -1))
        st = st_with(fin(1, 1, -16400))
        reg_op(st, OP['FGETEXP'], 0, 1)
        self.assertEqual(val(st, 1), fin(1, 16400, 0))
        st = st_with(inf(0))
        self.assertEqual(reg_op(st, OP['FGETMAN'], 0, 1).exc, A.OPERR)

    def test_mod_rem_and_quotient(self):
        def run(op, a, b):
            st = st_with(a, b)
            reg_op(st, op, 1, 0)
            return val(st, 0), (st.fpsr >> 16) & 0xFF
        self.assertEqual(run(OP['FMOD'], fin(0, 7, 0), fin(0, 2, 0)), (fin(0, 1, 0), 3))
        self.assertEqual(run(OP['FMOD'], fin(1, 7, 0), fin(0, 2, 0)), (fin(1, 1, 0), 0x83))
        self.assertEqual(run(OP['FREM'], fin(0, 7, 0), fin(0, 2, 0)), (fin(1, 1, 0), 4))
        self.assertEqual(run(OP['FREM'], fin(0, 5, 0), fin(0, 2, 0)), (fin(0, 1, 0), 2))
        self.assertEqual(run(OP['FREM'], fin(0, 4, 0), fin(0, 2, 0)), (zero(0), 2))
        big = fin(0, 1, 300)                      # 2^300 mod 3 = 1 (2^even mod 3)
        v, q = run(OP['FMOD'], big, fin(0, 3, 0))
        self.assertEqual(v, fin(0, 1, 0))
        self.assertEqual(q, ((1 << 300) // 3) & 0x7F)

    def test_fscale(self):
        st = st_with(fin(0, 3, -1), fin(0, 3, 0))
        reg_op(st, OP['FSCALE'], 1, 0)
        self.assertEqual(val(st, 0), fin(0, 3, 2))
        st = st_with(fin(0, 1, -16000), fin(0, 1, 14))    # 2^14: always overflows
        self.assertTrue(reg_op(st, OP['FSCALE'], 1, 0).exc & A.OVFL)
        st = st_with(fin(0, 1, 0), fin(0, 27, -3))        # 3.375 chops to 3
        reg_op(st, OP['FSCALE'], 1, 0)
        self.assertEqual(val(st, 0), fin(0, 1, 3))

    def test_fsincos_shared_register(self):
        st = st_with(fin(0, 1, -1))
        res = A.general(st, (0 << 10) | (2 << 7) | 0x30 | 2)
        A.apply(st, res)
        sin_only = A.general(st_with(fin(0, 1, -1)), (0 << 10) | (2 << 7) | 0x0E)
        self.assertEqual(st.fp[2], sin_only.regs[2])

    def test_illegal_opmode(self):
        self.assertTrue(A.general(A.State(), 0x0040).fline)
        self.assertTrue(A.general(A.State(), (1 << 13)).fline)       # opclass 001


class Conversions(unittest.TestCase):
    """FPU 4.6 FMOVE, 6.1.3."""

    def test_long_out(self):
        st = st_with(fin(0, 1, 31))
        res = move_out(st, F.FMT_L, 0)
        self.assertEqual((res.mem, res.exc), (0x7FFFFFFF, A.OPERR))
        st = st_with(fin(1, 1, 31))
        self.assertEqual(move_out(st, F.FMT_L, 0).mem, 0x80000000)
        st = st_with(fin(0, 3, -1))
        res = move_out(st, F.FMT_L, 0)
        self.assertEqual((res.mem, res.exc), (2, A.INEX2))
        st = st_with(inf(1))
        self.assertEqual(move_out(st, F.FMT_W, 0).mem, 0x8000)
        st = st_with(F.Reg(0, 0x7FFF, 0xC123456700000000))
        res = move_out(st, F.FMT_L, 0)
        self.assertEqual((res.mem, res.exc), (0xC1234567, A.OPERR))

    def test_condition_codes_untouched_by_move_out(self):
        st = st_with(fin(0, 1, 0))
        st.fpsr = 0x0F000000
        move_out(st, F.FMT_L, 0)
        self.assertEqual(st.fpsr >> 24, 0xF)

    def test_byte_in(self):
        st = A.State()
        mem_in(st, F.FMT_B, 0xFF)
        self.assertEqual(val(st, 0), fin(1, 1, 0))

    def test_long_in_single_precision_is_inexact(self):
        st = A.State()
        st.fpcr = fcr(prec=1)
        res = mem_in(st, F.FMT_L, 0x01000001)          # 2^24 + 1
        self.assertEqual(res.exc, A.INEX2)

    def test_single_out_denormal(self):
        st = st_with(fin(0, 1, -149))
        res = move_out(st, F.FMT_S, 0)
        self.assertEqual((res.mem, res.exc), (1, A.UNFL))


class Packed(unittest.TestCase):
    """FPU 3.3, table 3-4, 4.6 FMOVE k-factor table."""

    X12345 = X.round_fin(X.from_frac(12345678765, 10 ** 6), RN, X.EXT).value

    @staticmethod
    def image(sm, se, exp, digits, exp3=0):
        v = (sm << 95) | (se << 94)
        e = [int(c) for c in f'{exp:03d}']
        v |= (e[0] << 88) | (e[1] << 84) | (e[2] << 80) | (exp3 << 76)
        d = [int(c) for c in digits.ljust(17, '0')]
        v |= d[0] << 64
        for i, x in enumerate(d[1:]):
            v |= x << (4 * (15 - i))
        return v

    def test_k_factor_table(self):
        table = {-5: '1234567877', -3: '12345679', -1: '123457', 0: '12346',
                 1: '1', 3: '123', 5: '12346'}
        for k, digits in table.items():
            img, flags = P.encode_p(self.X12345, k, RN)
            self.assertEqual(img, self.image(0, 0, 4, digits), k)
            self.assertIn('inex2', flags)

    def test_k_over_17_is_operr(self):
        img, flags = P.encode_p(fin(0, 1, 0), 20, RN)
        self.assertIn('operr', flags)
        self.assertEqual(img, self.image(0, 0, 0, '1'))

    def test_input(self):
        st = A.State()
        res = mem_in(st, F.FMT_P, self.image(0, 0, 0, '15'))   # 1.5E0
        self.assertEqual(val(st, 0), fin(0, 3, -1))
        self.assertEqual(res.exc, 0)
        res = mem_in(st, F.FMT_P, self.image(0, 1, 1, '1'))    # 1E-1
        self.assertEqual(res.exc, A.INEX1)
        res = mem_in(st, F.FMT_P, self.image(1, 0, 123, '0'))  # -0, any exponent
        self.assertEqual(val(st, 0), zero(1))

    def test_input_infinity_and_nan(self):
        st = A.State()
        inf_img = (1 << 95) | (1 << 94) | (3 << 92) | (0xFFF << 80)
        mem_in(st, F.FMT_P, inf_img)
        self.assertEqual(val(st, 0), inf(1))
        mem_in(st, F.FMT_P, inf_img | 0x4000000000000001)
        self.assertEqual(st.fp[0], F.Reg(1, 0x7FFF, 0x4000000000000001 | (1 << 62)))

    def test_four_digit_exponent(self):
        img, flags = P.encode_p(fin(0, 1, 4000), 17, RN)      # 2^4000 ~ 1.3E1204
        self.assertIn('operr', flags)
        self.assertEqual((img >> 76) & 0xF, 1)                # EXP3
        self.assertEqual((img >> 80) & 0xFFF, 0x204)

    def test_against_python_decimal(self):
        rng = random.Random(1)
        rounding = {RN: decimal.ROUND_HALF_EVEN, RZ: decimal.ROUND_DOWN,
                    RM: decimal.ROUND_FLOOR, RP: decimal.ROUND_CEILING}
        for _ in range(400):
            x = fin(rng.getrandbits(1), rng.getrandbits(64) | (1 << 63), rng.randint(-1200, 1200))
            k = rng.randint(1, 17)
            mode = rng.randint(0, 3)
            img, flags = P.encode_p(x, k, mode)
            num, den = x.frac()
            ctx = decimal.Context(prec=k, rounding=rounding[mode], Emax=10 ** 6, Emin=-10 ** 6)
            d = ctx.divide(decimal.Decimal(num), decimal.Decimal(den))
            sign, digits, exp = d.as_tuple()
            digits = ''.join(map(str, digits)).ljust(k, '0')
            e10 = exp + len(d.as_tuple().digits) - 1
            want = self.image(sign, 1 if e10 < 0 else 0, abs(e10) % 1000, digits,
                              abs(e10) // 1000)
            self.assertEqual(img, want, (x, k, mode))


class Status(unittest.TestCase):
    """FPU 2.3.4, 6.1.9 and 6.1.10."""

    def test_accrued(self):
        self.assertEqual(A.aexc_of(A.UNFL), 0)
        self.assertEqual(A.aexc_of(A.UNFL | A.INEX2), A.A_UNFL | A.A_INEX)
        self.assertEqual(A.aexc_of(A.OVFL), A.A_OVFL | A.A_INEX)
        self.assertEqual(A.aexc_of(A.SNAN), A.A_IOP)
        self.assertEqual(A.aexc_of(A.INEX1), A.A_INEX)

    def test_inexact_trap_on_untrapped_overflow(self):
        self.assertEqual(A.trap_vector(A.OVFL, A.INEX2), 49)
        self.assertEqual(A.trap_vector(A.OVFL | A.INEX2, A.OVFL | A.INEX2), 53)
        self.assertIsNone(A.trap_vector(A.OVFL, A.INEX1))

    def test_priority(self):
        self.assertEqual(A.trap_vector(0xFF, 0xFF), 48)
        self.assertEqual(A.trap_vector(A.OPERR | A.INEX2, 0xFF), 52)
        self.assertEqual(A.trap_vector(A.UNFL | A.DZ, 0xFF), 51)

    def test_exc_cleared_by_each_instruction(self):
        st = st_with(fin(0, 1, 0), zero(0))
        reg_op(st, OP['FDIV'], 1, 0)
        self.assertTrue(st.fpsr & (A.DZ << 8))
        reg_op(st, OP['FMOVE'], 1, 2)
        self.assertFalse(st.fpsr & (A.DZ << 8))
        self.assertTrue(st.fpsr & A.A_DZ)                # accrued stays


class Constants(unittest.TestCase):
    """FPU 4.6 FMOVECR."""

    def cr(self, offset, mode=RN, prec=0):
        st = A.State()
        st.fpcr = fcr(prec=prec, mode=mode)
        res = A.general(st, (2 << 13) | (7 << 10) | offset)
        A.apply(st, res)
        return st.fp[0], res.exc

    def test_pi(self):
        self.assertEqual(self.cr(0x00), (F.Reg(0, 0x4000, 0xC90FDAA22168C235), A.INEX2))
        self.assertEqual(self.cr(0x00, RZ)[0], F.Reg(0, 0x4000, 0xC90FDAA22168C234))

    def test_powers_of_ten(self):
        self.assertEqual(self.cr(0x34), (F.encode_x(fin(0, 100, 0)), 0))
        self.assertEqual(self.cr(0x37)[1], 0)          # 10^16 < 2^64: exact
        self.assertEqual(self.cr(0x38)[1], A.INEX2)    # 10^32 needs 75 bits

    def test_rounded_to_precision(self):
        self.assertEqual(self.cr(0x00, prec=1)[0].m & 0xFFFFFFFFFF, 0)


if __name__ == '__main__':
    unittest.main()
