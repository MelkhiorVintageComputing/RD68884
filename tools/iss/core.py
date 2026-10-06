# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The sequencer and datapath, one microinstruction per clock.

This is the executable definition of every microword field
(tools/ucode/fields.py) and the reference rtl/rd68884_seq.sv is checked
against, cycle by cycle (tools/iss/lockstep.py). Each step() is one clock:
every action reads the state as it was at the start of the clock and the
results land at its end, as in the RTL. Where two fields write the same
register in one clock, the later in this order wins: TDST, ASRC/BSRC, MOP,
EOP, SGN (the microcode never relies on it).

The BIU is not here. step() takes the BIU's outputs for this clock (a dict,
the names of rd68884_biu's *_o ports without the suffix) and returns what the
core drives back (the names of its *_i ports).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ucode'))
import fields  # noqa: E402

M72 = (1 << 72) - 1
M80 = (1 << 80) - 1
M88 = (1 << 88) - 1
M68 = (1 << 68) - 1
EXP_SPECIAL = 0x7FFF - 16383          # 16384: infinity and NaN
EXP_ZERO = -16383
ETEMP = 8

# Precisions, PSR values: (LSB position in the 72-bit mantissa, minimum
# normal exponent, maximum exponent). FPU 2.2.2, 6.1.4, 6.1.5, table 3-3.
P_X, P_S, P_D, P_SGLX = 0, 1, 2, 3
PREC = {P_X: (8, -16383, 16383), P_S: (48, -126, 127), P_D: (19, -1022, 1023),
        P_SGLX: (48, -16383, 16383)}
RN, RZ, RM, RP = 0, 1, 2, 3


def sext16(v):
    return v - 0x10000 if v & 0x8000 else v


def wrap18(v):
    v &= (1 << 18) - 1
    return v - (1 << 18) if v & (1 << 17) else v


def sext7(v):
    v &= 0x7F
    return v - 0x80 if v & 0x40 else v


def clamp_sa(v):
    return 0 if v < 0 else (127 if v > 127 else v)


def trap_vector(exc, en):
    """FPU 6.1.9 / 6.1.10, as tools/model/arith.py."""
    for bit, vec in ((0x80, 48), (0x40, 54), (0x20, 52), (0x10, 53), (0x08, 51), (0x04, 50)):
        if exc & en & bit:
            return vec
    if ((exc & 0x12) and en & 0x02) or (exc & en & 0x01):
        return 49
    return None


def aexc_of(exc):
    """FPU 2.3.4 / 6.1.10."""
    a = 0
    if exc & 0xE0:
        a |= 0x80
    if exc & 0x10:
        a |= 0x40
    if exc & 0x08 and exc & 0x02:
        a |= 0x20
    if exc & 0x04:
        a |= 0x10
    if exc & 0x13:
        a |= 0x08
    return a


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

    def __init__(self, sign=0, exp=EXP_ZERO, mant=0):
        self.sign, self.exp, self.mant = sign, exp, mant

    def copy(self):
        return FP(self.sign, self.exp, self.mant)

    def __eq__(self, o):
        return (self.sign, self.exp, self.mant) == (o.sign, o.exp, o.mant)

    def __repr__(self):
        return f'FP({self.sign},{self.exp},{self.mant:018X})'

    # Classification, FPU table 3-3.
    @property
    def special(self):
        return self.exp == EXP_SPECIAL

    @property
    def zero(self):
        return not self.special and self.mant == 0

    @property
    def inf(self):
        return self.special and self.mant & ((1 << 71) - 1) == 0

    @property
    def nan(self):
        return self.special and self.mant & ((1 << 71) - 1) != 0

    @property
    def snan(self):
        return self.nan and not (self.mant >> 70) & 1


def rf_word(a):
    """A working value as the register file stores it: {sign, exponent[17:0],
    mantissa[71:0]} (rtl/rd68884_regfile.sv)."""
    return (a.sign << 90) | ((a.exp & 0x3FFFF) << 72) | a.mant


def unpack_x(xi):
    w0, w1, w2 = xi
    return FP(w0 >> 31 & 1, ((w0 >> 16) & 0x7FFF) - 16383, (((w1 << 32) | w2) << 8) & M72)


