# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""rd68884_biu's registers at the level of whole CIR accesses.

The rules are those of rtl/rd68884_biu.sv (which sim/tb/biu_tb.sv checks at
the pins, for every port size). Here an access is one call: access() says
whether it can complete now (an operand access the sequencer is not ready
for waits, FPU 10.5) and, when it does, applies its effects and raises the
one-clock events the core sees on the next clock.
"""

EXP_CMD, EXP_RESP, EXP_OPW, EXP_OPR, EXP_RSEL = range(5)
CIR_RESPONSE, CIR_CONTROL, CIR_SAVE, CIR_RESTORE = 0x00, 0x02, 0x04, 0x06
CIR_COMMAND, CIR_CONDITION, CIR_OPERAND, CIR_REGSEL = 0x0A, 0x0E, 0x10, 0x14
CIR_INSTADDR = 0x18


class Biu:
    def __init__(self):
        self.reset()

    def reset(self):
        self.resp, self.oneshot, self.expect, self.expect_next = 0x0802, 0, EXP_CMD, EXP_CMD
        self.pv = 0
        self.cmd_pend, self.cmd_cond, self.cmd_word = 0, 0, 0
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

    # ---------------------------------------------------- the core's view
    def outputs(self):
        e = self.ev
        return dict(arch_reset=self.arch_reset, cmd_pend=self.cmd_pend,
                    cmd_cond=self.cmd_cond, cmd_word=self.cmd_word,
                    opw_valid=self.opw_valid, opw_data=self.opw_data,
                    opr_valid=self.opr_valid, save_req=self.save_req,
                    restore_req=self.restore_req, restore_word=self.restore_word,
                    fpiar=self.fpiar, pv=self.pv,
                    resp_read=e.get('resp', 0), rsel_read=e.get('rsel', 0),
                    save_read=e.get('save', 0), abort=e.get('abort', 0))

    def from_core(self, o):
        """The core's writes, at the end of a clock (after its events)."""
        self.ev = {}
        if o['resp_we'] and not (o['resp_cond'] and self.cmd_pend):
            self.resp = o['resp']
            self.oneshot = o['resp_oneshot']
            if o['resp_oneshot']:
                self.expect, self.expect_next = EXP_RESP, o['expect']
            else:
                self.expect = o['expect']
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
            if not self.cmd_pend:
                self.resp, self.oneshot, self.expect = 0x0802, 0, EXP_CMD

    # ------------------------------------------------------ an access
    def ready(self, cir, write):
        if cir == CIR_OPERAND and not self._violates(cir, write) and not self.pv:
            return self.opr_valid if not write else not self.opw_valid
        if cir == CIR_RESTORE and not write:
            return self.restore_valid
        return True

    def _violates(self, cir, write):
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
        rv = 0xFFFF if cir < 0x10 else 0xFFFFFFFF
        viol = self._violates(cir, write)
        if cir == CIR_RESPONSE and not write:
            rv = self.resp
        elif cir == CIR_SAVE and not write:
            rv = 0x0218 if self.xfer else (self.save if self.save_valid else 0x0118)
        elif cir == CIR_RESTORE and not write:
            rv = self.restore
        elif cir == CIR_OPERAND and not write:
            rv = self.opr if (self.expect == EXP_OPR and not self.pv) else 0xFFFFFFFF
        elif cir == CIR_REGSEL and not write:
            rv = self.rsel << 8
        if viol and not self.pv:
            self.pv, self.resp, self.oneshot = 1, 0x1D0D, 0
        elif not viol and not self.pv:
            if cir == CIR_RESPONSE and not write:
                ev['resp'] = 1
                if self.oneshot:
                    self.resp, self.oneshot, self.expect = 0x8900, 0, self.expect_next
            elif cir == CIR_REGSEL and not write:
                ev['rsel'] = 1
                self.expect = EXP_OPR if self.rsel_dir else EXP_OPW
            elif cir in (CIR_COMMAND, CIR_CONDITION) and write:
                self.cmd_pend, self.cmd_cond, self.cmd_word = 1, int(cir == CIR_CONDITION), value & 0xFFFF
                self.resp, self.oneshot, self.expect = 0x8900, 0, EXP_RESP
            elif cir == CIR_OPERAND:
                if write:
                    self.opw_data, self.opw_valid = value & 0xFFFFFFFF, 1
                else:
                    self.opr_valid = 0
                if self.xfer:
                    self.xfer -= 1
                    if self.xfer == 0:
                        self.expect = EXP_CMD
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
            elif cir == CIR_INSTADDR and write:
                self.fpiar = value & 0xFFFFFFFF
        if cir == CIR_CONTROL and write:
            ev['abort'] = 1
            self.pv, self.resp, self.oneshot, self.expect = 0, 0x0802, 0, EXP_CMD
            self.cmd_pend = self.opw_valid = self.opr_valid = 0
            self.save_valid = self.save_req = 0
            self.restore_req = self.restore_valid = 0
            self.restore_xfer = self.xfer = 0
        self.ev = ev
        return rv
