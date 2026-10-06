# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""rd68884_biu's registers at the level of whole CIR accesses.

The rules are those of rtl/rd68884_biu.sv (which sim/tb/biu_tb.sv checks at
the pins, for every port size). Here an access is one call: access() says
whether it can complete now (an operand access the sequencer is not ready
for waits, FPU 10.5) and, when it does, applies its effects and raises the
one-clock events the core sees on the next clock.

A response read that arrives while a just-latched command has been taken by
the sequencer but not answered waits up to RESP_HOLD clocks for the answer,
as in the RTL.

The MC68882 build (model=68882, doc/rd68885.md) adds the conversion unit
(CU): while the sequencer computes a released instruction (the core's
apu_run), the CU takes the next one, answers its first primitive itself,
collects its operand, and holds it until the sequencer takes it (CU_TAKE).
Some FMOVEs it completes itself, without the APU (stage B, FPU table 5-5:
cu_fm_decide). It adds the mandatory PC transfer (EXP_PC, FPU 6.1.12), the CA = 0 transfers
(the expectation returns to a command at the last operand, FPU 7.5.1), and
the control CIR's AB and XA bits (FPU 7.2.2).
"""

RESP_HOLD = 20                 # rtl/rd68884_biu.sv's default

EXP_CMD, EXP_RESP, EXP_OPW, EXP_OPR, EXP_RSEL, EXP_PC = range(6)
CIR_RESPONSE, CIR_CONTROL, CIR_SAVE, CIR_RESTORE = 0x00, 0x02, 0x04, 0x06
CIR_COMMAND, CIR_CONDITION, CIR_OPERAND, CIR_REGSEL = 0x0A, 0x0E, 0x10, 0x14
CIR_INSTADDR = 0x18

ONES = 0xFFFFFFFF

# Source formats (FPU table 4-14): L S X P W D B; their lengths in bytes.
FMT_BYTES = (4, 4, 12, 12, 2, 8, 1)


def cu_takes(word):
    """FPU 5.1.1.2: what the CU takes while the APU computes. Register to
    register (FMOVECR too) and opclass 010 with a B, W, L, S, D or X source;
    not an illegal command word (FPU 6.1.11)."""
    opclass, rx = word >> 13 & 7, word >> 10 & 7
    if opclass == 0:
        return not word >> 6 & 1
    if opclass == 2:
        return rx == 7 or (not word >> 6 & 1 and rx != 3)
    return False


def cu_first(word, pcen):
    """The CU's first primitive and the long words to follow: figure 7-17
    (register to register), 7-19 (S, D, X, CA = 0), 7-18 (B, W, L)."""
    opclass, rx = word >> 13 & 7, word >> 10 & 7
    pc = pcen << 14
    if opclass == 0 or rx == 7:
        return 0x0900 | pc, 0
    n = FMT_BYTES[rx]
    vea = 0x0500 if n <= 4 else 0x0600
    if rx in (1, 5, 2):                          # S, D, X
        return 0x1000 | pc | vea | n, (n + 3) // 4
    return 0x9000 | pc | vea | n, 1              # B, W, L


FM_NONE, FM_IN, FM_RR, FM_XOUT = range(4)
EXP_SPECIAL, EXP_ZERO = 16384, -16383          # tools/iss/core.py
M72 = (1 << 72) - 1


def fm_kind(word):
    """FPU table 5-5's fully concurrent FMOVEs: <ea>,FPn from S, D or X;
    FPm,FPn; FPm,<ea> to X."""
    opclass, rx = word >> 13 & 7, word >> 10 & 7
    if opclass == 2 and rx in (1, 5, 2) and word & 0x7F == 0:
        return FM_IN
    if opclass == 0 and word & 0x7F == 0:
        return FM_RR
    if opclass == 3 and rx == 2:
        return FM_XOUT
    return FM_NONE


def apu_dests(word):
    """The registers the APU's instruction writes: FPn, and FSINCOS's FPc."""
    if word is None or word >> 13 & 7 not in (0, 2):
        return set()
    d = {word >> 7 & 7}
    if word & 0x78 == 0x30:
        d.add(word & 7)
    return d


def rf_word(sign, exp, mant):
    return (sign << 90) | ((exp & 0x3FFFF) << 72) | mant


def rf_fields(w):
    e = (w >> 72) & 0x3FFFF
    return w >> 90 & 1, e - (1 << 18) if e >> 17 else e, w & M72


def fm_ok(sign, exp, mant):
    """Table 5-5 (b): a normal, zero or infinite value -- in its canonical
    form, which the APU's FMOVE would leave unchanged."""
    if exp == EXP_SPECIAL:
        return mant == 0
    if mant == 0:
        return exp == EXP_ZERO
    return bool(mant >> 71 & 1)