def unpack_ieee(sign, e, f, ebits, fbits):
    """Single or double (FPU tables 3-1, 3-2) as a working value, not
    normalised: a denormal keeps the format's minimum exponent and J = 0."""
    bias = (1 << (ebits - 1)) - 1
    sh = 71 - fbits
    if e == (1 << ebits) - 1:
        # FPU 3.2.5: a NaN's fraction is left-justified with J set; an
        # infinity is the all-zero significand.
        return FP(sign, EXP_SPECIAL, ((1 << 71) | (f << sh)) if f else 0)
    if e == 0:
        return FP(sign, (1 - bias) if f else EXP_ZERO, f << sh)
    return FP(sign, e - bias, (1 << 71) | (f << sh))


def unpack_int(v, bits):
    if v >> (bits - 1) & 1:
        v -= 1 << bits
    if v == 0:
        return FP()
    return FP(int(v < 0), bits - 1, abs(v) << (72 - bits))


def pack_x(a):
    m = a.mant >> 8
    return [(a.sign << 31) | (((a.exp + 16383) & 0x7FFF) << 16),
            (m >> 32) & 0xFFFFFFFF, m & 0xFFFFFFFF]


def pack_ieee(a, ebits, fbits):
    """The working value, already rounded to the format, as its image."""
    bias = (1 << (ebits - 1)) - 1
    f = (a.mant >> (71 - fbits)) & ((1 << fbits) - 1)
    if a.special:
        e = (1 << ebits) - 1
    elif a.mant == 0 or not a.mant >> 71:
        e = 0
    else:
        e = (a.exp + bias) & ((1 << ebits) - 1)
    return (a.sign << (ebits + fbits)) | (e << fbits) | f


def cc_of(a):
    zero = (not a.special) and (a.mant >> 8) == 0
    return (a.sign << 3) | (int(zero) << 2) | (int(a.inf) << 1) | int(a.nan)


def int_bits(cmd):
    """FMOVE out: the integer format's width from the command word's
    destination format (FPU table 4-15: L 000, W 100, B 110)."""
    return {0: 32, 4: 16, 6: 8}.get(cmd >> 10 & 7, 32)


def place_int(v, bits):
    return (v & ((1 << bits) - 1)) << (32 - bits)


FP_NAN = FP(0, EXP_SPECIAL, ((1 << 64) - 1) << 8)
FP_ZERO = FP(0, EXP_ZERO, 0)


