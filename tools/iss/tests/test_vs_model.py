# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The microcode (through the ISS) against the golden model (tools/model).

Each scenario is a sequence of instructions run by the MC68020 driver
(tools/model/mpu.py) twice: once on cpif.FPU881, once on iss.Machine. The
two must end with the same registers, the same main-processor registers and
memory (so the same FSAVE frames), the same exceptions, and the same
primitives -- leaving out the null (CA=1) waits, whose number is timing, and
counting the two CA=0 nulls a finished instruction may answer with ($0900
while running, $0802 when done) as one.

M4 scope: only what the microcode implements (tools/ucode/program.py).
"""

import unittest

from model import cpif as C, formats as F
from model.cpif import FPU881
from model.mpu import MPU, CPException
from model.xnum import fin
from iss.machine import Machine


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def x96(v):
    return F.encode_x(v).bits96()


FMT_X = F.FMT_X


class Run:
    """One scenario on one FPU, recording what the MPU saw."""

    def __init__(self, fpu):
        self.fpu = fpu
        self.m = MPU(fpu)
        self.m.a[7] = 0x8000
        self.events = []

    def gen(self, c, ea=None):
        try:
            p = self.m.fgen(c, ea)
            self.events.append(('ok', c))
        except CPException as e:
            self.events.append((e.kind, e.vector, c))

    def cond(self, pred):
        try:
            self.events.append(('tf', pred, self.m.fcond(pred)))
        except CPException as e:
            self.events.append((e.kind, e.vector, pred))

    def fsave(self, ea):
        try:
            self.events.append(('save', self.m.fsave(ea)))
        except CPException as e:
            self.events.append((e.kind, e.vector))

    def frestore(self, ea):
        try:
            self.events.append(('restore', self.m.frestore(ea)))
        except CPException as e:
            self.events.append((e.kind, e.vector))

    def settle(self):
        self.fpu.tick(400)

    # ------------------------------------------------------- the outcome
    def state(self):
        f = self.fpu
        if isinstance(f, FPU881):
            regs = [f.st.fp[i].bits80() for i in range(8)]
            ctl = (f.st.fpcr, f.st.fpsr, f.st.fpiar)
        else:
            regs = [f.fp_bits80(i) for i in range(8)]
            ctl = (f.fpcr, f.fpsr, f.fpiar)
        return dict(regs=regs, ctl=ctl, d=list(self.m.d), a=list(self.m.a),
                    mem=dict(self.m.mem.b), events=self.events)

    def prims(self):
        out = []
        for rw, cir, v in self.fpu.log:
            if rw == 'R' and cir == C.CIR_RESPONSE:
                if v in (C.P_WAIT,):
                    continue
                if v in (C.P_IDLE, C.P_RELEASE):
                    v = 'NULL0'
                if out and out[-1] == v:
                    continue
                out.append(v)
        return out


def both(scenario):
    runs = []
    for fpu in (FPU881(latency=lambda c: 3), Machine()):
        r = Run(fpu)
        scenario(r)
        r.settle()
        runs.append(r)
    return runs


class VsModel(unittest.TestCase):

    def same(self, scenario, prims=True):
        model, iss = both(scenario)
        sm, si = model.state(), iss.state()
        for k in sm:
            self.assertEqual(sm[k], si[k], f'{k} differs')
        if prims:
            self.assertEqual(model.prims(), iss.prims(), 'primitives differ')
        return model, iss

    # --------------------------------------------------------------- scenarios
    def test_fmove_x(self):
        vals = [fin(0, 3, 0), fin(1, 0xABCDEF, -40), fin(0, 1, 16000)]

        def s(r):
            for i, v in enumerate(vals):
                r.gen(cmd(2, FMT_X, i, 0), ('imm', x96(v), 12))
            r.gen(cmd(0, 1, 5, 0))                          # FMOVE.X FP1,FP5
            r.m.a[0] = 0x4000
            r.gen(cmd(3, FMT_X, 5, 0), ('ind', 0))          # FMOVE.X FP5,(A0)
            r.gen(cmd(3, FMT_X, 2, 0), ('predec', 7))
        self.same(s)

    def test_fmovem(self):
        vals = [fin(0, i + 1, i) for i in range(4)]

        def s(r):
            for i, v in enumerate(vals):
                r.gen(cmd(2, FMT_X, i, 0), ('imm', x96(v), 12))
            r.gen((7 << 13) | (0 << 11) | 0x0F, ('predec', 7))      # FP0-FP3,-(A7)
            r.m.d[2] = 0b11110000                                    # FP4-FP7 (mode 11)
            r.gen((6 << 13) | (3 << 11) | (2 << 4), ('postinc', 7))
            r.m.a[1] = 0x6000
            r.gen((7 << 13) | (2 << 11) | 0x81, ('ind', 1))          # FP0/FP7,(A1)
            r.m.d[3] = 0
            r.gen((7 << 13) | (3 << 11) | (3 << 4), ('ind', 1))      # empty dynamic list
        self.same(s)

    def test_control_registers(self):
        def s(r):
            r.gen(cmd(4, 4, 0, 0), ('imm', 0x0000FFF0, 4))   # FPCR: bits 31-16 dropped
            r.gen(cmd(4, 2, 0, 0), ('dn', 3))                # FPSR from D3 (0)
            r.m.a[0] = 0x5000
            r.m.mem.write(0x5000, 12, (0x30 << 64) | (0x0F00FF00 << 32) | 0x1234)
            r.gen(cmd(4, 7, 0, 0), ('ind', 0))               # all three
            r.gen(cmd(5, 7, 0, 0), ('predec', 7))
            r.gen(cmd(5, 1, 0, 0), ('an', 4))                # FPIAR alone to A4
            r.gen(cmd(5, 0, 0, 0), ('dn', 5))                # list 000 = FPIAR
            r.gen(cmd(4, 5, 0, 0), ('ind', 0))               # FPCR and FPIAR
        self.same(s)

    def test_conditionals(self):
        def s(r):
            for cc in range(16):
                r.gen(cmd(4, 2, 0, 0), ('imm', cc << 24, 4))
                for p in list(range(32)) + [0x2A, 0x3F]:
                    r.cond(p)
            r.gen(cmd(4, 4, 0, 0), ('imm', 0x8000, 4))       # BSUN enabled
            r.gen(cmd(4, 2, 0, 0), ('imm', 1 << 24, 4))      # NAN set
            r.m.pc = 0x1234
            r.cond(0x12)                                      # GT: BSUN, vector 48
            r.cond(0x02)                                      # OGT: aware, no trap
        self.same(s)

    def test_frames_and_pending_exception(self):
        def s(r):
            r.fsave(('predec', 7))                           # null after reset
            r.gen(cmd(2, FMT_X, 0, 0), ('imm', x96(fin(0, 5, 0)), 12))
            r.settle()
            r.fsave(('predec', 7))                           # idle
            base = r.m.a[7]
            # Make an exception pending by hand (FPU 6.4.2.2): EXC PEND low,
            # with DZ set in the FPSR and enabled in the FPCR.
            flags = r.m.mem.read(base + 0x18, 4)
            r.m.mem.write(base + 0x18, 4, flags & ~(1 << 27))
            r.gen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
            r.gen(cmd(4, 2, 0, 0), ('imm', 0x0400, 4))
            r.frestore(('postinc', 7))
            r.gen((7 << 13) | (2 << 11) | 0x80, ('predec', 7))   # FMOVEM: not reported
            r.gen(cmd(5, 4, 0, 0), ('dn', 1))                    # FMOVE FPCR: not reported
            r.cond(0x00)                                          # FNOP: vector 50
            r.cond(0x00)                                          # and then clear
            r.gen(cmd(4, 4, 0, 0), ('imm', 0, 4))
            r.fsave(('predec', 7))
        self.same(s)

    def test_fline(self):
        def s(r):
            r.gen(0x0045)                                    # opmode $45
            r.gen(cmd(1, 0, 0, 0))                           # opclass 001
            r.gen(cmd(2, FMT_X, 0, 0), ('imm', x96(fin(0, 1, 0)), 12))
        self.same(s)

    def test_restore_invalid_and_null(self):
        def s(r):
            r.gen(cmd(2, FMT_X, 3, 0), ('imm', x96(fin(0, 9, 0)), 12))
            r.m.a[0] = 0x7000
            r.m.mem.write(0x7000, 4, 0x3F180000)
            r.frestore(('ind', 0))                           # another version: format error
            r.m.mem.write(0x7000, 4, 0)
            r.frestore(('ind', 0))                           # null: reset
            r.fsave(('predec', 7))
        self.same(s)

    def test_protocol_violation(self):
        def s(r):
            r.fpu.write(C.CIR_OPERAND, 0)                    # unexpected operand write
            r.events.append(('resp', r.fpu.read(C.CIR_RESPONSE)))
            r.fpu.write(C.CIR_CONTROL, 2)
            r.gen(cmd(2, FMT_X, 0, 0), ('imm', x96(fin(0, 1, 0)), 12))
        self.same(s)


class IssOnly(unittest.TestCase):
    """What only the ISS can show: interrupts at its own polling points."""

    def test_interrupt_before_first_primitive(self):
        """FSAVE while an instruction waits for its first primitive to be read:
        an idle frame with the instruction pending, restarted by FRESTORE."""
        fpu = Machine()
        # The read must find the instruction unanswered: no response hold-off,
        # as when a read has outwaited it (rtl/rd68884_biu.sv, RESP_HOLD).
        fpu.biu.resp_hold = 0
        m = MPU(fpu)
        done = []

        def handler(mpu):
            if done:
                return
            done.append(1)
            h = MPU(fpu, m.mem)
            h.a[7] = 0x8000
            self.assertEqual(h.fsave(('predec', 7)), C.FW_IDLE)
            flags = h.mem.read(h.a[7] + 0x18, 4)
            self.assertEqual(flags >> 28 & 7, C.PEND_GEN)
            h.fgen(cmd(2, FMT_X, 6, 0), ('imm', x96(fin(0, 77, 0)), 12))
            h.fcond(0)      # FNOP: FRESTORE aborts a running instruction (FPU 7.2.4)
            h.frestore(('postinc', 7))
        m.poll_hook = handler
        m.fgen(cmd(2, FMT_X, 1, 0), ('imm', x96(fin(0, 42, 0)), 12))
        fpu.tick(100)
        self.assertTrue(done, 'the handler never ran')
        self.assertEqual(fpu.fp_bits80(1), F.encode_x(fin(0, 42, 0)).bits80())
        self.assertEqual(fpu.fp_bits80(6), F.encode_x(fin(0, 77, 0)).bits80())

    def test_save_during_computation_waits(self):
        fpu = Machine()
        m = MPU(fpu)
        m.fgen(cmd(2, FMT_X, 1, 0), ('imm', x96(fin(0, 42, 0)), 12))
        m.fgen(cmd(0, 1, 2, 0))
        m.a[7] = 0x8000
        self.assertEqual(m.fsave(('predec', 7)), C.FW_IDLE)


if __name__ == '__main__':
    unittest.main()