def fm_cc(sign, exp, mant):
    """FPSR's condition codes (N Z I NAN) of such a value."""
    return (sign << 3) | (int(exp != EXP_SPECIAL and mant == 0) << 2) | (int(exp == EXP_SPECIAL) << 1)


def fm_unpack(word, d):
    """An S, D or X operand in the register file's form, or None if table
    5-5 (b) sends it to the APU (a NaN, denormal or unnormal)."""
    rx = word >> 10 & 7
    if rx == 2:
        s, e, m = d[0] >> 31, d[0] >> 16 & 0x7FFF, (d[1] << 32) | d[2]
        if e == 0x7FFF:
            return (s, EXP_SPECIAL, 0) if m & ((1 << 63) - 1) == 0 else None
        if e == 0:
            return (s, EXP_ZERO, 0) if m == 0 else None
        return (s, e - 16383, m << 8) if m >> 63 else None
    eb, fb = (8, 23) if rx == 1 else (11, 52)
    v = d[0] if rx == 1 else (d[0] << 32) | d[1]
    s, e, f = v >> (eb + fb), v >> fb & ((1 << eb) - 1), v & ((1 << fb) - 1)
    if e == (1 << eb) - 1:
        return (s, EXP_SPECIAL, 0) if f == 0 else None
    if e == 0:
        return (s, EXP_ZERO, 0) if f == 0 else None
    return (s, e - ((1 << (eb - 1)) - 1), (1 << 71) | (f << (71 - fb)))


def is_fp_take(resp):
    """A take-exception primitive for a floating-point exception (vectors
    48-54): the MC68882 keeps reporting it past XA (FPU 7.2.2, 7.4.2.5)."""
    return (resp >> 9 & 0x1F) == 0b01110 and 48 <= (resp & 0xFF) <= 54


