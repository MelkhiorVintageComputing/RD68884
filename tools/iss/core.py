# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The sequencer and datapath, one microinstruction per clock.

This is the executable definition of every microword field
(tools/ucode/fields.py) and the reference rtl/rd68884_seq.sv is checked
against, cycle by cycle (tools/iss/lockstep.py). Each step() is one clock:
every action reads the state as it was at the start of the clock and the
results land at its end, as in the RTL.

The BIU is not here. step() takes the BIU's outputs for this clock (a dict,
the names of rd68884_biu's *_o ports without the suffix) and returns what the
core drives back (the names of its *_i ports).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ucode'))
import fields  # noqa: E402

M72 = (1 << 72) - 1
EXP_SPECIAL = 0x7FFF - 16383          # 16384: infinity and NaN
ETEMP = 8


def trap_vector(exc, en):
    """FPU 6.1.9 / 6.1.10, as tools/model/arith.py."""
    for bit, vec in ((0x80, 48), (0x40, 54), (0x20, 52), (0x10, 53), (0x08, 51), (0x04, 50)):
        if exc & en & bit:
            return vec
    if ((exc & 0x12) and en & 0x02) or (exc & en & 0x01):
        return 49
    return None


def predicate(p, cc):
    """FPU 4.4: (true/false, BSUN). p is the 5-bit predicate (table 4-20
    note 3: the sixth bit is ignored), cc FPSR[27:24] = N Z I NAN."""
    n, z, nan = cc >> 3 & 1, cc >> 2 & 1, cc & 1
    eq = z
    gt = not (nan or z or n)
    lt = n and not (nan or z)
    un = nan
    tf = [False, eq, gt, gt or eq, lt, lt or eq, gt or lt, not un,
          un, un or eq, un or gt, un or gt or eq, un or lt, un or lt or eq,
          not eq, True][p & 0xF]
    return int(bool(tf)), int(bool(p & 0x10) and bool(nan))


class FP:
    """A working value: sign, 18-bit exponent (unbiased, as an int), 72-bit
    mantissa with the explicit integer bit at 71 (doc/microcode.md)."""
    __slots__ = ('sign', 'exp', 'mant')

    def __init__(self, sign=0, exp=-16383, mant=0):
        self.sign, self.exp, self.mant = sign, exp, mant

    def copy(self):
        return FP(self.sign, self.exp, self.mant)

    def __eq__(self, o):
        return (self.sign, self.exp, self.mant) == (o.sign, o.exp, o.mant)

    def __repr__(self):
        return f'FP({self.sign},{self.exp},{self.mant:018X})'


def unpack_x(xi):
    w0, w1, w2 = xi
    return FP(w0 >> 31 & 1, ((w0 >> 16) & 0x7FFF) - 16383, (((w1 << 32) | w2) << 8) & M72)


def pack_x(a):
    m = a.mant >> 8
    return [(a.sign << 31) | (((a.exp + 16383) & 0x7FFF) << 16),
            (m >> 32) & 0xFFFFFFFF, m & 0xFFFFFFFF]


def cc_of(a):
    special = a.exp == EXP_SPECIAL
    frac = (a.mant >> 8) & ((1 << 63) - 1)
    nan = special and frac != 0
    inf = special and frac == 0
    zero = (not special) and (a.mant >> 8) == 0
    return (a.sign << 3) | (int(zero) << 2) | (int(inf) << 1) | int(nan)


FP_NAN = FP(0, EXP_SPECIAL, ((1 << 64) - 1) << 8)
FP_ZERO = FP(0, -16383, 0)


