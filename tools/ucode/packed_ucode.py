# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The packed decimal microcode: FMOVE.P in and out (FPU 3.3, figure 3-11,
table 3-4; 4.6 FMOVE; 6.1.3, 6.1.8).

The bound is FPU 4.3.3's: 0.97 unit in the last place to nearest, 1.47
otherwise. Both directions scale by a power of ten, 10^s:

  |s| <= 27   exactly: 10^s is exact in the constant ROM, and one multiply
              (s >= 0) or one division (s < 0) with a sticky bit rounds
              correctly. These are the only scales at which the result can
              be exact (doc/microcode.md), so INEX1/INEX2 are exact too.
  otherwise   10^(s mod 64) x 10^(64 floor(s/64)) from the constant ROM, each
              truncated to 72 bits: the scaled value is within 2^-69 of the
              truth, a fraction of the 0.47 the bound leaves. The result is
              never exact there, and STK is forced on.

This is a different method from the golden model's, which converts exactly:
tools/iss/tests/test_arith.py holds the two to the bound, not to the bit.
"""

import crom
from arith_ucode_cmp import mag_cmp

T_X, T_Y, T_ILOG, T_LEN, T_S, T_HI, T_Q = 16, 17, 18, 19, 20, 21, 22
INEX1, INEX2, OPERR, SNAN = 0x01, 0x02, 0x20, 0x40


def emit(p):
    L, u = p.L, p.u

    # =========================================================================
    # scale10: A = x * 10^s, with x in T_X (finite, non-zero, normalised) and
    # s in A's exponent; STK is the sticky bit. Uses T_S and T_HI.
    # =========================================================================
    L('scale10')
    u(RF='WRITE', RFA='IMM', IMM=T_S)
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=0, TGT='sc10_n')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=28, TGT='sc10_pb')
    u(RF='CROM', RFA='ELO', IMM=crom.CR_P10, comment='10^s, exact')
    u(BSRC='RFQ', RF='READ', RFA='IMM', IMM=T_X)
    u(ASRC='RFQ', SEQ='CALL', TGT='mulc')
    u(SEQ='RET')
    L('sc10_n')
    u(EOP='NEGE')
    u(RF='WRITE', RFA='IMM', IMM=T_S, comment='|s|')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=28, TGT='sc10_nb')
    u(RF='CROM', RFA='ELO', IMM=crom.CR_P10, comment='10^|s|, exact')
    u(BSRC='RFQ', RF='READ', RFA='IMM', IMM=T_X)
    u(ASRC='RFQ', SEQ='CALL', TGT='divc')
    u(SEQ='RET')
    for lab, lo, hi in (('sc10_pb', crom.CR_P10, crom.CR_P10H),
                        ('sc10_nb', crom.CR_N10, crom.CR_N10H)):
        L(lab)
        u(RF='CROM', RFA='EHI', IMM=hi)
        u(ASRC='RFQ')
        u(RF='WRITE', RFA='IMM', IMM=T_HI, comment='10^(+-64 floor(|s|/64))')
        u(RF='READ', RFA='IMM', IMM=T_S)
        u(ASRC='RFQ')
        u(RF='CROM', RFA='ELO', IMM=lo, comment='10^(+-(|s| mod 64))')
        u(BSRC='RFQ', RF='READ', RFA='IMM', IMM=T_X)
        u(ASRC='RFQ', SEQ='CALL', TGT='mulc')
        u(RF='READ', RFA='IMM', IMM=T_HI)
        u(BSRC='RFQ')
        u(SEQ='CALL', TGT='mulc')
        u(FLAG='SET_STK', SEQ='RET', comment='never exact at this scale')

    # =========================================================================
    # dig10: A, an integer at bit 0, divided by ten (B = 10 x 2^68): the
    # quotient in C, the remainder digit in {RX[0], A[71:69]} for the XOP
    # DIGR/EDIGR/EDIG3 that goes with the caller's SQFIN.
    # =========================================================================
    L('dig10')
    u(MOP='CLRQ', CTR='LOAD', IMM=68)
    L('dig10_l')
    u(SEQ='LOOP', TGT='dig10_l', MOP='DIVSTEP')
    u(SEQ='RET')

    # =========================================================================
    # Packed in: the image in XI. M, the 17 digits, in A's mantissa; the
    # decimal exponent in A's exponent; then M x 10^(exponent - 16).
    # =========================================================================
    L('pin')
    u(SEQ='BR', COND='P_SPECIAL', TGT='pin_spec')
    u(ASRC='ZERO', EOP='LDI', IMM=0)
    for _ in range(3):
        u(EOP='EXP10', XOP='EDIGL', comment='exponent = 10 exponent + digit')
    u(CTR='LOAD', IMM=16)
    L('pin_dl')
    u(MOP='MUL10')
    u(MOP='ADDDIG', XOP='DIGL', SEQ='LOOP', TGT='pin_dl', comment='M = 10 M + digit')
    u(SEQ='BR', COND='A_ZERO', TGT='pin_zero')
    u(SEQ='BR', NEG=1, COND='P_SE', TGT='pin_ep')
    u(EOP='NEGE')
    L('pin_ep')
    u(EOP='ADDI', IMM=-16 & 0xFFFF, comment='s = exponent - 16')
    u(RF='WRITE', RFA='IMM', IMM=T_Q)
    u(EOP='LDI', IMM=71)
    u(MOP='NORM', comment='M as a value')
    u(RF='WRITE', RFA='IMM', IMM=T_X)
    u(RF='READ', RFA='IMM', IMM=T_Q)
    u(ASRC='RFQ', SEQ='CALL', TGT='scale10')
    # FPU 6.1.8: rounded to extended whatever the precision, INEX1 if inexact.
    u(SGN='XI0', PSR='X')
    u(MOP='ROUND')
    u(PSR='FPCR', SEQ='BR', NEG=1, COND='RINEX', TGT='src_norm')
    u(EXCSET=INEX1, SEQ='JUMP', TGT='src_norm')
    L('pin_zero')
    u(MOP='ZEROM', SGN='XI0', SEQ='JUMP', TGT='op_disp', comment='table 3-4: a zero')
    L('pin_spec')
    u(ASRC='UNPACKX', SEQ='JUMP', TGT='src_norm',
      comment='SE, YY, $FFF: the extended infinity or NaN, the fraction as is')

    # =========================================================================
    # Packed out, k in MASK[6:0] (FPU 4.6 FMOVE register-to-memory)
    # =========================================================================
    L('o_p_go')
    u(SEQ='BR', COND='A_NAN', TGT='po_nan')
    u(SEQ='BR', COND='A_INF', TGT='po_inf')
    u(SEQ='BR', COND='A_ZERO', TGT='po_zero')
    u(SEQ='BR', NEG=1, COND='K_GT17', TGT='po_k')
    u(EXCSET=OPERR, TSRC='IMM', IMM=17, TDST='MASK', comment='k of 18 to 63: OPERR, as 17')
    L('po_k')
    u(MOP='NORM')
    u(RF='WRITE', RFA='IMM', IMM=T_X)
    u(EOP='LOG10', comment='an estimate of floor(log10 |x|), low by at most one')
    u(RF='WRITE', RFA='IMM', IMM=T_ILOG)
    # The number of digits: k, or (for k <= 0) the digits to the left of
    # 10^k, at least one and at most 17 (doc/model.md).
    L('po_len')
    u(SEQ='BR', NEG=1, COND='K_POS', TGT='po_lk0')
    u(EOP='LDK', SEQ='JUMP', TGT='po_lw')
    L('po_lk0')
    u(EOP='ADDI', IMM=1)
    u(EOP='SUBK')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=18, TGT='po_l1')
    u(EOP='LDI', IMM=17)
    L('po_l1')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=1, TGT='po_lw')
    u(EOP='LDI', IMM=1)
    L('po_lw')
    u(RF='WRITE', RFA='IMM', IMM=T_LEN)
    # Y = x * 10^(len - 1 - ilog), with len digits before the point.
    L('po_scale')
    u(RF='READ', RFA='IMM', IMM=T_ILOG)
    u(BSRC='RFQ', EOP='ADDI', IMM=0xFFFF)
    u(EOP='SUBB')
    u(SEQ='CALL', TGT='scale10')
    u(RF='WRITE', RFA='IMM', IMM=T_Q, SGN='ABS', comment='Y; A = |Y|')
    u(SEQ='CALL', TGT='po_vs_len')
    mag_cmp(p, 'po_low', 'po_y_ok', 'po_low')     # |Y| >= 10^len: ilog was low
    L('po_y_ok')
    # Round Y to an integer, in the mode, with its sign.
    u(RF='READ', RFA='IMM', IMM=T_Q)
    u(ASRC='RFQ')
    u(EOP='SA_IA', IMM=63)
    u(MOP='SHRA')
    u(MOP='ROUNDX')
    u(SEQ='BR', NEG=1, COND='RINEX', TGT='po_ex')
    u(EXCSET=INEX2)
    L('po_ex')
    u(EOP='LDI', IMM=63)
    u(MOP='NORM')
    u(RF='WRITE', RFA='IMM', IMM=T_Q, SGN='ABS', comment='q; A = |q|')
    u(SEQ='CALL', TGT='po_vs_len')
    mag_cmp(p, 'po_carry', 'po_q_ok', 'po_carry')  # q = 10^len: a carry
    L('po_q_ok')
    # The digits: |q| x 10^(17 - len), seventeen of them, by division.
    u(RF='READ', RFA='IMM', IMM=T_ILOG)
    u(ASRC='RFQ')
    u(XOP='PINIT', RF='READ', RFA='IMM', IMM=T_LEN, comment='SM, SE')
    u(ASRC='RFQ')
    u(EOP='NEGE')
    u(EOP='ADDI', IMM=17)
    u(RF='CROM', RFA='ELO', IMM=crom.CR_P10)
    u(BSRC='RFQ', RF='READ', RFA='IMM', IMM=T_Q)
    u(ASRC='RFQ', SGN='ABS', SEQ='CALL', TGT='mulc', comment='exact')
    u(EOP='SA_IA', IMM=71)
    u(MOP='SHRA', RF='CROM', RFA='IMM', IMM=crom.CR_P10 + 1, comment='the integer at bit 0')
    u(BSRC='RFQ', comment='B = 10 x 2^68')
    for _ in range(17):
        u(SEQ='CALL', TGT='dig10')
        u(MOP='SQFIN', XOP='DIGR', comment='A = A / 10, a digit in')
    # The exponent's digits; four of them is an OPERR (FPU 6.1.3).
    u(RF='READ', RFA='IMM', IMM=T_ILOG)
    u(ASRC='RFQ')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=1000, TGT='po_eop')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=-999 & 0xFFFF, TGT='po_e2')
    L('po_eop')
    u(EXCSET=OPERR)
    L('po_e2')
    u(MOP='EXPF', comment='|ilog| as a value')
    u(EOP='SA_IA', IMM=71)
    u(MOP='SHRA')
    for _ in range(3):
        u(SEQ='CALL', TGT='dig10')
        u(MOP='SQFIN', XOP='EDIGR')
    u(SEQ='CALL', TGT='dig10')
    u(MOP='SQFIN', XOP='EDIG3', SEQ='JUMP', TGT='out_x')

    # ilog was low by one: one more, and len again.
    L('po_low')
    u(RF='READ', RFA='IMM', IMM=T_ILOG)
    u(ASRC='RFQ')
    u(EOP='ADDI', IMM=1)
    u(RF='WRITE', RFA='IMM', IMM=T_ILOG, SEQ='JUMP', TGT='po_len')
    # The rounding carried into a new digit (9.99 -> 10.0): one more ilog,
    # and for k <= 0 one more digit, up to 17.
    L('po_carry')
    u(RF='READ', RFA='IMM', IMM=T_ILOG)
    u(ASRC='RFQ')
    u(EOP='ADDI', IMM=1)
    u(RF='WRITE', RFA='IMM', IMM=T_ILOG)
    u(RF='READ', RFA='IMM', IMM=T_LEN)
    u(ASRC='RFQ')
    u(SEQ='BR', COND='K_POS', TGT='po_scale')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=17, TGT='po_scale')
    u(EOP='ADDI', IMM=1)
    u(RF='WRITE', RFA='IMM', IMM=T_LEN, SEQ='JUMP', TGT='po_scale')

    # po_vs_len: B = 10^len, A unchanged (STK too).
    L('po_vs_len')
    u(RF='WRITE', RFA='IMM', IMM=T_HI)
    u(RF='READ', RFA='IMM', IMM=T_LEN)
    u(ASRC='RFQ')
    u(RF='CROM', RFA='ELO', IMM=crom.CR_P10)
    u(BSRC='RFQ', RF='READ', RFA='IMM', IMM=T_HI)
    u(ASRC='RFQ', SEQ='RET')

    # Infinities, NaNs and zeros (FPU table 3-4): PACKX writes exactly them.
    L('po_nan')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='po_pk')
    u(EXCSET=SNAN, MOP='QUIET', SEQ='JUMP', TGT='po_pk')
    L('po_inf')
    u(MOP='INFA', SEQ='JUMP', TGT='po_pk')
    L('po_zero')
    u(MOP='ZEROM')
    L('po_pk')
    u(XOP='PACKX', SEQ='JUMP', TGT='out_x')
