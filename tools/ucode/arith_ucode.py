# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The arithmetic microcode: opclasses 000 (FPm to FPn), 010 (<ea> to FPn)
and 011 (FPm to <ea>).

The dialogs are FPU 7.5.1.1-7.5.1.3; the operations are FPU 4.6, their
special operands the operation tables there, their exceptions FPU 6.1, the
rounding FPU figure 6-3 with 4.5.5.2's order (underflow, round, overflow).
The golden model is tools/model/arith.py; tools/iss/tests checks this
microcode against it.

Conventions. A holds the source, B the destination of a dyadic operation,
each normalised (load_dst); results come back in A. PSR and RMR (the
precision and rounding mode registers) are set once per instruction. STK is
the sticky bit below A's mantissa; every instruction clears it first.
"""

import crom
import packed_ucode
import trans_ucode
from arith_ucode_cmp import mag_cmp
from busy import bwait, opw_replay, opr_replay

# Register-file temporaries (entries 16 and up, doc/microcode.md).
T_X, T_Y, T_ILOG, T_LEN, T_S, T_HI, T_Q = 16, 17, 18, 19, 20, 21, 22

# Exception byte bits, FPU figure 6-1.
BSUN, SNAN, OPERR, OVFL, UNFL, DZ, INEX2, INEX1 = (1 << b for b in range(7, -1, -1))

# Opmodes implemented in M5 (FPU table 4-13), with the redundant encodings of
# note 3 folded onto their neighbours as tools/model/arith.py does.
OPS = {0x00: 'op_fmove', 0x01: 'op_fint', 0x03: 'op_fintrz', 0x04: 'op_fsqrt',
       0x18: 'op_fabs', 0x1A: 'op_fneg', 0x1E: 'op_fgetexp', 0x1F: 'op_fgetman',
       0x20: 'op_fdiv', 0x22: 'op_fadd', 0x23: 'op_fmul', 0x24: 'op_fsgldiv',
       0x27: 'op_fsglmul', 0x28: 'op_fsub', 0x38: 'op_fcmp', 0x3A: 'op_ftst',
       0x21: 'op_fmod', 0x25: 'op_frem', 0x26: 'op_fscale'}
OPS.update(trans_ucode.FUNCS)
ALIAS = {0x05: 0x04, 0x1B: 0x1A, 0x39: 0x38}
ALIAS.update(trans_ucode.ALIAS)
for _c in range(0x29, 0x30):
    ALIAS[_c] = 0x28
for _c in range(0x3B, 0x40):
    ALIAS[_c] = 0x3A


def emit(p):
    L, u = p.L, p.u

    # =========================================================================
    # Entry. Unimplemented opmodes are refused before any primitive.
    # =========================================================================
    L('op_rr')
    u(SEQ='DISP', IDX='OPMODE', TGT='t_supp')
    L('op_mem')
    u(SEQ='DISP', IDX='RX', TGT='t_memsrc', comment='source specifier 111 is FMOVECR')
    L('op_mem_g')
    u(SEQ='DISP', IDX='OPMODE', TGT='t_supp')
    L('arith_ok')
    u(PSR='FPCR', RMR='FPCR', FPSR='CLREXC', MOP='CLRQ',
      comment='FPU 2.3.3: the exception byte is cleared at the start')
    u(SEQ='DISP', IDX='OPCLASS', TGT='t_src')

    # ---- the source, FPm --------------------------------------------------
    run = 'SET_RUN' if p.model == 68882 else 'NONE'     # rd68884_biu apu_run_i
    L('src_rr')
    u(RESP='WR', IMM=0x0900, ORS='PC', EXPECT='CMD', RF='READ', RFA='RX', FLAG=run,
      comment='null CA=0: release (PC if exceptions are enabled)')
    u(SEQ='WAIT', COND='RESP_READ', ASRC='RFQ',
      comment='the PC transfer, if asked, follows the read')
    u(SEQ='JUMP', TGT='src_norm')

    # ---- the source, <ea>: FPU table 4-14, primitives table 7-5 -----------
    L('src_mem')
    u(SEQ='DISP', IDX='RX', TGT='t_memfmt')
    for fmt, prim, nl, unpack in (('p', 0x960C, 3, None),
                                  ('l', 0x9504, 1, 'UNPACKL'), ('s', 0x9504, 1, 'UNPACKS'),
                                  ('x', 0x960C, 3, 'UNPACKX'), ('w', 0x9502, 1, 'UNPACKW'),
                                  ('d', 0x9608, 2, 'UNPACKD'), ('b', 0x9501, 1, 'UNPACKB')):
        L(f'm_{fmt}')
        if p.model == 68882 and fmt in ('s', 'd', 'x'):
            # FPU figure 7-19: CA = 0. The main processor writes the operand
            # and goes on; the BIU expects a command after the last long
            # word (XFER), and the conversion unit may take it.
            u(RESP='WR', IMM=prim & 0x7FFF, ORS='PC', ONESHOT=1, EXPECT='OPW', XFER=nl,
              FLAG='SET_RUN', comment='evaluate <ea> and transfer data, CA=0')
            u(SEQ='CALL', TGT='wait_first')
            for k in range(nl):
                bwait(p, 'OPW_VALID', False,
                      [dict(RESP='WR', IMM=0x8900, EXPECT='OPW', XFER=nl - k)])
                u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
            u(RESP='WRC', IMM=0x0900, EXPECT='CMD', ASRC=unpack, SEQ='JUMP', TGT='src_norm',
              comment='released already; not over the CU\'s dialog')
            continue
        u(RESP='WR', IMM=prim, ORS='PC', ONESHOT=1, EXPECT='OPW',
          comment='evaluate <ea> and transfer data')
        u(SEQ='CALL', TGT='wait_first')
        for k in range(nl):
            bwait(p, 'OPW_VALID', False, opw_replay())
            u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
        if unpack is None:
            u(RESP='WR', IMM=0x0900, EXPECT='CMD', FLAG=run, SEQ='JUMP', TGT='pin',
              comment='release')
        else:
            u(RESP='WR', IMM=0x0900, EXPECT='CMD', ASRC=unpack, FLAG=run, SEQ='JUMP',
              TGT='src_norm', comment='release')

    # ---- FMOVECR, FPU 4.6: the constant, rounded as any result ----------
    L('movecr')
    u(PSR='FPCR', RMR='FPCR', FPSR='CLREXC', MOP='CLRQ')
    u(RESP='WR', IMM=0x0900, ORS='PC', EXPECT='CMD', FLAG=run, comment='as register to register')
    u(RF='CROM', RFA='CMD', IMM=crom.CR_MOVECR)
    u(SEQ='WAIT', COND='RESP_READ', ASRC='RFQ', comment='STK <= the constant\'s sticky bit')
    L('movecr_go')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    if p.model == 68882:
        # The conversion unit's instruction (program.py cu_take): its
        # dialog is over, its operand in XI. The MC68881's path from the
        # unpacking on.
        L('cu_rr')
        u(RF='READ', RFA='RX')
        u(ASRC='RFQ', SEQ='JUMP', TGT='src_norm')
        L('cu_mem')
        u(SEQ='DISP', IDX='RX', TGT='t_cufmt')
        for fmt, unpack in (('l', 'UNPACKL'), ('s', 'UNPACKS'), ('x', 'UNPACKX'),
                            ('w', 'UNPACKW'), ('d', 'UNPACKD'), ('b', 'UNPACKB')):
            L(f'cu_{fmt}')
            u(ASRC=unpack, SEQ='JUMP', TGT='src_norm')
        L('cu_movecr')
        u(RF='CROM', RFA='CMD', IMM=crom.CR_MOVECR)
        u(ASRC='RFQ', SEQ='JUMP', TGT='movecr_go')

    # Normalise the source (FPU 3.5.1): denormals and unnormals; an
    # unnormalised zero becomes a true zero; an infinity drops its integer bit.
    L('src_norm')
    u(SEQ='BR', COND='A_NAN', TGT='op_disp')
    u(SEQ='BR', COND='A_INF', TGT='src_inf')
    u(MOP='NORM')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='op_disp')
    u(MOP='ZEROM', SEQ='JUMP', TGT='op_disp')
    L('src_inf')
    u(MOP='INFA')
    L('op_disp')
    u(SEQ='DISP', IDX='OPMODE', TGT='t_op')

    # =========================================================================
    # Shared pieces
    # =========================================================================
    # The destination FPn into B, normalised; the source stays in A.
    L('load_dst')
    u(BSRC='A', RF='READ', RFA='RY')
    u(ASRC='RFQ')
    u(SEQ='BR', COND='A_NAN', TGT='ld_swap')
    u(SEQ='BR', COND='A_INF', TGT='ld_inf')
    u(MOP='NORM')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='ld_swap')
    u(MOP='ZEROM', SEQ='JUMP', TGT='ld_swap')
    L('ld_inf')
    u(MOP='INFA')
    L('ld_swap')
    u(ASRC='B', BSRC='A', comment='A = source, B = destination')
    u(SEQ='RET')

    # The exceptional operand for SNAN, OPERR and DZ: the source as an
    # extended value (FPU 6.1.2, 6.1.3, 6.1.6). A source below the extended
    # range is put back into denormal form. May destroy A; every caller is
    # about to replace it.
    L('etemp_src')
    u(SEQ='BR', NEG=1, COND='AE_LT_XMIN', TGT='et_src_w')
    u(EOP='SA_IA', IMM=-16383 & 0xFFFF)
    u(MOP='SHRA', EOP='LDI', IMM=-16383 & 0xFFFF)
    L('et_src_w')
    u(RF='WRITE', RFA='ETEMP', SEQ='RET')

    # NaN operands, FPU 4.5.4: an SNaN signals; the destination's NaN wins
    # over the source's; the result is quiet.
    L('nan2')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='n2_b')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('n2_b')
    u(SEQ='BR', NEG=1, COND='B_SNAN', TGT='n2_pick')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('n2_pick')
    u(SEQ='BR', NEG=1, COND='B_NAN', TGT='n2_q')
    u(ASRC='B')
    L('n2_q')
    u(MOP='QUIET', SEQ='JUMP', TGT='finish')

    L('nan1')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='n1_q')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('n1_q')
    u(MOP='QUIET', SEQ='JUMP', TGT='finish')

    # Operand error: the default NaN (FPU 6.1.3).
    L('operr')
    u(EXCSET=OPERR, SEQ='CALL', TGT='etemp_src')
    u(ASRC='NAN', SEQ='JUMP', TGT='finish')

    # The end of an opclass 000/010 instruction (FPU 6.1): store unless an
    # enabled SNAN, OPERR or DZ forbids it (the condition codes are left alone
    # then, doc/model.md), accrue, and make an enabled exception pending.
    L('finish')
    u(SEQ='BR', COND='SUPPRESS', TGT='fin_x')
    u(RF='WRITE', RFA='RY', FPSR='CC')
    L('fin_x')
    u(FPSR='ACCRUE')
    u(SEQ='BR', NEG=1, COND='TRAP', TGT='done')
    u(FLAG='SET_EXC')
    L('done')
    u(RESP='WRC', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle', comment='null CA=0 PF=1')

    # ---------------------------------------------------------------------
    # Rounding to a register, FPU 4.5.5.2 and 6.1.4-6.1.7. A is finite, not
    # zero and normalised, with STK; PSR and RMR are set. The saved copy in
    # B is the intermediate result the exceptional operand is made from.
    # ---------------------------------------------------------------------
    L('post')
    u(BSRC='A')
    u(SEQ='BR', COND='AE_LT_EMIN', TGT='p_tiny')
    u(MOP='ROUND')
    L('p_rnd')
    u(SEQ='BR', NEG=1, COND='RINEX', TGT='p_ovf')
    u(EXCSET=INEX2)
    L('p_ovf')
    u(SEQ='BR', COND='AE_GT_EMAX', TGT='p_over')
    L('p_fix')
    # Single and double results are extended values in range: a denormal of
    # those formats is stored normalised (FPU 2.2.2).
    u(SEQ='BR', COND='PREC_X', TGT='p_ret')
    u(SEQ='BR', COND='PREC_SGLX', TGT='p_ret')
    u(SEQ='BR', COND='A_J', TGT='p_ret')
    u(SEQ='BR', COND='A_ZERO', TGT='p_zero')
    u(MOP='NORM', SEQ='RET')
    L('p_zero')
    u(MOP='ZEROM')
    L('p_ret')
    u(SEQ='RET')
    L('p_tiny')
    u(EXCSET=UNFL, SEQ='CALL', TGT='et_unf', comment='EXC UNFL: tiny, exact or not (FPU 6.1.5)')
    u(ASRC='B', comment='the intermediate again')
    u(SEQ='BR', COND='PREC_SGLX', TGT='p_tsgl')
    L('p_den')
    u(EOP='SA_EMIN')
    u(MOP='SHRA', EOP='LDEMIN', comment='denormalise')
    u(MOP='ROUND', SEQ='JUMP', TGT='p_rnd')
    # FSGLMUL/FSGLDIV: 24 significant bits, but never below the extended
    # quantum. Up to a 40-bit denormalisation the first bound is the coarser:
    # round in place, then the shift is exact. Beyond, the second.
    L('p_tsgl')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=(-16383 - 40) & 0xFFFF, TGT='p_tsgl2')
    u(EOP='SA_EMIN')
    u(MOP='SHRA', EOP='LDEMIN')
    u(MOP='ROUNDX', SEQ='JUMP', TGT='p_rnd')
    L('p_tsgl2')
    u(MOP='ROUND')
    u(EOP='SA_EMIN')
    u(MOP='SHRA', EOP='LDEMIN', SEQ='JUMP', TGT='p_rnd')
    L('p_over')
    u(EXCSET=OVFL | INEX2, SEQ='CALL', TGT='et_ovf')
    u(SEQ='BR', COND='OVF_INF', TGT='p_inf')
    u(RF='CROM', RFA='PSR', IMM=crom.addr('MAXX'), comment='the largest number (FPU 6.1.4)')
    u(ASRC='RFQ')
    u(SGN='XOR', SEQ='RET', comment='B, the intermediate, has the sign')
    L('p_inf')
    u(MOP='INFA', SEQ='RET')

    # The exceptional operand of an over/underflow to a register (FPU 6.1.4
    # and 6.1.5, item 2): the intermediate rounded to extended precision,
    # its exponent biased by -/+$6000, or $0000 when it is catastrophic.
    L('et_ovf')
    u(ASRC='B')
    u(MOP='ROUNDX')
    u(EOP='ADDI', IMM=-0x6000 & 0xFFFF)
    u(SEQ='BR', COND='AE_GE_IMM', IMM=16385, TGT='et_cat')
    u(SEQ='JUMP', TGT='et_w')
    L('et_unf')
    u(ASRC='B')
    u(MOP='ROUNDX')
    u(EOP='ADDI', IMM=0x6000)
    u(SEQ='BR', COND='AE_GE_IMM', IMM=-16382 & 0xFFFF, TGT='et_w')
    L('et_cat')
    u(EOP='LDI', IMM=-16383 & 0xFFFF)
    L('et_w')
    u(RF='WRITE', RFA='ETEMP', SEQ='RET')

    # ---------------------------------------------------------------------
    # Rounding to memory (FMOVE to S or D), FPU 6.1.4/6.1.5 item 1: the
    # format's range; the exceptional operand is the intermediate rounded to
    # the format's precision with the ordinary extended bias.
    # ---------------------------------------------------------------------
    L('post_mem')
    u(BSRC='A')
    u(SEQ='BR', COND='AE_LT_EMIN', TGT='pm_tiny')
    u(MOP='ROUND')
    L('pm_rnd')
    u(SEQ='BR', NEG=1, COND='RINEX', TGT='pm_ovf')
    u(EXCSET=INEX2)
    L('pm_ovf')
    u(SEQ='BR', NEG=1, COND='AE_GT_EMAX', TGT='pm_ret')
    u(EXCSET=OVFL | INEX2, SEQ='CALL', TGT='et_mem')
    u(SEQ='BR', COND='OVF_INF', TGT='pm_inf')
    u(RF='CROM', RFA='PSR', IMM=crom.addr('MAXX'))
    u(ASRC='RFQ')
    u(SGN='XOR', SEQ='RET')
    L('pm_inf')
    u(MOP='INFA')
    L('pm_ret')
    u(SEQ='RET')
    L('pm_tiny')
    u(EXCSET=UNFL, SEQ='CALL', TGT='et_mem')
    u(ASRC='B')
    u(EOP='SA_EMIN')
    u(MOP='SHRA', EOP='LDEMIN')
    u(MOP='ROUND', SEQ='JUMP', TGT='pm_rnd')
    L('et_mem')
    u(ASRC='B')
    u(MOP='ROUND')
    u(RF='WRITE', RFA='ETEMP', SEQ='RET')

    # =========================================================================
    # Monadic operations
    # =========================================================================
    L('op_fmove')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    L('mono_post')
    u(SEQ='BR', COND='A_INF', TGT='finish')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    L('op_fabs')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SGN='ABS', SEQ='JUMP', TGT='mono_post')
    L('op_fneg')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SGN='NEG', SEQ='JUMP', TGT='mono_post')

    # FSQRT, bit by bit (doc/microcode.md). FPU 4.6 FSQRT table.
    L('op_fsqrt')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', COND='A_SIGN', TGT='operr')
    u(SEQ='BR', COND='A_INF', TGT='finish')
    u(BSRC='A', SEQ='BR', COND='AE_ODD', TGT='sq_odd', comment='B = the radicand')
    u(EOP='SA_IMM', IMM=1)
    u(MOP='SHRB', SEQ='JUMP', TGT='sq_go', comment='even exponent: radicand in [1,2)')
    L('sq_odd')
    u(EOP='ADDI', IMM=0xFFFF, comment='odd exponent: radicand in [2,4)')
    L('sq_go')
    u(MOP='CLRQ', CTR='LOAD', IMM=71)
    u(MOP='ZEROM', EOP='HALF', comment='A = 0, its exponent halved')
    L('sq_loop')
    u(SEQ='LOOP', TGT='sq_loop', MOP='SQSTEP')
    u(MOP='SQFIN', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    # FINT and FINTRZ (FPU 4.6): round to an integer at the right place --
    # shifted so that the integer part ends at the extended LSB -- then put
    # it back and round to the precision as every result is.
    L('op_fint')
    u(SEQ='JUMP', TGT='int_go')
    L('op_fintrz')
    u(RMR='RZ')
    L('int_go')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_INF', TGT='finish')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=63, TGT='int_big')
    u(EOP='SA_IA', IMM=63)
    u(MOP='SHRA')
    u(MOP='ROUNDX')
    u(RMR='FPCR', SEQ='BR', NEG=1, COND='RINEX', TGT='int_nx')
    u(EXCSET=INEX2)
    L('int_nx')
    u(SEQ='BR', COND='A_ZERO', TGT='int_zero')
    u(EOP='LDI', IMM=63)
    u(MOP='NORM', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('int_zero')
    u(MOP='ZEROM', SEQ='JUMP', TGT='finish', comment='a signed zero')
    L('int_big')
    u(RMR='FPCR', SEQ='CALL', TGT='post', comment='already an integer')
    u(SEQ='JUMP', TGT='finish')

    L('op_fgetexp')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_INF', TGT='operr')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(MOP='EXPF')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(MOP='NORM', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    L('op_fgetman')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_INF', TGT='operr')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(EOP='LDI', IMM=0, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    L('op_ftst')
    u(SEQ='BR', NEG=1, COND='A_NAN', TGT='tst_cc')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='tst_q')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('tst_q')
    u(MOP='QUIET')
    L('tst_cc')
    u(FPSR='CC', SEQ='JUMP', TGT='fin_x')

    # =========================================================================
    # Dyadic operations: CALL load_dst, then A = source, B = destination
    # =========================================================================
    L('op_fadd')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    u(SEQ='JUMP', TGT='add_go')
    L('op_fsub')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='sub_neg')
    u(SEQ='BR', NEG=1, COND='B_INF', TGT='sub_neg')
    u(SEQ='BR', NEG=1, COND='SIGN_XOR', TGT='operr', comment='inf - inf: ETEMP is the source as given')
    L('sub_neg')
    u(SGN='NEG', comment='FPn - source = FPn + (-source)')
    L('add_go')
    u(SEQ='BR', COND='A_INF', TGT='add_ainf')
    u(SEQ='BR', COND='B_INF', TGT='res_b')
    u(SEQ='BR', COND='A_ZERO', TGT='add_azero')
    u(SEQ='BR', COND='B_ZERO', TGT='res_a_post')
    u(SEQ='BR', NEG=1, COND='AE_LT_B', TGT='add_al')
    u(ASRC='B', BSRC='A', comment='the larger exponent in A')
    L('add_al')
    u(EOP='SA_AB')
    u(MOP='SHRB', comment='align, the bits shifted out into STK')
    u(SEQ='BR', COND='SIGN_XOR', TGT='add_sub')
    u(MOP='ADD')
    u(MOP='RSH1', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('add_sub')
    u(MOP='SUB', comment='with STK as a borrow')
    u(SEQ='BR', NEG=1, COND='RX0', TGT='add_s2')
    u(MOP='NEG', SGN='NEG', comment='equal exponents, B was the larger')
    L('add_s2')
    u(SEQ='BR', COND='A_ZERO', TGT='add_exz')
    u(MOP='NORM', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('add_exz')
    u(MOP='ZEROM', SGN='RMZ', SEQ='JUMP', TGT='finish',
      comment='an exact zero: +0, -0 in RM (FPU figure 4-2 note 1)')
    L('add_ainf')
    u(SEQ='BR', NEG=1, COND='B_INF', TGT='finish')
    u(SEQ='BR', COND='SIGN_XOR', TGT='operr')
    u(SEQ='JUMP', TGT='finish')
    L('add_azero')
    u(SEQ='BR', NEG=1, COND='B_ZERO', TGT='res_b_post')
    u(SEQ='BR', NEG=1, COND='SIGN_XOR', TGT='finish')
    u(SGN='RMZ', SEQ='JUMP', TGT='finish')
    L('res_a_post')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('res_b')
    u(ASRC='B', SEQ='JUMP', TGT='finish')
    L('res_b_post')
    u(ASRC='B')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    # The cores, for any finite non-zero normalised operands, the sticky bit
    # in STK: mulc A = A x B, 72 x 16 a clock, five clocks; divc A = A / B,
    # one quotient bit a clock (doc/microcode.md).
    # trunc2: A and B, finite and non-zero, truncated to 24 significant bits
    # (FPU 4.5.5.2): shifted down, the low bits dropped, and back.
    L('trunc2')
    u(SEQ='CALL', TGT='trunc1')
    u(ASRC='B', BSRC='A')
    u(SEQ='CALL', TGT='trunc1')
    u(ASRC='B', BSRC='A', SEQ='RET')
    L('trunc1')
    u(EOP='SA_IMM', IMM=48)
    u(MOP='SHRA')
    u(MOP='NORM', FLAG='CLR_STK')
    u(EOP='ADDI', IMM=48, SEQ='RET')

    L('mulc')
    u(SGN='XOR', EOP='ADDB', MOP='CLRQ')
    for _ in range(5):
        u(MOP='MULSTEP')
    u(MOP='MULFIN')
    u(MOP='NORM', SEQ='RET')
    L('divc')
    u(EOP='SUBB', MOP='CLRQ', CTR='LOAD', IMM=73)
    L('div_loop')
    u(SEQ='LOOP', TGT='div_loop', MOP='DIVSTEP')
    u(MOP='DIVFIN')
    u(MOP='NORM', SEQ='RET')

    # FMUL and FSGLMUL.
    L('op_fsglmul')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    u(PSR='SGLX', SEQ='JUMP', TGT='mul_go', comment='FPU 4.5.5.2: single mantissa, extended range')
    L('op_fmul')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    L('mul_go')
    u(SEQ='BR', COND='A_INF', TGT='mul_ainf')
    u(SEQ='BR', COND='B_INF', TGT='mul_binf')
    u(SEQ='BR', COND='A_ZERO', TGT='mul_zero')
    u(SEQ='BR', COND='B_ZERO', TGT='mul_zero')
    u(SEQ='BR', COND='PREC_SGLX', NEG=1, TGT='mul_go2')
    u(SEQ='CALL', TGT='trunc2', comment='FSGLMUL: the operands truncated to single')
    L('mul_go2')
    u(SEQ='CALL', TGT='mulc')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('mul_ainf')
    u(SEQ='BR', COND='B_ZERO', TGT='operr')
    u(SGN='XOR', MOP='INFA', SEQ='JUMP', TGT='finish')
    L('mul_binf')
    u(SEQ='BR', COND='A_ZERO', TGT='operr')
    u(SGN='XOR', MOP='INFA', SEQ='JUMP', TGT='finish')
    L('mul_zero')
    u(SGN='XOR', MOP='ZEROM', SEQ='JUMP', TGT='finish')

    # FDIV and FSGLDIV.
    L('op_fsgldiv')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    u(PSR='SGLX', SEQ='JUMP', TGT='div_go')
    L('op_fdiv')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    L('div_go')
    u(SEQ='BR', COND='A_ZERO', TGT='div_azero')
    u(SEQ='BR', COND='A_INF', TGT='div_ainf')
    u(SEQ='BR', COND='B_INF', TGT='div_inf')
    u(SEQ='BR', COND='B_ZERO', TGT='div_zero')
    u(SEQ='BR', COND='PREC_SGLX', NEG=1, TGT='div_go2')
    u(SEQ='CALL', TGT='trunc2', comment='FSGLDIV: the operands truncated to single')
    L('div_go2')
    u(ASRC='B', BSRC='A', SGN='XOR', comment='A = dividend (FPn), B = divisor')
    u(SEQ='CALL', TGT='divc')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('div_azero')
    u(SEQ='BR', COND='B_ZERO', TGT='operr')
    u(SEQ='BR', COND='B_INF', TGT='div_inf', comment='inf / 0: no DZ, FPn is not in range')
    u(EXCSET=DZ, SEQ='CALL', TGT='etemp_src', comment='FPU 6.1.6')
    L('div_inf')
    u(SGN='XOR', MOP='INFA', SEQ='JUMP', TGT='finish')
    L('div_ainf')
    u(SEQ='BR', COND='B_INF', TGT='operr')
    L('div_zero')
    u(SGN='XOR', MOP='ZEROM', SEQ='JUMP', TGT='finish')

    # FSCALE (FPU 4.6): FPn x 2^int(source), the integer part by truncation.
    # From 2^14 up an overflow or underflow is certain (the FSCALE text):
    # the exponent goes far enough out that ETEMP is catastrophic too.
    L('op_fscale')
    u(SEQ='CALL', TGT='load_dst')
    u(SEQ='BR', COND='A_NAN', TGT='nan2')
    u(SEQ='BR', COND='B_NAN', TGT='nan2')
    u(SEQ='BR', COND='A_INF', TGT='operr')
    u(SEQ='BR', COND='A_ZERO', TGT='sc_dst')
    u(SEQ='BR', COND='B_ZERO', TGT='sc_dst')
    u(SEQ='BR', COND='B_INF', TGT='sc_dst')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=14, TGT='sc_huge')
    u(EOP='SA_IA', IMM=63)
    u(MOP='SHRA', comment='the integer part, at bit 8')
    u(ASRC='B', BSRC='A', comment='A = FPn, B = the integer')
    u(EOP='ADDBI', SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('sc_dst')
    u(ASRC='B')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', COND='A_INF', TGT='finish')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('sc_huge')
    u(SEQ='BR', COND='A_SIGN', TGT='sc_under')
    u(ASRC='B', EOP='LDI', IMM=0x7FFF)
    u(EOP='ADDI', IMM=0x7FFF, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('sc_under')
    u(ASRC='B', EOP='LDI', IMM=-0x7FFF & 0xFFFF)
    u(EOP='ADDI', IMM=-0x7FFF & 0xFFFF, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    # FMOD and FREM (FPU 4.6): the exact remainder of the quotient rounded
    # toward zero, or to nearest (even on a tie), and the quotient byte.
    # Restoring division one bit a clock, as many as the exponents differ
    # (doc/microcode.md); the remainder is exact, then rounded as any result.
    for name, nearest in (('op_fmod', False), ('op_frem', True)):
        pre = name[3:]
        L(name)
        u(SEQ='CALL', TGT='load_dst', comment='A = source y, B = FPn x')
        u(SEQ='BR', COND='A_NAN', TGT='nan2')
        u(SEQ='BR', COND='B_NAN', TGT='nan2')
        u(SEQ='BR', COND='A_ZERO', TGT='operr')
        u(SEQ='BR', COND='B_INF', TGT='operr')
        u(FPSR='QSIGN', comment='the quotient\'s sign')
        u(FPSR='QBITS', comment='C is clear: no bits yet')
        u(SEQ='BR', COND='B_ZERO', TGT='res_b')
        u(SEQ='BR', COND='A_INF', TGT='res_b_post')
        u(SGN='ABS')
        u(RF='WRITE', RFA='IMM', IMM=T_Y, comment='|y|')
        u(ASRC='B', BSRC='A', comment='A = x, B = |y|')
        u(RF='WRITE', RFA='IMM', IMM=T_X, SGN='ABS', comment='x; A = |x|')
        u(SEQ='BR', COND='AE_LT_B', TGT=f'{pre}_r')
        u(EOP='SUBB', comment='the exponents\' difference')
        u(CTR='LOADE', MOP='CLRQ')
        L(f'{pre}_loop')
        u(SEQ='LOOP', TGT=f'{pre}_loop', MOP='DIVSTEP')
        u(EOP='LDB', comment='{RX, A} is twice the remainder, in units of y\'s LSB')
        u(EOP='ADDI', IMM=0xFFFF)
        u(MOP='RSH1')
        L(f'{pre}_r')
        u(SEQ='BR', COND='A_ZERO', TGT=f'{pre}_out')
        u(MOP='NORM')
        if nearest:
            # Round the quotient to nearest: if 2r > y, or 2r = y with the
            # quotient odd, the remainder is r - y and the quotient one more.
            u(EOP='ADDI', IMM=1, comment='2r')
            mag_cmp(p, 'rem_adj', 'rem_noadj')
            u(SEQ='BR', COND='Q_ODD', TGT='rem_adj', comment='a tie: to even')
            L('rem_noadj')
            u(EOP='ADDI', IMM=0xFFFF, SEQ='JUMP', TGT='rem_out')
            L('rem_adj')
            u(EOP='ADDI', IMM=0xFFFF)
            u(ASRC='B', BSRC='A', comment='A = |y|, B = r: y - r is exact')
            u(EOP='SA_AB')
            u(MOP='SHRB')
            u(MOP='SUB')
            u(MOP='NORM')
            u(SGN='NEG', MOP='QINC', comment='the opposite sign; the quotient + 1')
        L(f'{pre}_out')
        if not nearest:
            u(SEQ='JUMP', TGT='rem_out')
    L('rem_out')
    u(FPSR='QBITS', RF='READ', RFA='IMM', IMM=T_X)
    u(BSRC='RFQ')
    u(SGN='XOR', comment='the sign of x, flipped by the adjustment')
    u(SEQ='BR', COND='A_ZERO', TGT='rem_zero')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('rem_zero')
    u(MOP='ZEROM', SEQ='JUMP', TGT='finish', comment='a zero takes the sign of x')

    # FCMP (FPU 4.6): only the condition codes, from the operation table.
    L('op_fcmp')
    u(SEQ='CALL', TGT='load_dst', comment='A = source, B = FPn')
    u(SEQ='BR', COND='A_NAN', TGT='cmp_nan')
    u(SEQ='BR', COND='B_NAN', TGT='cmp_nan')
    u(SEQ='BR', COND='A_ZERO', TGT='cmp_az')
    u(SEQ='BR', COND='B_ZERO', TGT='cmp_bz')
    u(SEQ='BR', COND='SIGN_XOR', TGT='cmp_sd')
    # The same sign: the magnitudes decide, the other way round when negative.
    mag_cmp(p, 'cmp_sgt', 'cmp_slt', 'cmp_eq')
    L('cmp_sgt')                                  # |source| > |FPn|
    u(SEQ='BR', COND='B_SIGN', TGT='cmp_gt')
    u(SEQ='JUMP', TGT='cmp_lt')
    L('cmp_slt')                                  # |source| < |FPn|
    u(SEQ='BR', COND='B_SIGN', TGT='cmp_lt')
    u(SEQ='JUMP', TGT='cmp_gt')
    L('cmp_az')                                   # source zero
    u(SEQ='BR', COND='B_ZERO', TGT='cmp_eq')
    L('cmp_sd')                                   # or the signs differ: FPn's sign
    u(SEQ='BR', COND='B_SIGN', TGT='cmp_lt')
    u(SEQ='JUMP', TGT='cmp_gt')
    L('cmp_bz')                                   # FPn zero: the source's sign
    u(SEQ='BR', COND='A_SIGN', TGT='cmp_gt')
    u(SEQ='JUMP', TGT='cmp_lt')
    L('cmp_gt')
    u(FPSR='CCIMM', IMM=0, SEQ='JUMP', TGT='fin_x')
    L('cmp_eq')
    u(SEQ='BR', COND='B_SIGN', TGT='cmp_eqn')
    u(FPSR='CCIMM', IMM=0x4, SEQ='JUMP', TGT='fin_x')
    L('cmp_eqn')
    u(FPSR='CCIMM', IMM=0xC, SEQ='JUMP', TGT='fin_x')
    L('cmp_lt')
    u(FPSR='CCIMM', IMM=0x8, SEQ='JUMP', TGT='fin_x')
    L('cmp_nan')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='cmp_n2')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('cmp_n2')
    u(SEQ='BR', NEG=1, COND='B_SNAN', TGT='cmp_n3')
    u(EXCSET=SNAN, SEQ='CALL', TGT='etemp_src')
    L('cmp_n3')
    u(SEQ='BR', NEG=1, COND='B_NAN', TGT='cmp_n4')
    u(ASRC='B')
    L('cmp_n4')
    u(MOP='QUIET')
    u(FPSR='CC', SEQ='JUMP', TGT='fin_x')

    # =========================================================================
    # Opclass 011, FPm to <ea>. FPU 7.5.1.3, figure 7-20; 4.6 FMOVE
    # (register-to-memory) for the results and exceptions.
    # =========================================================================
    L('op_out')
    u(SEQ='DISP', IDX='RX', TGT='t_outfmt')
    for fmt, psr in (('l', 'X'), ('s', 'S'), ('x', 'X'), ('w', 'X'), ('d', 'D'), ('b', 'X'),
                     ('p', 'X')):
        L(f'o_{fmt}')
        u(RESP='WR', IMM=0x8900, ORS='PC', ONESHOT=1, EXPECT='RESP', RF='READ', RFA='RY',
          PSR=psr, RMR='FPCR', FPSR='CLREXC', MOP='CLRQ',
          MASK='LOAD' if fmt == 'p' else 'NONE',
          comment='null CA=1 while converting (PC if enabled)' + (
              '; the static k-factor' if fmt == 'p' else ''))
        u(SEQ='CALL', TGT='wait_first')
        u(ASRC='RFQ', SEQ='JUMP', TGT=f'o_{fmt}_go')
    # The dynamic k-factor: the MPU sends Dn first (FPU table 7-5).
    L('o_pd')
    u(RESP='WR', IMM=0x8C00, ORS='DNPC', ONESHOT=1, EXPECT='OPW',
      PSR='X', RMR='FPCR', FPSR='CLREXC', MOP='CLRQ', comment='transfer Dn')
    u(SEQ='CALL', TGT='wait_first')
    bwait(p, 'OPW_VALID', False, opw_replay())
    u(BIU='OPW_ACK', TSRC='OPW', TDST='MASK', RF='READ', RFA='RY', comment='the k-factor')
    u(ASRC='RFQ', SEQ='JUMP', TGT='o_p_go')

    # ---- extended: exact, a denormal signals UNFL --------------------------
    L('o_x_go')
    u(SEQ='BR', COND='A_NAN', TGT='ox_nan')
    u(SEQ='BR', COND='A_INF', TGT='ox_inf')
    u(SEQ='BR', COND='A_ZERO', TGT='ox_zero')
    u(MOP='NORM')
    u(SEQ='BR', NEG=1, COND='AE_LT_XMIN', TGT='ox_pack')
    u(EXCSET=UNFL, EOP='SA_IA', IMM=-16383 & 0xFFFF)
    u(MOP='SHRA', EOP='LDI', IMM=-16383 & 0xFFFF)
    L('ox_pack')
    u(XOP='PACKX', SEQ='JUMP', TGT='out_x')
    L('ox_nan')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='ox_pack')
    u(EXCSET=SNAN, MOP='QUIET', SEQ='JUMP', TGT='ox_pack')
    L('ox_inf')
    u(MOP='INFA', SEQ='JUMP', TGT='ox_pack')
    L('ox_zero')
    u(MOP='ZEROM', SEQ='JUMP', TGT='ox_pack')

    # ---- single and double: the format's precision and range ----------------
    for fmt, xop, out in (('s', 'PACKS', 'out_1'), ('d', 'PACKD', 'out_d')):
        L(f'o_{fmt}_go')
        u(SEQ='BR', COND='A_NAN', TGT=f'o{fmt}_nan')
        u(SEQ='BR', COND='A_INF', TGT=f'o{fmt}_pack')
        u(SEQ='BR', COND='A_ZERO', TGT=f'o{fmt}_pack')
        u(MOP='NORM', SEQ='CALL', TGT='post_mem')
        L(f'o{fmt}_pack')
        u(XOP=xop, SEQ='JUMP', TGT=out)
        L(f'o{fmt}_nan')
        u(SEQ='BR', NEG=1, COND='A_SNAN', TGT=f'o{fmt}_pack')
        u(EXCSET=SNAN, MOP='QUIET', SEQ='JUMP', TGT=f'o{fmt}_pack')

    # ---- integers: round in the current mode, OPERR on overflow -------------
    for fmt, bits, out in (('l', 32, 'out_1'), ('w', 16, 'out_w'), ('b', 8, 'out_b')):
        L(f'o_{fmt}_go')
        u(SEQ='BR', COND='A_NAN', TGT=f'o{fmt}_nan')
        u(SEQ='BR', COND='A_INF', TGT=f'o{fmt}_sat')
        u(SEQ='BR', COND='A_ZERO', TGT=f'o{fmt}_pack')
        u(MOP='NORM')
        u(SEQ='BR', COND='AE_GE_IMM', IMM=bits, TGT=f'o{fmt}_sat')
        u(EOP='SA_IA', IMM=63)
        u(MOP='SHRA')
        u(MOP='ROUNDX')
        u(SEQ='BR', COND='INT_OVF', TGT=f'o{fmt}_sat')
        u(SEQ='BR', NEG=1, COND='RINEX', TGT=f'o{fmt}_pack')
        u(EXCSET=INEX2)
        L(f'o{fmt}_pack')
        u(XOP='PACKI', SEQ='JUMP', TGT=out)
        L(f'o{fmt}_sat')
        u(EXCSET=OPERR, XOP='PACKSAT', SEQ='JUMP', TGT=out,
          comment='FPU 6.1.3: the largest integer of the sign')
        L(f'o{fmt}_nan')
        u(SEQ='BR', NEG=1, COND='A_SNAN', TGT=f'o{fmt}_qnan')
        u(EXCSET=SNAN, MOP='QUIET')
        u(XOP='PACKNI', SEQ='JUMP', TGT=out, comment='the NaN significand\'s top bits')
        L(f'o{fmt}_qnan')
        u(EXCSET=OPERR, XOP='PACKNI', SEQ='JUMP', TGT=out)

    # ---- transfer, then the final primitive ---------------------------------
    # The first wait may see a save request before the primitive was read
    # (an interrupt while polling, FPU 7.5.4.3): then it is written again.
    def first_out_wait(prim):
        def replay(w):
            r = w + '_r'
            u(SEQ='BR', COND='RESP_READ', TGT=r)
            u(RESP='WR', IMM=prim, ONESHOT=1, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR',
              SEQ='JUMP', TGT=w, comment='not read yet: the primitive again')
            L(r)
            u(RESP='WR', IMM=0x8900, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR',
              SEQ='JUMP', TGT=w)
        bwait(p, 'OPR_VALID', True, replay)
    for name, prim in (('out_1', 0xB104), ('out_w', 0xB102), ('out_b', 0xB101)):
        L(name)
        u(RESP='WR', IMM=prim, ONESHOT=1, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR')
        first_out_wait(prim)
        u(SEQ='JUMP', TGT='out_end')
    L('out_d')
    u(RESP='WR', IMM=0xB208, ONESHOT=1, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR')
    first_out_wait(0xB208)
    u(TSRC='XI1', BIU='OPR_WR')
    bwait(p, 'OPR_VALID', True, opr_replay('XI1'))
    u(SEQ='JUMP', TGT='out_end')
    L('out_x')
    u(RESP='WR', IMM=0xB20C, ONESHOT=1, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR')
    first_out_wait(0xB20C)
    for k in (1, 2):
        u(TSRC=f'XI{k}', BIU='OPR_WR')
        bwait(p, 'OPR_VALID', True, opr_replay(f'XI{k}'))
    # FPU 7.5.4.2: an enabled exception is reported here, mid-instruction.
    L('out_end')
    u(FPSR='ACCRUE')
    u(SEQ='BR', COND='TRAP', TGT='out_trap')
    u(RESP='WR', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle')
    L('out_trap')
    u(FLAG='SET_EXC', RESP='WR', IMM=0x1D00, ORS='VEC',
      EXPECT='CMD' if p.model == 68882 else 'RESP', SEQ='JUMP', TGT='idle',
      comment='take mid-instruction exception' + (' (it persists)' if p.model == 68882 else ''))

    # =========================================================================
    # Tables
    # =========================================================================
    supp = {}
    for code in range(128):
        op = ALIAS.get(code, code)
        if op in OPS:
            supp[code] = 'arith_ok'
    p.table('t_supp', 7, supp, 'fline')
    p.table('t_op', 7, {c: OPS[ALIAS.get(c, c)] for c in range(128)
                        if ALIAS.get(c, c) in OPS}, 'fline')
    p.table('t_src', 3, {0: 'src_rr', 2: 'src_mem'}, 'fline')
    p.table('t_memsrc', 3, {7: 'movecr'}, 'op_mem_g')
    if p.model == 68882:
        p.table('t_cufmt', 3, {0: 'cu_l', 1: 'cu_s', 2: 'cu_x', 4: 'cu_w', 5: 'cu_d',
                               6: 'cu_b', 7: 'cu_movecr'}, 'illegal')
    p.table('t_memfmt', 3, {0: 'm_l', 1: 'm_s', 2: 'm_x', 3: 'm_p', 4: 'm_w', 5: 'm_d',
                            6: 'm_b'}, 'fline')
    p.table('t_outfmt', 3, {0: 'o_l', 1: 'o_s', 2: 'o_x', 3: 'o_p', 4: 'o_w', 5: 'o_d',
                            6: 'o_b', 7: 'o_pd'}, 'fline')
    packed_ucode.emit(p)
    trans_ucode.emit(p)