class Core:
    def __init__(self, words):
        self.words = words
        self.lay = {n: (pos, w, vals) for n, pos, w, vals in fields.layout()[0]}
        self.reset_state()

    # ------------------------------------------------------------------ state
    def reset_state(self):
        """rst_n: every register's reset value, as in rd68884_seq.sv."""
        self.upc = self.entry('reset')
        self.stack = [0, 0, 0, 0]
        self.sp = 0
        self.ctr = 0
        self.t = 0
        self.mask = 0
        self.rn = 0
        self.cmd = 0
        self.is_cond = 0
        self.fpcr = 0
        self.fpsr = 0
        self.xi = [0, 0, 0]
        self.a = FP()
        self.rfq = FP()
        self.rf = [FP() for _ in range(128)]
        self.exc_pend = 0
        self.null_state = 0
        self.pcode = 0
        self.ev_resp = self.ev_rsel = self.ev_save = 0
        self.restore_req_q = 0

    def entry(self, name):
        return self.labels[name]

    labels = {}

    def f(self, w, name):
        pos, wid, vals = self.lay[name]
        v = (w >> pos) & ((1 << wid) - 1)
        if isinstance(vals, list):
            return vals[v] if v < len(vals) else None
        return v

    # ---------------------------------------------------------------- a clock
    def step(self, b):
        """One clock. b: the BIU's outputs. Returns the core's outputs."""
        w = self.words[self.upc]
        F = lambda n: self.f(w, n)  # noqa: E731
        out = dict(resp_we=0, resp=0, resp_oneshot=0, expect=0, resp_cond=0,
                   cmd_ack=0, opw_ack=0, opr_we=0, opr=0, rsel_we=0, rsel=0,
                   rsel_dir=0, save_we=0, save=0, save_xfer=0, restore_we=0,
                   restore=0, restore_xfer=0, fpiar_we=0, fpiar=0, clear=0)

        # ---- traps: the RESET pin, an abort, a restore CIR write ----------
        restore_rise = b['restore_req'] and not self.restore_req_q
        self.restore_req_q = b['restore_req']
        trap = None
        if b['arch_reset']:
            trap = 'reset'
        elif b['abort']:
            trap = 'abort'
        elif restore_rise:
            trap = 'restore'
        # The sticky event copies (doc/microcode.md).
        ev_resp = self.ev_resp | b['resp_read']
        ev_rsel = self.ev_rsel | b['rsel_read']
        ev_save = self.ev_save | b['save_read']
        if trap:
            self.ev_resp, self.ev_rsel, self.ev_save = ev_resp, ev_rsel, ev_save
            self.upc = self.entry(trap)
            return out

        cmd = self.cmd
        opclass, rx = cmd >> 13 & 7, cmd >> 10 & 7
        tf, bsun = predicate(cmd & 0x1F, self.fpsr >> 24 & 0xF)
        pcen = int((self.fpcr >> 8) & 0x7F != 0)
        vec = trap_vector(self.fpsr >> 8 & 0xFF, self.fpcr >> 8 & 0xFF) or 49

        cond = {
            'TRUE': 1, 'CMD_PEND': b['cmd_pend'], 'CMD_COND': b['cmd_cond'],
            'OPW_VALID': b['opw_valid'], 'OPR_VALID': b['opr_valid'],
            'RESP_READ': ev_resp, 'RSEL_READ': ev_rsel, 'SAVE_REQ': b['save_req'],
            'SAVE_READ': ev_save, 'EXC_PEND': self.exc_pend,
            'NULL_STATE': self.null_state, 'CTR_ZERO': int(self.ctr == 0),
            'MASK_ZERO': int(self.mask == 0), 'TF': tf, 'BSUN': bsun,
            'BSUN_EN': self.fpcr >> 15 & 1,
            'REST_NULL': int(b['restore_word'] >> 8 == 0),
            'REST_IDLE': int(b['restore_word'] == 0x1F18),
            'FLINE': int(opclass == 1 or ((opclass == 0 or (opclass == 2 and rx != 7))
                                          and cmd >> 6 & 1)),
            'REPORTS': int(opclass in (0, 2, 3)), 'PCEN': pcen,
            'LST_CR': cmd >> 12 & 1, 'LST_SR': cmd >> 11 & 1,
            'LST_IAR': int(cmd >> 10 & 1 or rx == 0), 'DYN_LIST': cmd >> 11 & 1,
            'PV': b['pv'], 'PEND_GEN': int(self.t >> 28 & 7 == 3),
            'PEND_COND': int(self.t >> 28 & 7 == 1), 'IS_COND': self.is_cond,
            'CMD_FMOVE': int(cmd & 0x7F == 0),
        }[F('COND')]
        cond = int(bool(cond)) ^ F('NEG')

        # ---- the transfer bus ------------------------------------------
        imm = F('IMM')
        ts = F('TSRC')
        if ts == 'T':
            tbus = self.t
        elif ts == 'OPW':
            tbus = b['opw_data']
        elif ts == 'IMM':
            tbus = imm
        elif ts == 'FPCR':
            tbus = self.fpcr
        elif ts == 'FPSR':
            tbus = self.fpsr
        elif ts == 'FPIAR':
            tbus = b['fpiar']
        elif ts == 'CMDW':
            tbus = ((cmd << 16) | 0xFFFF) if self.pcode else 0xFFFFFFFF
        elif ts in ('XI0', 'XI1', 'XI2'):
            tbus = self.xi[int(ts[2])]
        elif ts == 'FLAGS':
            code = (1 if self.is_cond else 3) if self.pcode else 7
            tbus = (b['pv'] << 31) | (code << 28) | ((1 - self.exc_pend) << 27) \
                | (1 << 26) | 0xFFFF
        elif ts == 'RESTW':
            tbus = b['restore_word']
        else:
            tbus = 0xFFFFFFFF

        # ---- next state, from the start-of-clock values --------------------
        n_fpcr, n_fpsr = self.fpcr, self.fpsr
        n_xi = list(self.xi)
        n_a, n_rfq = self.a, self.rfq
        n_mask, n_rn, n_ctr = self.mask, self.rn, self.ctr
        n_cmd, n_is_cond = self.cmd, self.is_cond
        n_exc, n_null, n_pcode = self.exc_pend, self.null_state, self.pcode

        td = F('TDST')
        if td == 'FPCR':
            n_fpcr = tbus & 0xFFFF
        elif td == 'FPSR':
            n_fpsr = tbus & 0x0FFFFFF8
        elif td == 'MASK':
            n_mask = tbus & 0xFF
        elif td in ('XI0', 'XI1', 'XI2'):
            n_xi[int(td[2])] = tbus
        elif td == 'CMD':
            n_cmd = tbus >> 16
        elif td == 'FLAGS':
            n_exc = 1 - (tbus >> 27 & 1)

        # The response and the BIU.
        rs = F('RESP')
        if rs != 'NONE':
            ors = F('ORS')
            v = imm
            if ors in ('PC', 'DNPC'):
                v |= pcen << 14
            if ors == 'TF':
                v |= tf
            if ors == 'VEC':
                v |= vec
            if ors in ('DN', 'DNPC'):
                v |= cmd >> 4 & 7
            out.update(resp_we=1, resp=v, resp_oneshot=F('ONESHOT'),
                       expect=fields.enum('EXPECT', F('EXPECT')),
                       resp_cond=int(rs == 'WRC'))
            ev_resp = 0
        bo = F('BIU')
        if bo == 'CMD_ACK':
            out['cmd_ack'] = 1
            n_cmd, n_is_cond = b['cmd_word'], b['cmd_cond']
        elif bo == 'OPW_ACK':
            out['opw_ack'] = 1
        elif bo == 'OPR_WR':
            out.update(opr_we=1, opr=tbus)
        elif bo == 'RSEL_WR':
            out.update(rsel_we=1, rsel=self.mask, rsel_dir=cmd >> 13 & 1)
            ev_rsel = 0
        elif bo == 'SAVE_WR':
            out.update(save_we=1, save=tbus & 0xFFFF, save_xfer=F('XFER'))
            ev_save = 0
        elif bo == 'RESTORE_WR':
            out.update(restore_we=1, restore=tbus & 0xFFFF, restore_xfer=F('XFER'))
        elif bo == 'FPIAR_WR':
            out.update(fpiar_we=1, fpiar=tbus)
        elif bo == 'CLEAR':
            out['clear'] = 1

        # The registers.
        rfop = F('RF')
        if rfop != 'NONE':
            ra = F('RFA')
            addr = {'IMM': imm & 0x7F, 'RX': rx, 'RY': cmd >> 7 & 7, 'RN': self.rn,
                    'ETEMP': ETEMP, 'CTR': self.ctr & 0x7F}[ra]
            if rfop == 'READ':
                n_rfq = self.rf[addr].copy()
            else:
                self.rf[addr] = self.a.copy()
        asrc = F('ASRC')
        if asrc == 'RFQ':
            n_a = self.rfq.copy()
        elif asrc == 'UNPACKX':
            n_a = unpack_x(self.xi)
        elif asrc == 'NAN':
            n_a = FP_NAN.copy()
        elif asrc == 'ZERO':
            n_a = FP_ZERO.copy()
        if F('XOP') == 'PACKX':
            n_xi = pack_x(self.a)
        fo = F('FPSR')
        if fo in ('CC', 'CC_CLREXC'):
            n_fpsr = (n_fpsr & ~0x0F000000) | (cc_of(self.a) << 24)
        if fo in ('CLREXC', 'CC_CLREXC'):
            n_fpsr &= ~0xFF00

        co = F('CTR')
        if co == 'LOAD':
            n_ctr = imm
        elif co == 'DEC':
            n_ctr = (self.ctr - 1) & 0xFFFF
        mo = F('MASK')
        if mo == 'LOAD':
            n_mask = cmd & 0xFF
        elif mo == 'NEXT' and self.mask:
            bit = self.mask.bit_length() - 1
            n_rn = (7 - bit) if cmd >> 12 & 1 else bit
            n_mask = self.mask & ~(1 << bit)

        fl = F('FLAG')
        if fl == 'SET_EXC':
            n_exc = 1
        elif fl == 'CLR_EXC':
            n_exc = 0
        elif fl == 'SET_NULL':
            n_null = 1
        elif fl == 'CLR_NULL':
            n_null = 0
        elif fl == 'BSUN':
            n_fpsr |= 0x8000 | 0x80
        elif fl == 'RESET':
            n_fpcr, n_fpsr, n_exc, n_null = 0, 0, 0, 1
        elif fl == 'PEND_NONE':
            n_pcode = 0
        elif fl == 'PEND_CMD':
            n_pcode = 1
        elif fl == 'SET_COND':
            n_is_cond = 1
        elif fl == 'CLR_COND':
            n_is_cond = 0

        # ---- the next micro-address ----------------------------------------
        seq = F('SEQ')
        tgt = F('TGT')
        nxt = self.upc + 1
        if seq == 'JUMP':
            nxt = tgt
        elif seq == 'CALL':
            self.stack[self.sp] = self.upc + 1
            self.sp = (self.sp + 1) & 3
            nxt = tgt
        elif seq == 'RET':
            self.sp = (self.sp - 1) & 3
            nxt = self.stack[self.sp]
        elif seq == 'BR':
            nxt = tgt if cond else self.upc + 1
        elif seq == 'WAIT':
            nxt = self.upc + 1 if cond else self.upc
        elif seq == 'DISP':
            idx = {'OPCLASS': opclass, 'RX': rx, 'OPMODE': cmd & 0x7F}[F('IDX')]
            nxt = tgt | idx
        elif seq == 'LOOP':
            if self.ctr != 0:
                n_ctr = (self.ctr - 1) & 0xFFFF
                nxt = tgt

        self.fpcr, self.fpsr, self.xi = n_fpcr, n_fpsr, n_xi
        self.a, self.rfq = n_a, n_rfq
        self.mask, self.rn, self.ctr = n_mask, n_rn, n_ctr
        self.cmd, self.is_cond = n_cmd, n_is_cond
        self.exc_pend, self.null_state, self.pcode = n_exc, n_null, n_pcode
        self.ev_resp, self.ev_rsel, self.ev_save = ev_resp, ev_rsel, ev_save
        self.t = tbus
        self.upc = nxt & ((1 << fields.UADDR_BITS) - 1)
        return out
