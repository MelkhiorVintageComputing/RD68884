# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The microcode.

M4 scope (doc/architecture.md): every coprocessor dialog, with the arithmetic
stubbed. Implemented: FMOVE.X FPm,FPn; FMOVE.X <ea>,FPn; FMOVE.X FPm,<ea>;
FMOVE/FMOVEM of the control registers; FMOVEM of the data registers, static
and dynamic lists; the conditionals; pre-instruction exceptions from a
pending exception; F-line for illegal command words; FSAVE/FRESTORE of null
and idle frames. Every other general instruction answers F-line until its
arithmetic exists (M5).

Comments cite the manual (FPU = MC68881UM_split). The golden model of every
dialog is tools/model/cpif.py; the field semantics are tools/iss/core.py.
"""

from asm import Program

ETEMP = 8          # register file entry of the exceptional operand


def build():
    p = Program()
    L, u = p.L, p.u

    # =========================================================================
    # Reset: power-on, the RESET pin, and FRESTORE of a null frame
    # FPU 9.9 / 6.4.2.1: FP0-FP7 nonsignalling NaNs, FPCR, FPSR, FPIAR zero.
    # =========================================================================
    L('reset')
    u(FLAG='RESET', TSRC='IMM', IMM=0, BIU='FPIAR_WR', comment='FPCR, FPSR, FPIAR = 0; null state')
    u(CTR='LOAD', IMM=7, ASRC='NAN')
    L('reset_fp')
    u(SEQ='LOOP', TGT='reset_fp', RF='WRITE', RFA='CTR', comment='FP7..FP0 = NaN')
    u(ASRC='ZERO')
    u(RF='WRITE', RFA='ETEMP')
    u(BIU='CLEAR', SEQ='JUMP', TGT='idle')

    L('illegal')
    u(SEQ='JUMP', TGT='reset', comment='no reachable path gets here')

    # A control CIR write, FPU 7.2.2: terminate, clear pending exceptions. The
    # BIU has already returned itself to idle.
    L('abort')
    u(FLAG='CLR_EXC', SEQ='JUMP', TGT='idle')

    # =========================================================================
    # Idle. A save request first: a command latched while the previous
    # instruction ran belongs in the frame (FPU 6.4.2.2, pending code 011).
    # =========================================================================
    L('idle')
    u(SEQ='BR', COND='SAVE_REQ', TGT='save')
    u(SEQ='BR', COND='CMD_PEND', TGT='cmd')
    u(SEQ='JUMP', TGT='idle')

    L('cmd')
    u(BIU='CMD_ACK', FLAG='CLR_NULL', comment='CMD, IS_COND <= the latched word')
    # Entered with CMD set, from here or from FRESTORE of a pending instruction.
    L('begin')
    u(SEQ='BR', COND='IS_COND', TGT='cond')
    u(SEQ='BR', COND='EXC_PEND', TGT='gen_pend')
    L('gen_go')
    u(SEQ='BR', COND='FLINE', TGT='fline')
    u(SEQ='DISP', IDX='OPCLASS', TGT='t_opclass')

    # FPU 6.4.2.2: a pending exception is reported by the next opclass 000,
    # 010 or 011 instruction or conditional, never by the others.
    L('gen_pend')
    u(SEQ='BR', NEG=1, COND='REPORTS', TGT='gen_go')
    L('pre_exc')
    u(RESP='WR', IMM=0x1C00, ORS='VEC', EXPECT='RESP', SEQ='JUMP', TGT='idle',
      comment='take pre-instruction exception; persists until the acknowledge')

    # FPU 6.1.11: an illegal command word. Table 7-7's $1C0B
    # (doc/manual-contradictions.md 5).
    L('fline')
    u(RESP='WR', IMM=0x1C0B, EXPECT='RESP', SEQ='JUMP', TGT='idle')

    # Wait for the first primitive to be read. A save request meanwhile means
    # nothing has been transferred: save an idle frame with the instruction
    # pending, and FRESTORE will start it again.
    L('wait_first')
    u(SEQ='BR', COND='RESP_READ', TGT='wait_first_done')
    u(SEQ='BR', COND='SAVE_REQ', TGT='save_first')
    u(SEQ='JUMP', TGT='wait_first')
    L('wait_first_done')
    u(SEQ='RET')

    # =========================================================================
    # Opclass 000, FPm to FPn. FPU 7.5.1.1, figure 7-17.
    # =========================================================================
    L('op_rr')
    u(SEQ='BR', NEG=1, COND='CMD_FMOVE', TGT='fline', comment='M4: FMOVE only')
    u(RESP='WR', IMM=0x0900, ORS='PC', EXPECT='CMD', RF='READ', RFA='RX',
      comment='null CA=0: release (PC if exceptions are enabled)')
    u(SEQ='WAIT', COND='RESP_READ', ASRC='RFQ', comment='the PC transfer, if asked, follows the read')
    u(RF='WRITE', RFA='RY', FPSR='CC_CLREXC')
    L('done')
    u(RESP='WRC', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle', comment='null CA=0 PF=1')

    # =========================================================================
    # Opclass 010, <ea> to FPn. FPU 7.5.1.2, figure 7-18.
    # =========================================================================
    L('op_mem')
    u(SEQ='BR', NEG=1, COND='CMD_FMOVE', TGT='fline', comment='M4: FMOVE only')
    u(SEQ='DISP', IDX='RX', TGT='t_memfmt')
    L('mem_x')
    u(RESP='WR', IMM=0x960C, ORS='PC', ONESHOT=1, EXPECT='OPW', comment='evaluate <ea>, 12 bytes')
    u(SEQ='CALL', TGT='wait_first')
    for k in range(3):
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
    u(RESP='WR', IMM=0x0900, EXPECT='CMD', ASRC='UNPACKX', comment='release')
    u(RF='WRITE', RFA='RY', FPSR='CC_CLREXC', SEQ='JUMP', TGT='done')

    # =========================================================================
    # Opclass 011, FPm to <ea>. FPU 7.5.1.3, figure 7-20.
    # =========================================================================
    L('op_out')
    u(SEQ='DISP', IDX='RX', TGT='t_outfmt')
    L('out_x')
    u(RESP='WR', IMM=0x8900, ORS='PC', ONESHOT=1, EXPECT='RESP', RF='READ', RFA='RY',
      comment='null CA=1 while converting (PC if enabled)')
    u(SEQ='CALL', TGT='wait_first')
    u(ASRC='RFQ')
    u(XOP='PACKX', FPSR='CLREXC')
    u(RESP='WR', IMM=0xB20C, ONESHOT=1, EXPECT='OPR', TSRC='XI0', BIU='OPR_WR',
      comment='evaluate <ea> and transfer out, 12 bytes')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    for k in (1, 2):
        u(TSRC=f'XI{k}', BIU='OPR_WR')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(RESP='WR', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle')

    # =========================================================================
    # Opclass 100/101, the control registers. FPU 7.5.1.4, figure 7-22; the
    # primitives of table 7-5 and 4-17. No PC, no exception reported.
    # =========================================================================
    L('ctl_in')
    u(SEQ='DISP', IDX='RX', TGT='t_ctlin')
    for name, prim in (('ci_iar', 0x9704), ('ci_1', 0x9504), ('ci_2', 0x9608), ('ci_3', 0x960C)):
        L(name)
        u(RESP='WR', IMM=prim, ONESHOT=1, EXPECT='OPW', SEQ='JUMP', TGT='ci_go')
    L('ci_go')
    u(SEQ='CALL', TGT='wait_first')
    u(SEQ='BR', NEG=1, COND='LST_CR', TGT='ci_sr', comment='FPCR first, then FPSR, then FPIAR')
    u(SEQ='WAIT', COND='OPW_VALID')
    u(BIU='OPW_ACK', TSRC='OPW', TDST='FPCR')
    L('ci_sr')
    u(SEQ='BR', NEG=1, COND='LST_SR', TGT='ci_iar2')
    u(SEQ='WAIT', COND='OPW_VALID')
    u(BIU='OPW_ACK', TSRC='OPW', TDST='FPSR')
    L('ci_iar2')
    u(SEQ='BR', NEG=1, COND='LST_IAR', TGT='ci_end')
    u(SEQ='WAIT', COND='OPW_VALID')
    u(BIU='OPW_ACK', TSRC='OPW')
    u(TSRC='T', BIU='FPIAR_WR')
    L('ci_end')
    u(RESP='WR', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle')

    L('ctl_out')
    u(SEQ='DISP', IDX='RX', TGT='t_ctlout')
    for name, prim in (('co_iar', 0xB304), ('co_1', 0xB104), ('co_2', 0xB208), ('co_3', 0xB20C)):
        L(name)
        u(RESP='WR', IMM=prim, ONESHOT=1, EXPECT='OPR', SEQ='JUMP', TGT='co_go')
    L('co_go')
    u(SEQ='CALL', TGT='wait_first')
    u(SEQ='BR', NEG=1, COND='LST_CR', TGT='co_sr')
    u(TSRC='FPCR', BIU='OPR_WR')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    L('co_sr')
    u(SEQ='BR', NEG=1, COND='LST_SR', TGT='co_iar2')
    u(TSRC='FPSR', BIU='OPR_WR')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    L('co_iar2')
    u(SEQ='BR', NEG=1, COND='LST_IAR', TGT='ci_end')
    u(TSRC='FPIAR', BIU='OPR_WR')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(SEQ='JUMP', TGT='ci_end')

    # =========================================================================
    # Opclass 110/111, FMOVEM of the data registers. FPU 7.5.1.5, figure 7-23.
    # =========================================================================
    for d in ('in', 'out'):
        L(f'mm_{d}')
        u(SEQ='BR', COND='DYN_LIST', TGT=f'mm_{d}_dyn')
        u(MASK='LOAD', SEQ='JUMP', TGT=f'mm_{d}_go')
        L(f'mm_{d}_dyn')
        u(RESP='WR', IMM=0x8C00, ORS='DN', ONESHOT=1, EXPECT='OPW',
          comment='transfer single main processor register: the list')
        u(SEQ='CALL', TGT='wait_first')
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST='MASK')
        L(f'mm_{d}_go')
        u(BIU='RSEL_WR', RESP='WR', IMM=0x810C if d == 'in' else 0xA10C, ONESHOT=1,
          EXPECT='RSEL', comment='transfer multiple coprocessor registers')
        u(SEQ='CALL', TGT='wait_first')
        u(SEQ='JUMP', TGT=f'mm_{d}_loop')
    L('mm_in_loop')
    u(SEQ='BR', COND='MASK_ZERO', TGT='mm_end')
    u(MASK='NEXT')
    for k in range(3):
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
    u(ASRC='UNPACKX')
    u(RF='WRITE', RFA='RN', SEQ='JUMP', TGT='mm_in_loop', comment='bit for bit (FPU 4.6)')
    L('mm_out_loop')
    u(SEQ='BR', COND='MASK_ZERO', TGT='mm_end')
    u(MASK='NEXT')
    u(RF='READ', RFA='RN')
    u(ASRC='RFQ')
    u(XOP='PACKX')
    for k in range(3):
        u(TSRC=f'XI{k}', BIU='OPR_WR')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(SEQ='JUMP', TGT='mm_out_loop')
    L('mm_end')
    u(SEQ='WAIT', COND='RSEL_READ', comment='an empty list: the select read still comes')
    u(RESP='WR', IMM=0x0802, EXPECT='CMD', SEQ='JUMP', TGT='idle')

    # =========================================================================
    # The conditionals. FPU 7.5.2, figure 7-24; BSUN, 6.1.1 and 7.5.4.4.
    # =========================================================================
    L('cond')
    u(SEQ='BR', COND='EXC_PEND', TGT='pre_exc')
    u(SEQ='BR', NEG=1, COND='BSUN', TGT='cond_eval')
    u(FLAG='BSUN', comment='EXC BSUN, AEXC IOP')
    u(SEQ='BR', NEG=1, COND='BSUN_EN', TGT='cond_eval')
    u(RESP='WR', IMM=0x5C30, EXPECT='RESP', SEQ='JUMP', TGT='idle', comment='take BSUN, with the PC')
    L('cond_eval')
    u(RESP='WR', IMM=0x0800, ORS='TF', EXPECT='CMD', SEQ='JUMP', TGT='idle', comment='null CA=0 TF')

    # =========================================================================
    # FSAVE. FPU 6.4.3, 7.5.3.1; frame figure 6-4 (doc/model.md).
    # =========================================================================
    L('save')
    u(SEQ='BR', COND='NULL_STATE', TGT='save_null')
    u(SEQ='BR', COND='CMD_PEND', TGT='save_latched')
    u(FLAG='PEND_NONE', SEQ='JUMP', TGT='save_frame')
    L('save_latched')
    u(BIU='CMD_ACK', comment='the latched instruction goes into the frame')
    L('save_first')
    u(FLAG='PEND_CMD')
    L('save_frame')
    u(RF='READ', RFA='ETEMP')
    u(ASRC='RFQ')
    u(XOP='PACKX', TSRC='IMM', IMM=0x1F18, BIU='SAVE_WR', XFER=6, comment='idle frame')
    for src in ('FLAGS', 'ONES', 'XI2', 'XI1', 'XI0', 'CMDW'):
        u(TSRC=src, BIU='OPR_WR', comment='highest address first' if src == 'FLAGS' else '')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(FLAG='CLR_EXC', comment='FPU 6.4.3: idle, no pending exceptions')
    u(BIU='CLEAR', SEQ='JUMP', TGT='idle')
    L('save_null')
    u(TSRC='IMM', IMM=0x0018, BIU='SAVE_WR', XFER=0, SEQ='JUMP', TGT='idle')

    # =========================================================================
    # FRESTORE. FPU 6.4.4, 7.5.3.2. Entered by trap when the restore CIR is
    # written, whatever was running.
    # =========================================================================
    L('restore')
    u(FLAG='CLR_EXC')
    u(SEQ='BR', COND='REST_NULL', TGT='rest_null')
    u(SEQ='BR', COND='REST_IDLE', TGT='rest_idle')
    u(TSRC='IMM', IMM=0x0218, BIU='RESTORE_WR', XFER=0, SEQ='JUMP', TGT='idle',
      comment='invalid: the main processor aborts (FPU 7.5.4.7)')
    L('rest_null')
    u(TSRC='RESTW', BIU='RESTORE_WR', XFER=0, SEQ='JUMP', TGT='reset')
    L('rest_idle')
    u(TSRC='RESTW', BIU='RESTORE_WR', XFER=6)
    for dst in ('CMD', 'XI0', 'XI1', 'XI2', 'NONE', 'FLAGS'):
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST=dst)
    u(ASRC='UNPACKX', FLAG='CLR_NULL')
    u(RF='WRITE', RFA='ETEMP')
    u(SEQ='BR', COND='PEND_GEN', TGT='rest_gen')
    u(SEQ='BR', COND='PEND_COND', TGT='rest_cond')
    u(BIU='CLEAR', SEQ='JUMP', TGT='idle')
    # A pending instruction starts again. No CLEAR: the response must stay
    # $8900 (set by the restore write) until its first primitive is there,
    # or a main processor re-reading it after RTE would see "done".
    L('rest_gen')
    u(FLAG='CLR_COND', SEQ='JUMP', TGT='begin', comment='start the pending instruction again')
    L('rest_cond')
    u(FLAG='SET_COND', SEQ='JUMP', TGT='begin')

    # =========================================================================
    # Jump tables
    # =========================================================================
    p.table('t_opclass', 3, {0: 'op_rr', 1: 'fline', 2: 'op_mem', 3: 'op_out',
                             4: 'ctl_in', 5: 'ctl_out', 6: 'mm_in', 7: 'mm_out'}, 'fline')
    p.table('t_memfmt', 3, {2: 'mem_x'}, 'fline')
    p.table('t_outfmt', 3, {2: 'out_x'}, 'fline')
    p.table('t_ctlin', 3, {0: 'ci_iar', 1: 'ci_iar', 2: 'ci_1', 4: 'ci_1',
                           3: 'ci_2', 5: 'ci_2', 6: 'ci_2', 7: 'ci_3'}, 'fline')
    p.table('t_ctlout', 3, {0: 'co_iar', 1: 'co_iar', 2: 'co_1', 4: 'co_1',
                            3: 'co_2', 5: 'co_2', 6: 'co_2', 7: 'co_3'}, 'fline')
    return p