class Core:
    def __init__(self, words, crom=None, labels=None, model=68881):
        self.words = words
        self.model = model          # 68881 or 68882 (RD68885, doc/rd68885.md)
        if labels is not None:
            self.labels = labels
        if crom is None:
            import json
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   '..', '..', 'build', 'ucode.json')) as fh:
                crom = json.load(fh)['crom']
        self.crom = crom
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
        self.b = FP()
        self.c = 0
        self.rx = 0
        self.stk = 0
        self.stkb = 0
        self.sa = 0
        self.rinex = 0
        self.psr = P_X
        self.rmr = RN
        self.rfq = FP()
        self.qstk = 0
        self.rf = [FP() for _ in range(128)]
        self.exc_pend = 0
        self.null_state = 0
        self.pcode = 0
        self.ev_resp = self.ev_rsel = self.ev_save = 0
        self.restore_req_q = 0
        self.run = 0                # RD68885: a released instruction computes
        self.rfbq = FP()            # RD68885: the register file's second port, read

    def _rfb(self, b, n_rfbq):
        """The second port's write and read register, at the clock's end."""
        if b.get('rfb_we'):
            w = b['rfb_wd']
            e = (w >> 72) & 0x3FFFF
            self.rf[b['rfb_wa']] = FP(w >> 90 & 1, e - (1 << 18) if e >> 17 else e, w & M72)
        self.rfbq = n_rfbq

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
                   restore=0, restore_xfer=0, fpiar_we=0, fpiar=0, clear=0,
                   resp_xfer=0, cu_take=0, cu_load=0, cu_idx=0, cu_data=0, cu_resume=0,
                   relatch=0, relatch_word=0, relatch_cond=0,
                   apu_run=self.run, pcen=int((self.fpcr >> 8) & 0x7F != 0),
                   prec_x=int((self.fpcr >> 6) & 3 in (0, 3)), dcc_ack=0,
                   rfb_q=rf_word(self.rfbq))
        # RD68885: the conversion unit's port into the register file reads
        # what the clock starts with (a block RAM's read-first port).
        n_rfbq = self.rf[b['rfb_ra']].copy() if b.get('rfb_re') else self.rfbq

        # ---- traps: the RESET pin, an abort, a restore CIR write ----------
        restore_rise = b['restore_req'] and not self.restore_req_q
        self.restore_req_q = b['restore_req']
        trap = None
        if b['arch_reset']:
            trap = 'reset'
        elif b['abort']:
            trap = 'abort'
        elif b.get('abort_ab'):
            trap = 'abort_ab'
        elif restore_rise:
            trap = 'restore'
        # The sticky event copies (doc/microcode.md).
        ev_resp = self.ev_resp | b['resp_read']
        ev_rsel = self.ev_rsel | b['rsel_read']
        ev_save = self.ev_save | b['save_read']
        if trap:
            self.ev_resp, self.ev_rsel, self.ev_save = ev_resp, ev_rsel, ev_save
            self.run = 0
            self._rfb(b, n_rfbq)
            self.upc = self.entry(trap)
            return out

        cmd = self.cmd
        a, bb = self.a, self.b
        opclass, rx = cmd >> 13 & 7, cmd >> 10 & 7
        tf, bsun = predicate(cmd & 0x1F, self.fpsr >> 24 & 0xF)
        pcen = int((self.fpcr >> 8) & 0x7F != 0)
        exc, en = self.fpsr >> 8 & 0xFF, self.fpcr >> 8 & 0xFF
        vec = trap_vector(exc, en) or 49
        lsb, emin, emax = PREC[self.psr]
        imm = F('IMM')

        cond_name = F('COND')
        if cond_name == 'INT_OVF':
            nb = int_bits(cmd)
            cond = int((a.mant >> 8) > (1 << (nb - 1)) - 1 + a.sign)
        else:
            cond = {
                'TRUE': 1, 'CMD_PEND': b['cmd_pend'], 'CMD_COND': b['cmd_cond'],
                'OPW_VALID': b['opw_valid'], 'OPR_VALID': b['opr_valid'],
                'RESP_READ': ev_resp, 'RSEL_READ': ev_rsel, 'SAVE_REQ': b['save_req'],
                'SAVE_READ': ev_save, 'EXC_PEND': self.exc_pend,
                'NULL_STATE': self.null_state, 'CTR_ZERO': int(self.ctr == 0),
                'MASK_ZERO': int(self.mask == 0), 'TF': tf, 'BSUN': bsun,
                'BSUN_EN': self.fpcr >> 15 & 1,
                'REST_NULL': int(b['restore_word'] >> 8 == 0),
                'REST_IDLE': int(b['restore_word'] == (0x1F38 if self.model == 68882 else 0x1F18)),
                'REST_BUSY': int(b['restore_word'] == (0x1FD4 if self.model == 68882 else 0x1FB4)),
                'FLINE': int(opclass == 1 or ((opclass == 0 or (opclass == 2 and rx != 7))
                                              and cmd >> 6 & 1)),
                'REPORTS': int(opclass in (0, 2, 3)), 'PCEN': pcen,
                'LST_CR': cmd >> 12 & 1, 'LST_SR': cmd >> 11 & 1,
                'LST_IAR': int(cmd >> 10 & 1 or rx == 0), 'DYN_LIST': cmd >> 11 & 1,
                'PV': b['pv'], 'PEND_GEN': int(self.t >> 28 & 7 == 3),
                'PEND_COND': int(self.t >> 28 & 7 == 1), 'IS_COND': self.is_cond,
                'CMD_FMOVE': int(cmd & 0x7F == 0),
                'A_ZERO': a.zero, 'A_INF': a.inf, 'A_NAN': a.nan, 'A_SNAN': a.snan,
                'A_SIGN': a.sign, 'B_ZERO': bb.zero, 'B_INF': bb.inf, 'B_NAN': bb.nan,
                'B_SNAN': bb.snan, 'B_SIGN': bb.sign, 'A_J': a.mant >> 71 & 1,
                'AE_LT_EMIN': a.exp < emin, 'AE_GT_EMAX': a.exp > emax,
                'AE_LT_XMIN': a.exp < -16383, 'AE_LT_B': a.exp < bb.exp,
                'AE_GE_IMM': a.exp >= sext16(imm), 'AE_ODD': a.exp & 1,
                'RINEX': self.rinex, 'RX0': self.rx & 1,
                'OVF_INF': {RN: 1, RZ: 0, RM: a.sign, RP: 1 - a.sign}[self.rmr],
                'AE_EQ_B': a.exp == bb.exp, 'RX1': self.rx >> 1 & 1,
                'TRAP': trap_vector(exc, en) is not None,
                'SUPPRESS': (exc & en & 0x64) != 0, 'PREC_X': self.psr == P_X,
                'SIGN_XOR': a.sign != bb.sign, 'RM_MODE': self.rmr == RM,
                'PREC_SGLX': self.psr == P_SGLX,
                'P_SPECIAL': (self.xi[0] >> 28 & 7) == 7 and (self.xi[0] >> 16 & 0xFFF) == 0xFFF,
                'P_SE': self.xi[0] >> 30 & 1, 'K_GT17': sext7(self.mask) > 17, 'K_POS': sext7(self.mask) > 0,
                'Q_ODD': self.c & 1, 'A_POW2': a.mant == 1 << 71,
                'CU_READY': b.get('cu_ready', 0), 'CU_VALID': b.get('cu_valid', 0),
                'CU_MID': b.get('cu_mid', 0), 'PCODE': self.pcode,
            }[cond_name]
        cond = int(bool(cond)) ^ F('NEG')

        # ---- the transfer bus ------------------------------------------
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
        elif ts == 'SEQST':
            tbus = (self.stack[(self.sp - 1) & 3] << 20) | (self.mask << 12) | (self.rn << 9) \
                | (self.is_cond << 8) | (self.exc_pend << 7) | (ev_resp << 6) | (ev_rsel << 5)
        elif ts == 'CU':
            tbus = b['cu_save'][imm & 7]
        else:
            tbus = 0xFFFFFFFF

        # ---- next state, from the start-of-clock values --------------------
        n_fpcr, n_fpsr = self.fpcr, self.fpsr
        n_xi = list(self.xi)
        n_a, n_b, n_rfq, n_qstk = a.copy(), bb.copy(), self.rfq, self.qstk
        n_c, n_rx, n_stk, n_stkb = self.c, self.rx, self.stk, self.stkb
        n_sa, n_rinex, n_psr, n_rmr = self.sa, self.rinex, self.psr, self.rmr
        n_mask, n_rn, n_ctr = self.mask, self.rn, self.ctr
        n_cmd, n_is_cond = self.cmd, self.is_cond
        n_exc, n_null, n_pcode = self.exc_pend, self.null_state, self.pcode

        td = F('TDST')
        push_addr = None
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
        elif td == 'CU':
            out.update(cu_load=1, cu_idx=imm & 7, cu_data=tbus)
        elif td == 'SEQST':
            push_addr = (tbus >> 20) & 0xFFF
            n_mask, n_rn = (tbus >> 12) & 0xFF, (tbus >> 9) & 7
            n_is_cond, n_exc = (tbus >> 8) & 1, (tbus >> 7) & 1

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
                       resp_cond=int(rs == 'WRC'), resp_xfer=F('XFER') & 3)
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
        elif bo == 'CU_TAKE':
            out['cu_take'] = 1
            n_cmd, n_is_cond, n_xi = b['cu_word'], 0, list(b['cu_d'])
        elif bo == 'CU_RESUME':
            out['cu_resume'] = 1
        elif bo == 'RELATCH':
            out.update(relatch=1, relatch_word=cmd, relatch_cond=self.is_cond)

        # ---- the registers ------------------------------------------------
        rfop = F('RF')
        if rfop == 'CROM':
            off = {'IMM': 0, 'CMD': cmd & 0x7F, 'ELO': a.exp & 0x3F,
                   'EHI': (a.exp >> 6) & 0x7F, 'EXP': a.exp & 0x3FF,
                   'PSR': self.psr}[F('RFA')]
            stk, sign, e, m = self.crom[(imm + off) & 0x7FF]
            n_rfq, n_qstk = FP(sign, e, m), stk
        elif rfop != 'NONE':
            ra = F('RFA')
            addr = {'IMM': imm & 0x7F, 'RX': rx, 'RY': cmd >> 7 & 7, 'RN': self.rn,
                    'ETEMP': ETEMP, 'CTR': self.ctr & 0x7F, 'FPC': cmd & 7}[ra]
            if rfop == 'READ':
                n_rfq, n_qstk = self.rf[addr].copy(), 0
            else:
                self.rf[addr] = a.copy()

        asrc = F('ASRC')
        xi = self.xi
        if asrc == 'RFQ':
            n_a, n_stk = self.rfq.copy(), self.stk | self.qstk
        elif asrc == 'UNPACKX':
            n_a = unpack_x(xi)
        elif asrc == 'NAN':
            n_a = FP_NAN.copy()
        elif asrc == 'ZERO':
            n_a = FP_ZERO.copy()
        elif asrc == 'B':
            n_a, n_stk = bb.copy(), self.stkb
        elif asrc == 'UNPACKS':
            n_a = unpack_ieee(xi[0] >> 31, xi[0] >> 23 & 0xFF, xi[0] & 0x7FFFFF, 8, 23)
        elif asrc == 'UNPACKD':
            n_a = unpack_ieee(xi[0] >> 31, xi[0] >> 20 & 0x7FF,
                              ((xi[0] & 0xFFFFF) << 32) | xi[1], 11, 52)
        elif asrc == 'UNPACKL':
            n_a = unpack_int(xi[0], 32)
        elif asrc == 'UNPACKW':
            n_a = unpack_int(xi[0] >> 16, 16)
        elif asrc == 'UNPACKB':
            n_a = unpack_int(xi[0] >> 24, 8)
        bsrc = F('BSRC')
        if bsrc == 'RFQ':
            n_b = self.rfq.copy()
        elif bsrc == 'A':
            n_b, n_stkb = a.copy(), self.stk

        xo = F('XOP')
        if xo == 'PACKX':
            n_xi = pack_x(a)
        elif xo == 'PACKS':
            n_xi[0] = pack_ieee(a, 8, 23)
        elif xo == 'PACKD':
            v = pack_ieee(a, 11, 52)
            n_xi[0], n_xi[1] = v >> 32, v & 0xFFFFFFFF
        elif xo in ('PACKI', 'PACKNI', 'PACKSAT'):
            nb = int_bits(cmd)
            if xo == 'PACKI':
                m = a.mant >> 8
                n_xi[0] = place_int(-m if a.sign else m, nb)
            elif xo == 'PACKNI':
                n_xi[0] = ((a.mant >> (72 - nb)) & ((1 << nb) - 1)) << (32 - nb)
            else:
                n_xi[0] = place_int(-(1 << (nb - 1)) if a.sign else (1 << (nb - 1)) - 1, nb)
        elif xo in ('DIGL', 'DIGR', 'EDIGR', 'EDIG3'):
            v = ((xi[0] & 0xF) << 64) | (xi[1] << 32) | xi[2]
            dig = ((self.rx & 1) << 3) | (a.mant >> 69)
            ex = (xi[0] >> 16) & 0xFFF
            if xo == 'DIGL':
                v = (v << 4) & M68
            elif xo == 'DIGR':
                v = (dig << 64) | (v >> 4)
            elif xo == 'EDIGR':
                ex = (dig << 8) | (ex >> 4)
            if xo == 'EDIG3':
                n_xi[0] = (xi[0] & ~0xF000) | (dig << 12)
            else:
                n_xi[0] = (xi[0] & ~0x0FFF000F) | (ex << 16) | (v >> 64)
                n_xi[1], n_xi[2] = (v >> 32) & 0xFFFFFFFF, v & 0xFFFFFFFF
        elif xo == 'EDIGL':
            ex = (((xi[0] >> 16) & 0xFFF) << 4) & 0xFFF
            n_xi[0] = (xi[0] & ~0x0FFF0000) | (ex << 16)
        elif xo == 'PINIT':
            n_xi = [(a.sign << 31) | (int(a.exp < 0) << 30), 0, 0]

        # ---- the mantissa operation ------------------------------------------
        mop = F('MOP')
        if mop == 'ADD':
            s = a.mant + bb.mant
            n_a.mant, n_rx = s & M72, s >> 72
        elif mop == 'SUB':
            s = a.mant - bb.mant - self.stk
            n_a.mant, n_rx = s & M72, int(s < 0)
        elif mop == 'NEG':
            n_a.mant, n_rx = (-a.mant) & M72, 0
        elif mop in ('SHRA', 'SHRB'):
            src = a.mant if mop == 'SHRA' else bb.mant
            sh = self.sa
            lost = src & ((1 << sh) - 1)
            res = src >> sh
            n_stk = self.stk | int(lost != 0)
            if mop == 'SHRA':
                n_a.mant = res
            else:
                n_b.mant = res
        elif mop == 'NORM':
            if a.mant:
                lz = 71 - (a.mant.bit_length() - 1)
                n_a.mant, n_a.exp = (a.mant << lz) & M72, a.exp - lz
        elif mop == 'RSH1':
            if self.rx & 1:
                n_stk = self.stk | (a.mant & 1)
                n_a.mant, n_a.exp, n_rx = (1 << 71) | (a.mant >> 1), a.exp + 1, 0
        elif mop in ('ROUND', 'ROUNDX'):
            pos = 8 if mop == 'ROUNDX' else lsb
            m = a.mant
            low = m & ((1 << pos) - 1)
            g = m >> (pos - 1) & 1
            rest = (m & ((1 << (pos - 1)) - 1)) != 0 or self.stk
            inexact = low != 0 or self.stk
            q = m >> pos
            mode = self.rmr
            if mode == RN:
                inc = g and (rest or q & 1)
            elif mode == RM:
                inc = inexact and a.sign
            elif mode == RP:
                inc = inexact and not a.sign
            else:
                inc = 0
            q += int(bool(inc))
            e = a.exp
            if q >> (72 - pos):
                q >>= 1
                e += 1
            n_a.mant, n_a.exp = (q << pos) & M72, e
            n_rinex, n_stk = int(bool(inexact)), 0
        elif mop == 'CMPM':
            # The mantissas compared, through the adder: RX[0] A < B, RX[1] A = B.
            n_rx = (int(a.mant == bb.mant) << 1) | int(a.mant < bb.mant)
        elif mop == 'CLRQ':
            n_c, n_rx, n_stk = 0, 0, 0
        elif mop == 'MULSTEP':
            k = bb.mant & 0xFFFF
            n_c = ((self.c >> 16) + a.mant * k) & M88
            n_stk = self.stk | int(self.c & 0xFFFF != 0)
            # The 16 bits C drops go into B's top: after five steps
            # B[71:8] is the low 64 bits of the product (doc/microcode.md).
            n_b.mant = ((self.c & 0xFFFF) << 56) | (bb.mant >> 16)
        elif mop == 'MULFIN':
            # The product's top 72 bits; a NORM follows when C[79] is clear
            # (the bit it drops into STK is below any rounding's guard bit).
            c = self.c
            n_a.mant, n_a.exp = (c >> 8) & M72, a.exp + 1
            n_stk = self.stk | int(c & 0xFF != 0)
        elif mop == 'DIVSTEP':
            r = (self.rx << 72) | a.mant
            q = int(r >= bb.mant)
            if q:
                r -= bb.mant
            r <<= 1
            n_c = ((self.c << 1) | q) & M80
            n_a.mant, n_rx = r & M72, (r >> 72) & 0xF
        elif mop == 'DIVFIN':
            r = (self.rx << 72) | a.mant
            c = self.c
            n_a.mant = (c >> 2) & M72                # a NORM follows, as MULFIN's
            n_stk = self.stk | int(c & 3 != 0 or r != 0)
            n_rx = 0
        elif mop == 'SQSTEP':
            r = (self.rx << 72) | a.mant
            r2 = (r << 2) | (bb.mant >> 70 & 3)
            t = (self.c << 2) | 1
            q = int(r2 >= t)
            if q:
                r2 -= t
            assert r2 < 1 << 76
            n_b.mant = (bb.mant << 2) & M72
            n_c = ((self.c << 1) | q) & M80
            n_a.mant, n_rx = r2 & M72, (r2 >> 72) & 0xF
        elif mop == 'SQFIN':
            r = (self.rx << 72) | a.mant
            n_a.mant = self.c & M72
            n_stk = self.stk | int(r != 0)
            n_rx = 0
        elif mop == 'EXPF':
            v = a.exp
            if v == 0:
                n_a = FP_ZERO.copy()
            else:
                n_a.sign, n_a.mant, n_a.exp = int(v < 0), abs(v) << 54, 17
        elif mop == 'QUIET':
            n_a.mant = a.mant | (1 << 70)
        elif mop == 'INFA':
            n_a.exp, n_a.mant = EXP_SPECIAL, 0
        elif mop == 'ZEROM':
            n_a.exp, n_a.mant = EXP_ZERO, 0
        elif mop == 'MUL10':
            n_a.mant = (a.mant * 10) & M72
        elif mop == 'ADDDIG':
            n_a.mant = (a.mant + (xi[0] & 0xF)) & M72
        elif mop == 'QINC':
            n_c = (self.c & ~0x7F) | ((self.c + 1) & 0x7F)

        # ---- the exponent operation --------------------------------------------
        eop = F('EOP')
        if eop == 'ADDB':
            n_a.exp = wrap18(a.exp + bb.exp)
        elif eop == 'SUBB':
            n_a.exp = wrap18(a.exp - bb.exp)
        elif eop == 'ADDI':
            n_a.exp = wrap18(a.exp + sext16(imm))
        elif eop == 'LDI':
            n_a.exp = sext16(imm)
        elif eop == 'SA_AB':
            n_sa = clamp_sa(a.exp - bb.exp)
        elif eop == 'SA_IA':
            n_sa = clamp_sa(sext16(imm) - a.exp)
        elif eop == 'SA_IMM':
            n_sa = imm & 0x7F
        elif eop == 'SA_EMIN':
            n_sa = clamp_sa(emin - a.exp)
        elif eop == 'LDEMIN':
            n_a.exp = emin
        elif eop == 'HALF':
            n_a.exp = a.exp >> 1
        elif eop == 'LDB':
            n_a.exp = bb.exp
        elif eop == 'ADDBI':
            n = (bb.mant >> 8) & 0xFFFF
            n_a.exp = wrap18(a.exp + (-n if bb.sign else n))
        elif eop == 'NEGE':
            n_a.exp = wrap18(-a.exp)
        elif eop == 'EXP10':
            n_a.exp = wrap18(a.exp * 10 + ((xi[0] >> 24) & 0xF))
        elif eop == 'LOG10':
            # floor(E log10 2), exact for every exponent of the range
            # (doc/microcode.md): log10 2 * 2^32 = $4D104D42.07...
            n_a.exp = (a.exp * 0x4D104D42) >> 32
        elif eop == 'LDK':
            n_a.exp = sext7(self.mask)
        elif eop == 'SUBK':
            n_a.exp = wrap18(a.exp - sext7(self.mask))
        elif eop == 'LDM':
            n = (a.mant >> 8) & 0x3FFFF
            n_a.exp = wrap18(-n if a.sign else n)
        elif eop == 'LO6':
            n_a.exp = a.exp & 0x3F

        sg = F('SGN')
        if sg == 'NEG':
            n_a.sign = a.sign ^ 1
        elif sg == 'ABS':
            n_a.sign = 0
        elif sg == 'XOR':
            n_a.sign = a.sign ^ bb.sign
        elif sg == 'RMZ':
            n_a.sign = int(self.rmr == RM)
        elif sg == 'XI0':
            n_a.sign = xi[0] >> 31

        ps = F('PSR')
        if ps == 'FPCR':
            n_psr = {0: P_X, 1: P_S, 2: P_D, 3: P_X}[self.fpcr >> 6 & 3]
        elif ps in ('X', 'S', 'D', 'SGLX'):
            n_psr = {'X': P_X, 'S': P_S, 'D': P_D, 'SGLX': P_SGLX}[ps]
        rm = F('RMR')
        if rm == 'FPCR':
            n_rmr = self.fpcr >> 4 & 3
        elif rm in ('RZ', 'RN', 'RM'):
            n_rmr = {'RZ': RZ, 'RN': RN, 'RM': RM}[rm]

        fo = F('FPSR')
        if fo in ('CC', 'CC_CLREXC'):
            n_fpsr = (n_fpsr & ~0x0F000000) | (cc_of(a) << 24)
        if fo in ('CLREXC', 'CC_CLREXC'):
            n_fpsr &= ~0xFF00
        if fo == 'ACCRUE':
            n_fpsr |= aexc_of(exc)
        if fo == 'CCIMM':
            n_fpsr = (n_fpsr & ~0x0F000000) | ((imm & 0xF) << 24)
        if fo == 'QSIGN':
            n_fpsr = (n_fpsr & ~0x00800000) | ((a.sign ^ bb.sign) << 23)
        if fo == 'QBITS':
            n_fpsr = (n_fpsr & ~0x007F0000) | ((self.c & 0x7F) << 16)
        n_fpsr |= F('EXCSET') << 8

        co = F('CTR')
        if co == 'LOAD':
            n_ctr = imm
        elif co == 'DEC':
            n_ctr = (self.ctr - 1) & 0xFFFF
        elif co == 'LOADE':
            n_ctr = a.exp & 0xFFFF
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
        elif fl == 'SET_STK':
            n_stk = 1
        elif fl == 'CLR_STK':
            n_stk = 0
        n_run = self.run
        if fl == 'SET_RUN':
            n_run = 1
        elif fl == 'CLR_RUN':
            n_run = 0
        # RD68885: an FMOVE the CU completed while the APU computed has its
        # FPSR effects deferred; they land once the APU's instruction is over
        # (no released instruction computing), in program order.
        if b.get('dcc_v') and not self.run:
            n_fpsr &= ~0xFF00
            if b.get('dcc_cc_v'):
                n_fpsr = (n_fpsr & ~0x0F000000) | (b['dcc_cc'] << 24)
            out['dcc_ack'] = 1

        # ---- the next micro-address ----------------------------------------
        seq = F('SEQ')
        tgt = F('TGT')
        nxt = self.upc + 1
        if push_addr is not None:
            # TDST SEQST: the busy frame's resume address onto the stack.
            self.stack[self.sp] = push_addr
            self.sp = (self.sp + 1) & 3
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

        n_a.exp = wrap18(n_a.exp)
        self.fpcr, self.fpsr, self.xi = n_fpcr, n_fpsr, n_xi
        self.a, self.b, self.rfq, self.qstk = n_a, n_b, n_rfq, n_qstk
        self.c, self.rx, self.stk, self.stkb = n_c, n_rx, n_stk, n_stkb
        self.sa, self.rinex, self.psr, self.rmr = n_sa, n_rinex, n_psr, n_rmr
        self.mask, self.rn, self.ctr = n_mask, n_rn, n_ctr
        self.cmd, self.is_cond = n_cmd, n_is_cond
        self.exc_pend, self.null_state, self.pcode = n_exc, n_null, n_pcode
        self.run = n_run
        self._rfb(b, n_rfbq)
        if td == 'SEQST':
            ev_resp, ev_rsel = (tbus >> 6) & 1, (tbus >> 5) & 1
        self.ev_resp, self.ev_rsel, self.ev_save = ev_resp, ev_rsel, ev_save
        self.t = tbus
        self.upc = nxt & ((1 << fields.UADDR_BITS) - 1)
        return out
