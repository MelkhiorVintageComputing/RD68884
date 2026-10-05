# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The busy frame: FSAVE in the middle of an operand transfer (FPU 6.4.2.3,
7.5.4.3), and FRESTORE of one (doc/microcode.md).

Every wait for the main processor to move an operand is a loop that also
looks for a save request:

    w:    BR cond -> on;  BR !SAVE_REQ -> w
          CALL busy_save            the return address is the resume token
    stub: <replay>                  after FRESTORE: what the BIU held here
          JUMP w
    on:

busy_save sends the frame; FRESTORE pushes the token and RETs into the stub,
which writes the response (and the operand register) back as they were and
goes on waiting. The frame is our own layout -- the manual leaves it opaque
-- and FRESTORE receives it in the reverse of FSAVE's order:

    received first  SEQST (the token, MASK, RN, IS_COND, EXC_PEND, events)
                    the command word
                    ETEMP, three long words (through XI)
                    XI0, XI1, XI2 (the operand being transferred)
                    the BIU flags
    received last   36 long words of ones (45 in all: format word $1FB4)

The MC68882's (53 long words, $1FD4) puts the conversion unit's 8 long words
first (FPU 6.4.2: "at the top of the frame") and the BIU flags last, where
FPU figure 5-6's handler sets EXC PEND:

    received first  the CU's 8 long words (doc/model.md, FPU882)
                    SEQST, the command word, ETEMP, XI0..XI2
                    36 long words of ones
    received last   the BIU flags
"""

BUSY_PAD = 36


def bwait(p, cond, neg, replay):
    """A busy-capable wait: until COND (or !COND with neg), replaying the
    list of microword dicts `replay` after a restore."""
    L, u = p.L, p.u
    p.nbw = getattr(p, 'nbw', 0) + 1
    w, on = f'bw{p.nbw}', f'bw{p.nbw}_on'
    L(w)
    u(SEQ='BR', NEG=int(neg), COND=cond, TGT=on)
    u(SEQ='BR', NEG=1, COND='SAVE_REQ', TGT=w)
    u(SEQ='CALL', TGT='busy_save')
    if callable(replay):
        replay(w)                      # emits its own way back to w
    else:
        for f in replay:
            u(**f)
        u(SEQ='JUMP', TGT=w)
    L(on)


def opw_replay():
    """Waiting for an operand write: the primitive asking for it was read."""
    return [dict(RESP='WR', IMM=0x8900, EXPECT='OPW')]


def opr_replay(src):
    """Waiting for the operand register, holding `src`, to be read."""
    return [dict(RESP='WR', IMM=0x8900, EXPECT='OPR'), dict(TSRC=src, BIU='OPR_WR')]


def emit(p):
    L, u = p.L, p.u
    from program import FRAMES
    fr = FRAMES[p.model]

    # FSAVE with a transfer in progress. A protocol violation pending makes
    # an idle frame instead, as the model has it (doc/model.md).
    L('busy_save')
    u(SEQ='BR', COND='PV', TGT='save_first')
    u(CTR='LOAD', IMM=BUSY_PAD - 1)
    u(TSRC='IMM', IMM=fr['busy'], BIU='SAVE_WR', XFER=fr['busy_n'], comment='busy frame')
    if p.model == 68882:
        # The BIU flags at the frame's end, where FPU figure 5-6's handler
        # sets EXC PEND; the CU's words at its top (FPU 6.4.2).
        u(TSRC='FLAGS', BIU='OPR_WR', comment='highest address first')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    L('bs_pad')
    u(TSRC='ONES', BIU='OPR_WR')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(SEQ='LOOP', TGT='bs_pad')
    for src in (('XI2', 'XI1', 'XI0') if p.model == 68882 else ('FLAGS', 'XI2', 'XI1', 'XI0')):
        u(TSRC=src, BIU='OPR_WR')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(RF='READ', RFA='ETEMP')
    u(ASRC='RFQ')
    u(XOP='PACKX')
    for src in ('XI2', 'XI1', 'XI0'):
        u(TSRC=src, BIU='OPR_WR')
        u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(FLAG='PEND_CMD')
    u(TSRC='CMDW', BIU='OPR_WR', comment='the command word')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    u(TSRC='SEQST', BIU='OPR_WR', comment='the resume address, on the stack since the CALL')
    u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
    if p.model == 68882:
        for src, k in [('ONES', 0)] * 3 + [('CU', k) for k in range(4, -1, -1)]:
            u(TSRC=src, IMM=k, BIU='OPR_WR')
            u(SEQ='WAIT', NEG=1, COND='OPR_VALID')
        u(FLAG='CLR_EXC', TSRC='ONES', TDST='CU', IMM=0,
          comment='FPU 6.4.3: idle, no pending exceptions; the CU empty')
    else:
        u(FLAG='CLR_EXC', comment='FPU 6.4.3: idle, no pending exceptions')
    u(BIU='CLEAR', SEQ='JUMP', TGT='idle')

    # FRESTORE of a busy frame.
    L('rest_busy')
    u(TSRC='RESTW', BIU='RESTORE_WR', XFER=fr['busy_n'])
    if p.model == 68882:
        for dst, k in [('CU', k) for k in range(5)] + [('NONE', 0)] * 3:
            u(SEQ='WAIT', COND='OPW_VALID')
            u(BIU='OPW_ACK', TSRC='OPW', TDST=dst, IMM=k)
    u(SEQ='WAIT', COND='OPW_VALID')
    u(BIU='OPW_ACK', TSRC='OPW', TDST='SEQST', comment='pushes the resume address')
    u(SEQ='WAIT', COND='OPW_VALID')
    u(BIU='OPW_ACK', TSRC='OPW', TDST='CMD')
    for k in range(3):
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
    u(ASRC='UNPACKX')
    u(RF='WRITE', RFA='ETEMP')
    for k in range(3):
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST=f'XI{k}')
    if p.model == 68882:
        u(CTR='LOAD', IMM=BUSY_PAD - 1)
        L('rb_pad')
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', SEQ='LOOP', TGT='rb_pad')
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST='FLAGS')
    else:
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', TSRC='OPW', TDST='FLAGS', CTR='LOAD', IMM=BUSY_PAD - 1)
        L('rb_pad')
        u(SEQ='WAIT', COND='OPW_VALID')
        u(BIU='OPW_ACK', SEQ='LOOP', TGT='rb_pad')
    u(FLAG='CLR_NULL', SEQ='RET', comment='into the replay stub, then the wait')