class Biu:
    def __init__(self, resp_hold=RESP_HOLD, model=68881):
        self.resp_hold = resp_hold
        self.model = model          # 68882: the CU, the PC, AB and XA
        self.reset()

    def reset(self):
        self.resp, self.oneshot, self.expect, self.expect_next = 0x0802, 0, EXP_CMD, EXP_CMD
        self.pv = 0
        self.cmd_pend, self.cmd_cond, self.cmd_word = 0, 0, 0
        self.fresh, self.held = 0, 0
        self.opw_valid, self.opw_data = 0, 0
        self.opr_valid, self.opr = 0, 0xFFFFFFFF
        self.rsel, self.rsel_dir = 0, 0
        self.save_valid, self.save, self.save_req, self.save_xfer = 0, 0, 0, 0
        self.restore_req, self.restore_valid, self.restore_word = 0, 0, 0
        self.restore, self.restore_xfer = 0xFFFF, 0
        self.xfer = 0
        self.fpiar = 0
        self.arch_reset = 0
        self.ev = {}
        # The MC68882's.
        self.apu_run, self.pcen = 0, 0          # from the core, last clock
        self.resp_xfer, self.ca0 = 0, 0         # CA = 0: long words to come
        self.pc_after, self.pc_cu = EXP_CMD, 0  # after the PC; whose it is
        self.prec_x, self.rfb_q = 1, 0          # from the core, this clock
        self.apu_word = None                    # the APU's instruction
        self.dcc_v, self.dcc_cc_v, self.dcc_cc = 0, 0, 0
        self._fmdec = {}
        self.cu_clear()

    def cu_clear(self):
        self.cu_v, self.cu_word, self.cu_rel, self.cu_pcv = 0, 0, 0, 0
        self.cu_pc, self.cu_d, self.cu_cnt, self.cu_bwl = ONES, [ONES] * 3, 0, 0
        # Stage B: which fully concurrent FMOVE it is; the APU path chosen
        # instead (Table 5-5 (b)); FPm read; an extended store's operand out.
        self.cu_fm, self.cu_nofm, self.cu_rd, self.cu_xrd = FM_NONE, 0, 0, 0

    # ---------------------------------------------------- the core's view
    def cu_dlg(self):
        """The CU owns the response: its dialog is not over."""
        return self.cu_v and not self.cu_rel

    def cu_ready(self):
        return int(bool(self.cu_v and self.cu_cnt == 0 and (self.cu_rel or self.cu_bwl)
                        and not (self.expect == EXP_PC and self.pc_cu)
                        and not (self.cu_fm in (FM_IN, FM_RR) and not self.cu_nofm
                                 and self._fm_conds())))

    # ------------------------------------------------- stage B: FMOVE in the CU
    def _fm_conds(self):
        """FPU table 5-5: the CU completes the FMOVE itself while the APU
        computes, if the rounding precision is extended (c) and no register
        conflicts with the APU instruction's destination (a, f). MODEL
        CHOICE (doc/model.md): and no exception is enabled, so the older
        instruction cannot trap after the younger has completed."""
        w = self.cu_word
        dests = apu_dests(self.apu_word)
        ok = self.apu_run and not self.pcen and (self.prec_x or self.cu_fm == FM_XOUT)
        if self.cu_fm == FM_RR:
            ok = ok and (w >> 10 & 7) not in dests
        if self.cu_fm in (FM_IN, FM_RR, FM_XOUT):
            ok = ok and (w >> 7 & 7) not in dests
        return bool(ok)

    def _cu_takes(self, word):
        if cu_takes(word):
            return True
        # The extended store, only when it will complete in the CU.
        return fm_kind(word) == FM_XOUT and self.apu_run and not self.pcen and \
            (word >> 7 & 7) not in apu_dests(self.apu_word)

    def _fm_decide(self):
        """This clock's step for a fully concurrent FMOVE held in the CU,
        from the state at the start of the clock (rtl/rd68884_biu.sv)."""
        if self.model != 68882 or not self.cu_v or self.cu_fm == FM_NONE or self.cu_nofm:
            return {}
        k, w = self.cu_fm, self.cu_word
        if self.cu_cnt or self.cu_xrd or (self.expect == EXP_PC and self.pc_cu):
            return {}
        if k in (FM_IN, FM_RR) and not self.cu_rel:
            return {}
        if not self._fm_conds():
            return {'relatch': 1} if k == FM_XOUT else {}
        fn = w >> 7 & 7
        if k == FM_IN:
            v = fm_unpack(w, self.cu_d)
            if v is None:
                return {'nofm': 1}
            return {'we': 1, 'wa': fn, 'wd': rf_word(*v), 'cc': fm_cc(*v), 'done': 1}
        if not self.cu_rd:
            return {'re': 1, 'ra': (w >> 10 & 7) if k == FM_RR else fn, 'rd': 1}
        v = rf_fields(self.rfb_q)
        if not fm_ok(*v):
            return {'nofm': 1} if k == FM_RR else {'relatch': 1}
        if k == FM_RR:
            return {'we': 1, 'wa': fn, 'wd': self.rfb_q, 'cc': fm_cc(*v), 'done': 1}
        s_, e, m = v
        return {'xout': [(s_ << 31) | (((e + 16383) & 0x7FFF) << 16),
                         (m >> 40) & ONES, (m >> 8) & ONES]}

    def _fm_apply(self, d):
        if not d:
            return
        if d.get('rd'):
            self.cu_rd = 1
        if d.get('nofm'):
            self.cu_nofm = 1
        if 'cc' in d or 'xout' in d:
            # FPSR in program order: the FMOVE's condition codes (an extended
            # store has none) and its cleared exception byte wait for the
            # APU's instruction to end (the core applies them, dcc_ack).
            self.dcc_v = 1
            if 'cc' in d:
                self.dcc_cc_v, self.dcc_cc = 1, d['cc']
        if d.get('done'):
            self.cu_clear()
        if d.get('relatch'):
            self.cmd_pend, self.cmd_word, self.cmd_cond, self.fresh = 1, self.cu_word, 0, 0
            self.cu_clear()
        if 'xout' in d:
            # Figure 7-21: evaluate <ea> and transfer data, CA = 0.
            self.cu_d, self.cu_cnt, self.cu_xrd = d['xout'], 3, 1
            self.resp, self.oneshot = 0x320C, 1
            self.expect, self.expect_next = EXP_RESP, EXP_OPR

    def cu_words(self):
        """The CU's eight long words in a frame (doc/model.md, FPU882)."""
        if not self.cu_v:
            return [ONES] * 8
        w0 = (self.cu_word << 16) | (self.cu_rel << 14) | (self.cu_pcv << 13) | 0x1F00 | self.cu_cnt
        return [w0, self.cu_pc if self.cu_pcv else ONES] + list(self.cu_d) + [ONES] * 3

    def outputs(self):
        e = self.ev
        d = self._fmdec = self._fm_decide()
        return dict(arch_reset=self.arch_reset, cmd_pend=self.cmd_pend,
                    cmd_cond=self.cmd_cond, cmd_word=self.cmd_word,
                    opw_valid=self.opw_valid, opw_data=self.opw_data,
                    opr_valid=self.opr_valid, save_req=self.save_req,
                    restore_req=self.restore_req, restore_word=self.restore_word,
                    fpiar=self.fpiar, pv=self.pv,
                    resp_read=e.get('resp', 0), rsel_read=e.get('rsel', 0),
                    save_read=e.get('save', 0), abort=e.get('abort', 0),
                    abort_ab=e.get('abort_ab', 0),
                    cu_ready=self.cu_ready(), cu_valid=self.cu_v, cu_mid=int(self.cu_dlg()),
                    cu_word=self.cu_word, cu_d=list(self.cu_d), cu_save=self.cu_words(),
                    rfb_re=d.get('re', 0), rfb_ra=d.get('ra', 0), rfb_we=d.get('we', 0),
                    rfb_wa=d.get('wa', 0), rfb_wd=d.get('wd', 0),
                    dcc_v=self.dcc_v, dcc_cc_v=self.dcc_cc_v, dcc_cc=self.dcc_cc)

    def from_core(self, o):
        """The core's writes, at the end of a clock (after its events)."""
        self.ev = {}
        # WRC: the APU's end of instruction stands aside while the BIU is in
        # another instruction's dialog: a latched command, the CU's, or a PC
        # the main processor still owes (rd68884_biu resp_cond_i).
        if o['resp_we'] and not (o['resp_cond'] and (self.cmd_pend or self.cu_dlg()
                                                     or self.expect == EXP_PC)):
            self.fresh = 0
            self.resp = o['resp']
            self.oneshot = o['resp_oneshot']
            self.resp_xfer = o.get('resp_xfer', 0)
            # With a PC owed, the PC still comes first: the write sets what
            # is expected after it.
            owed = self.model == 68882 and self.expect == EXP_PC
            if o['resp_oneshot']:
                if owed:
                    self.pc_after = EXP_RESP
                else:
                    self.expect = EXP_RESP
                self.expect_next = o['expect']
            elif owed:
                self.pc_after = o['expect']
            else:
                self.expect = o['expect']
                if self.model == 68882 and self.resp_xfer:
                    self.ca0 = self.resp_xfer     # a CA = 0 transfer resumed after FRESTORE
        if o['cmd_ack']:
            self.cmd_pend = 0
        if o['opw_ack']:
            self.opw_valid = 0
        if o['opr_we']:
            self.opr, self.opr_valid = o['opr'], 1
        if o['rsel_we']:
            self.rsel, self.rsel_dir = o['rsel'], o['rsel_dir']
        if o['save_we']:
            self.save, self.save_valid, self.save_req = o['save'], 1, 0
            self.save_xfer = o['save_xfer']
        if o['restore_we']:
            self.restore, self.restore_valid, self.restore_req = o['restore'], 1, 0
            self.restore_xfer = o['restore_xfer']
        if o['fpiar_we']:
            self.fpiar = o['fpiar']
        if o['clear']:
            self.pv = 0
            self.ca0 = 0
            if not self.cmd_pend:
                self.fresh = 0
                self.resp, self.oneshot, self.expect = 0x0802, 0, EXP_CMD
        if self.model == 68882:
            self._from_core82(o)

    def _from_core82(self, o):
        if o['cmd_ack']:
            self.apu_word = self.cmd_word
        if o.get('dcc_ack'):
            self.dcc_v = self.dcc_cc_v = 0
        if o.get('cu_take'):
            self.apu_word = self.cu_word
            # The sequencer takes the CU's instruction (FPU 5.1.1.2). Its PC
            # becomes FPIAR (FPU 7.2.10); one still in its dialog (B, W, L)
            # is released now.
            if self.cu_pcv:
                self.fpiar = self.cu_pc
            if not self.cu_rel and not self.cmd_pend:
                self.resp, self.oneshot, self.expect = 0x0900, 0, EXP_CMD
            self.cu_clear()
        if o.get('cu_load'):
            self._cu_load(o['cu_idx'], o['cu_data'])
        if o.get('cu_resume'):
            # After FRESTORE of a busy frame: the CU's dialog as it was.
            if self.cu_cnt:
                self.resp = 0x8900 if self.cu_bwl else 0x0900
                self.expect = EXP_OPR if self.cu_xrd else EXP_OPW
            else:
                self.resp, self.expect = 0x8900, EXP_RESP
            self.oneshot = 0
        if o.get('relatch'):
            # After FRESTORE, a pending instruction goes back to the latch,
            # behind an instruction the frame left in the CU.
            self.cmd_pend, self.cmd_word, self.cmd_cond = 1, o['relatch_word'], o['relatch_cond']
            self.resp, self.oneshot, self.expect = 0x8900, 0, EXP_RESP
        self.apu_run, self.pcen = o.get('apu_run', 0), o.get('pcen', 0)
        self._fm_apply(self._fmdec)
        self._fmdec = {}
        # A latched command the CU can take, now that it is free.
        if self.cmd_pend and not self.cu_v and self.apu_run and not o['cmd_ack'] \
                and not self.pv and self._cu_takes(self.cmd_word) and not self.cmd_cond:
            self.cmd_pend, self.fresh = 0, 0
            self._cu_accept(self.cmd_word)

    def _cu_load(self, idx, v):
        """FRESTORE: a CU long word from the frame."""
        if idx == 0:
            if v >> 15 & 1:
                self.cu_clear()
                return
            self.cu_v, self.cu_word = 1, v >> 16
            self.cu_rel, self.cu_pcv, self.cu_cnt = v >> 14 & 1, v >> 13 & 1, v & 0xFF
            rx = self.cu_word >> 10 & 7
            self.cu_bwl = int(self.cu_word >> 13 & 7 == 2 and rx in (0, 4, 6))
            self.cu_fm, self.cu_nofm, self.cu_rd = fm_kind(self.cu_word), 0, 0
            self.cu_xrd = int(self.cu_fm == FM_XOUT and self.cu_cnt > 0)
        elif idx == 1:
            self.cu_pc = v
        elif idx <= 4:
            self.cu_d[idx - 2] = v

    def _cu_accept(self, word):
        self.cu_clear()
        if fm_kind(word) == FM_XOUT:
            # Figure 7-21: null (CA = 1) while the CU reads and converts.
            self.cu_v, self.cu_word, self.cu_fm = 1, word, FM_XOUT
            self.resp, self.oneshot, self.expect = 0x8900, 0, EXP_RESP
            return
        prim, n = cu_first(word, self.pcen)
        self.cu_v, self.cu_word, self.cu_cnt = 1, word, n
        self.cu_fm = fm_kind(word)
        rx = word >> 10 & 7
        self.cu_bwl = int(word >> 13 & 7 == 2 and rx in (0, 4, 6))
        self.resp, self.oneshot, self.expect = prim, 1, EXP_RESP
        if n == 0:
            self.expect_next = EXP_CMD
        else:
            self.expect_next = EXP_OPW
        self.resp_xfer = 0

    # ------------------------------------------------------ an access
    def ready(self, cir, write):
        """Asked once a clock while an access waits."""
        if cir == CIR_RESPONSE and not write and not self.pv:
            if self.fresh and not self.cmd_pend and self.held < self.resp_hold:
                self.held += 1
                return False
            return True
        if cir == CIR_OPERAND and not self._violates(cir, write) and not self.pv:
            if write and self.cu_dlg() and self.cu_cnt and not self.xfer:
                return True
            if not write and self._cu_read():
                return True
            return self.opr_valid if not write else not self.opw_valid
        if cir == CIR_RESTORE and not write:
            return self.restore_valid
        return True

    def _violates(self, cir, write):
        if self.model == 68882:
            # FPU 6.1.12: the PC, when asked for, comes next; never otherwise.
            if self.expect == EXP_PC:
                return cir in (CIR_COMMAND, CIR_CONDITION, CIR_OPERAND) or cir == CIR_REGSEL
            if cir == CIR_INSTADDR and write:
                return not self.xfer
        if cir in (CIR_COMMAND, CIR_CONDITION) and write:
            return self.expect != EXP_CMD
        if cir == CIR_OPERAND:
            return self.expect != (EXP_OPW if write else EXP_OPR)
        if cir == CIR_REGSEL:
            return (self.expect != EXP_RSEL) if not write else True
        return False

    def access(self, cir, write, value=0):
        """Complete one access; returns the read value. Call ready() first."""
        ev = {}
        self.held = 0
        rv = 0xFFFF if cir < 0x10 else 0xFFFFFFFF
        viol = self._violates(cir, write)
        if cir == CIR_RESPONSE and not write:
            rv = self.resp
        elif cir == CIR_SAVE and not write:
            rv = 0x0218 if self.xfer else (self.save if self.save_valid else 0x0118)
        elif cir == CIR_RESTORE and not write:
            rv = self.restore
        elif cir == CIR_OPERAND and not write and self._cu_read():
            rv = self.cu_d[3 - self.cu_cnt] if self.expect == EXP_OPR else 0xFFFFFFFF
        elif cir == CIR_OPERAND and not write:
            rv = self.opr if (self.expect == EXP_OPR and not self.pv) else 0xFFFFFFFF
        elif cir == CIR_REGSEL and not write:
            rv = self.rsel << 8
        if viol and not self.pv:
            self.pv, self.resp, self.oneshot, self.fresh = 1, 0x1D0D, 0, 0
        elif not viol and not self.pv:
            if cir == CIR_RESPONSE and not write:
                ev['resp'] = 1
                if self.model == 68882:
                    self._resp_read82()
                elif self.oneshot:
                    self.resp, self.oneshot, self.expect = 0x8900, 0, self.expect_next
            elif cir == CIR_REGSEL and not write:
                ev['rsel'] = 1
                self.expect = EXP_OPR if self.rsel_dir else EXP_OPW
            elif cir in (CIR_COMMAND, CIR_CONDITION) and write:
                if self.model == 68882 and cir == CIR_COMMAND and self.apu_run \
                        and not self.cu_v and not self.cmd_pend and self._cu_takes(value & 0xFFFF):
                    self._cu_accept(value & 0xFFFF)
                else:
                    self.cmd_pend, self.cmd_cond, self.cmd_word = 1, int(cir == CIR_CONDITION), value & 0xFFFF
                    self.resp, self.oneshot, self.expect = 0x8900, 0, EXP_RESP
                    self.fresh = 1
            elif cir == CIR_OPERAND:
                if write and self.model == 68882 and self.cu_dlg() and self.cu_cnt \
                        and not self.xfer:
                    self._cu_operand(value & 0xFFFFFFFF)
                elif not write and self._cu_read():
                    self.cu_cnt -= 1
                    if self.cu_cnt == 0:
                        self.expect = EXP_CMD                  # CA = 0, no read
                        self.cu_clear()
                elif write:
                    self.opw_data, self.opw_valid = value & 0xFFFFFFFF, 1
                else:
                    self.opr_valid = 0
                if self.xfer:
                    self.xfer -= 1
                    if self.xfer == 0:
                        self.expect = EXP_CMD
                elif self.ca0:
                    self.ca0 -= 1
                    if self.ca0 == 0:
                        self.expect = EXP_CMD            # FPU 7.5.1: CA = 0, no read
            elif cir == CIR_INSTADDR and write and self.model == 68882:
                self.expect = self.pc_after
                if self.pc_cu:
                    self.cu_pc, self.cu_pcv = value & 0xFFFFFFFF, 1
                else:
                    self.fpiar = value & 0xFFFFFFFF
        if not viol or self.pv:
            # Legal at any time (FPU 7.2.1-7.2.4, 7.2.10).
            if cir == CIR_SAVE and not write and not self.xfer:
                if self.save_valid:
                    ev['save'] = 1
                    self.save_valid = 0
                    self.save_req = 0
                    if self.save_xfer:
                        self.expect, self.xfer = EXP_OPR, self.save_xfer
                else:
                    self.save_req = 1
            elif cir == CIR_RESTORE and write:
                self.restore_word, self.restore_req, self.restore_valid = value & 0xFFFF, 1, 0
                self.resp, self.oneshot = 0x8900, 0
            elif cir == CIR_RESTORE and not write and self.restore_xfer:
                self.expect, self.xfer, self.restore_xfer = EXP_OPW, self.restore_xfer, 0
            elif cir == CIR_INSTADDR and write and self.model != 68882:
                self.fpiar = value & 0xFFFFFFFF
        if cir == CIR_CONTROL and write:
            if self.model == 68882:
                self._control82(value, ev)
            else:
                self._abort_all(ev)
        self.ev = ev
        return rv

    def _abort_all(self, ev):
        ev['abort'] = 1
        self.pv, self.resp, self.oneshot, self.expect = 0, 0x0802, 0, EXP_CMD
        self.cmd_pend = self.opw_valid = self.opr_valid = self.fresh = 0
        self.save_valid = self.save_req = 0
        self.restore_req = self.restore_valid = 0
        self.restore_xfer = self.xfer = 0
        self.ca0 = 0
        self.cu_clear()

    # ------------------------------------------------- the MC68882's rules
    def _resp_read82(self):
        r = self.resp
        cu = self.cu_dlg()
        if self.oneshot:
            nxt = self.expect_next
            if cu:
                # The CU's first primitive: $0900 after an S, D or X request
                # (figure 7-19), $8900 after a B, W or L one; released at
                # once if register to register (figure 7-17).
                if self.cu_cnt == 0:
                    self.cu_rel = 1
                else:
                    self.resp = 0x8900 if self.cu_bwl else 0x0900
            else:
                self.resp = 0x8900
                if self.resp_xfer:
                    self.ca0 = self.resp_xfer
            self.oneshot = 0
            self.expect = nxt
        if r >> 14 & 1:
            # FPU 7.2.10: the PC is mandatory, and comes before anything else.
            self.pc_after, self.pc_cu = self.expect, int(cu)
            self.expect = EXP_PC

    def _cu_read(self):
        """An operand read of the CU's extended store (figure 7-21)."""
        return self.model == 68882 and self.cu_xrd and self.cu_cnt and not self.xfer \
            and not self.pv and self.expect == EXP_OPR

    def _cu_operand(self, v):
        k = fmt_longs(self.cu_word) - self.cu_cnt
        self.cu_d[k] = v
        self.cu_cnt -= 1
        if self.cu_cnt == 0:
            if self.cu_bwl:
                self.expect = EXP_RESP                     # $8900 until the take
            else:
                self.cu_rel = 1                            # CA = 0: released
                self.expect = EXP_CMD

    def _control82(self, value, ev):
        """FPU 7.2.2. AB aborts the last instruction received, inside its
        window; XA acknowledges, and leaves a floating-point exception."""
        if value & 1:
            if self.cmd_pend:
                self.cmd_pend = self.fresh = 0
            elif self.cu_dlg():
                self.cu_clear()
            elif self.expect != EXP_CMD or self.pv:
                ev['abort_ab'] = 1
                self.pv, self.oneshot, self.expect = 0, 0, EXP_CMD
                self.opw_valid = self.opr_valid = self.fresh = 0
                self.save_valid = self.save_req = 0
                self.restore_req = self.restore_valid = 0
                self.restore_xfer = self.xfer = self.ca0 = 0
            self.pv = 0
            self.oneshot, self.expect = 0, EXP_CMD
            self.resp = 0x0900 if (self.apu_run or self.cu_v) else 0x0802
            return
        if self.pv or not is_fp_take(self.resp):
            self._abort_all(ev)


def fmt_longs(word):
    """The operand long words of an opclass 010 command word."""
    n = FMT_BYTES[word >> 10 & 7] if (word >> 13 & 7) == 2 and (word >> 10 & 7) != 7 else 0
    return (n + 3) // 4
