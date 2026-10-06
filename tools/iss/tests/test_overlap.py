# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The MC68882 microcode and its conversion unit (the ISS, model=68882)
against the golden model, cpif.FPU882 (doc/rd68885.md).

The programs are tools/model/tests/test_882.py's, run on both; the ISS's own
timing decides what overlaps, so an exception may be reported
mid-instruction on one and pre-instruction on the other. What must agree is
what the program sees: registers, memory, the values stored, the
conditions, each exception's vector and FPIAR.
"""

import random
import unittest

from model import cpif as C, formats as F
from model.arith import OP
from model.cpif import FPU882
from model.mpu import MPU, CPException
from model.tests.test_882 import program, run, handler, cmd, x96, SLOW, fpiar
from model.xnum import fin
from iss.machine import Machine


def responses(fpu):
    return [v for rw, cir, v in fpu.log if rw == 'R' and cir == C.CIR_RESPONSE]


class Dialogs(unittest.TestCase):
    """The MC68882's dialogs on the microcode."""

    def setUp(self):
        self.fpu = Machine(model=68882)
        self.m = MPU(self.fpu)
        self.m.a[7] = 0x8000

    def load(self, reg, v):
        self.m.fgen(cmd(2, F.FMT_X, reg, OP['FMOVE']), ('imm', x96(v), 12))

    def test_ca0_load(self):
        """Figure 7-19."""
        self.fpu.log.clear()
        p = self.m.fgen(cmd(2, F.FMT_D, 1, OP['FMOVE']), ('imm', F.encode_d(fin(0, 3, 0)), 8))
        self.assertEqual(p, 0x1608)
        self.assertEqual(responses(self.fpu), [0x1608])
        self.fpu.tick(200)
        self.assertEqual(self.fpu.fp_bits80(1), F.encode_x(fin(0, 3, 0)).bits80())

    def test_cu_takes_the_next(self):
        """FPU 5.1.1.2: while FSQRT computes, the next instruction's dialog
        is answered by the CU at once: no null (CA=1) in between."""
        self.load(0, fin(0, 2, 0))
        self.fpu.tick(200)
        self.fpu.log.clear()
        self.m.fgen(cmd(0, 0, 1, OP['FSQRT']))
        self.m.fgen(cmd(2, F.FMT_S, 2, OP['FADD']), ('imm', F.encode_s(fin(0, 5, 0)), 4))
        self.assertEqual(responses(self.fpu), [C.P_RELEASE, 0x1504])
        self.assertTrue(self.fpu.biu.cu_v)
        self.fpu.tick(400)
        self.assertFalse(self.fpu.biu.cu_v)

    def test_mid_instruction_report(self):
        """FPU 6.1: a B source in the CU reports the APU's exception."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        self.load(0, fin(0, 1, 0))
        self.load(1, fin(0, 0, 0))
        self.fpu.tick(200)
        self.m.fgen(cmd(0, 1, 0, OP['FDIV']))
        with self.assertRaises(CPException) as e:
            self.m.fgen(cmd(2, F.FMT_B, 2, OP['FMOVE']), ('imm', 5, 1))
        self.assertEqual((e.exception.kind, e.exception.vector), ('mid', 50))
        self.m.fsave(('predec', 7))
        base = self.m.a[7]
        self.assertEqual(self.m.mem.read(base, 4) >> 16, C.FW82_BUSY)
        self.assertEqual(self.m.mem.read(base + 0xD4, 4) >> 27 & 1, 0)
        self.m.mem.write(base + 0xD4, 1, self.m.mem.read(base + 0xD4, 1) | 8)
        self.m.frestore(('postinc', 7))
        self.assertEqual(self.m.resume(), C.P_RELEASE)
        self.fpu.tick(200)
        self.assertEqual(self.fpu.fp_bits80(2), F.encode_x(fin(0, 5, 0)).bits80())


class VsModel(unittest.TestCase):
    """Programs on the golden model and on the microcode."""

    def both(self, prog):
        a = run(FPU882(latency=lambda c: SLOW), prog)
        b = run(Machine(model=68882), prog)
        return a, b

    def test_held_cu_frame(self):
        """An exception holds a released instruction in the CU: the idle
        frames must be the same, byte for byte (doc/model.md)."""
        def scen(fpu):
            m = MPU(fpu)
            m.a[7] = 0x8000
            m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
            m.fgen(cmd(2, F.FMT_X, 0, OP['FMOVE']), ('imm', x96(fin(0, 1, 0)), 12))
            m.fgen(cmd(2, F.FMT_X, 1, OP['FMOVE']), ('imm', x96(fin(0, 0, 0)), 12))
            fpu.tick(300)
            m.pc = 0x2000
            m.fgen(cmd(0, 1, 0, OP['FDIV']))
            m.pc = 0x2004
            m.fgen(cmd(2, F.FMT_D, 2, OP['FMOVE']), ('imm', F.encode_d(fin(0, 9, 0)), 8))
            fpu.tick(300)
            with self.assertRaises(CPException):
                m.fcond(0x0F)
            m.fsave(('predec', 7))
            return [m.mem.read(m.a[7] + 4 * i, 4) for i in range(15)]
        a = scen(FPU882(latency=lambda c: SLOW))
        b = scen(Machine(model=68882))
        self.assertEqual(['%08X' % v for v in a], ['%08X' % v for v in b])
        self.assertEqual(a[2] >> 15 & 1, 0)                 # the CU holds it

    def test_busy_mid_ca0(self):
        """FSAVE in the middle of a CA = 0 operand transfer (a bus error on
        its second long word, FPU 7.5.4.3): a busy frame, other work,
        FRESTORE, the rest of the operand; then the next instruction, with
        no response read in between (figure 7-19)."""
        def scen(fpu, cu):
            m = MPU(fpu)
            m.a[7] = 0x8000
            m.fgen(cmd(2, F.FMT_X, 0, OP['FMOVE']), ('imm', x96(fin(0, 2, 0)), 12))
            fpu.tick(300)
            if cu:                                       # the APU busy: the CU's dialog
                m.fgen(cmd(0, 0, 1, OP['FSQRT']))
            img = F.encode_d(fin(0, 7, 0))
            fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_D, 2, OP['FADD']))
            assert fpu.read(C.CIR_RESPONSE) == 0x1608
            fpu.write(C.CIR_OPERAND, img >> 32)
            fw = m.fsave(('predec', 7))
            m.fgen(cmd(2, F.FMT_L, 3, OP['FMOVE']), ('imm', 5, 4))   # another task
            fpu.tick(300)
            m.frestore(('postinc', 7))
            fpu.write(C.CIR_OPERAND, img & 0xFFFFFFFF)
            m.fgen(cmd(2, F.FMT_S, 4, OP['FMOVE']), ('imm', F.encode_s(fin(0, 3, 0)), 4))
            fpu.tick(600)
            regs = [fpu.st.fp[i].bits80() for i in range(5)] if hasattr(fpu, 'st') \
                else [fpu.fp_bits80(i) for i in range(5)]
            return fw, regs
        for cu in (False, True):
            a = scen(FPU882(latency=lambda c: SLOW), cu)
            b = scen(Machine(model=68882), cu)
            self.assertEqual(a, b)
            self.assertEqual(a[0], C.FW82_BUSY)

    def regs(self, fpu, n=8):
        return [fpu.st.fp[i].bits80() for i in range(n)] if hasattr(fpu, 'st') \
            else [fpu.fp_bits80(i) for i in range(n)]

    def test_abort_the_cu(self):
        """FPU 7.2.2: AB in the CU's dialog aborts its instruction only."""
        def scen(fpu):
            m = MPU(fpu)
            m.fgen(cmd(2, F.FMT_X, 0, OP['FMOVE']), ('imm', x96(fin(0, 2, 0)), 12))
            fpu.tick(300)
            m.fgen(cmd(0, 0, 1, OP['FSQRT']))
            fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_D, 2, OP['FMOVE']))
            p = fpu.read(C.CIR_RESPONSE)
            fpu.write(C.CIR_CONTROL, 1)
            m.fgen(cmd(2, F.FMT_L, 3, OP['FMOVE']), ('imm', 4, 4))
            fpu.tick(600)
            return p, fpu.read(C.CIR_RESPONSE), self.regs(fpu, 4)
        a = scen(FPU882(latency=lambda c: SLOW))
        b = scen(Machine(model=68882))
        self.assertEqual(a, b)
        self.assertEqual(a[0], 0x1608)

    def test_interrupt_latched_behind_held_cu(self):
        """An interrupt's FSAVE/FRESTORE while a third instruction waits
        behind an exception-held CU: the frame carries both; afterwards the
        exception is reported, its handler lets the CU's instruction run,
        and the third one runs last (doc/rd68885.md)."""
        def scen(fpu):
            m = MPU(fpu)
            m.a[7] = 0x8000
            trail = []
            def irq(mpu):
                if not trail:
                    trail.append('irq')
                    trail.append(mpu.fsave(('predec', 7)))     # it polls too
                    mpu.frestore(('postinc', 7))
            m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
            m.fgen(cmd(2, F.FMT_X, 0, OP['FMOVE']), ('imm', x96(fin(0, 1, 0)), 12))
            m.fgen(cmd(2, F.FMT_X, 1, OP['FMOVE']), ('imm', x96(fin(0, 0, 0)), 12))
            fpu.tick(300)
            m.pc = 0x3000
            m.fgen(cmd(0, 1, 0, OP['FDIV']))                       # DZ
            m.pc = 0x3004
            m.fgen(cmd(2, F.FMT_S, 2, OP['FMOVE']), ('imm', F.encode_s(fin(0, 6, 0)), 4))
            m.pc = 0x3008
            m.poll_hook = irq
            third = cmd(2, F.FMT_S, 3, OP['FADD'])
            try:
                m.fgen(third, ('imm', F.encode_s(fin(0, 1, 0)), 4))
            except CPException as e:
                trail.append(('exc', e.kind, e.vector, fpiar(fpu)))
                m.poll_hook = None
                handler(m)
                m.fgen(third, ('imm', F.encode_s(fin(0, 1, 0)), 4))
            fpu.tick(600)
            return trail, self.regs(fpu, 4)
        a = scen(FPU882(latency=lambda c: SLOW))
        b = scen(Machine(model=68882))
        self.assertEqual(a, b)

    def test_fmove_in_the_cu(self):
        """FPU table 5-5: FMOVEs the CU completes while FSQRT computes, among
        them a conflict (f) that must wait, then the condition codes in
        program order. Same end as the model; and the CU did complete them."""
        from iss import biu as B
        done = []
        orig = B.Biu._fm_apply

        def spy(biu, d):
            if d.get('done') or d.get('xout'):
                done.append(biu.cu_fm)
            return orig(biu, d)

        def scen(fpu):
            m = MPU(fpu)
            m.a[5] = 0x5000
            for r in range(8):
                m.fgen(cmd(2, F.FMT_L, r, OP['FMOVE']), ('imm', r + 2, 4))
            fpu.tick(300)
            out = []
            for src in (0, 3):
                m.fgen(cmd(0, src, 1, OP['FSQRT']))                       # the APU: FP1
                m.fgen(cmd(2, F.FMT_D, 2, OP['FMOVE']), ('imm', F.encode_d(fin(1, 5, -1)), 8))
                m.fgen(cmd(0, 4, 5, OP['FMOVE']))                         # FP4 to FP5
                m.fgen(cmd(3, F.FMT_X, 6, 0), ('predec', 5))              # FP6 out
                m.fgen(cmd(2, F.FMT_S, 1, OP['FMOVE']), ('imm', F.encode_s(fin(0, 7, 0)), 4))  # (f)
                out.append(m.fcond(0x02))                                 # FBOGT: FP1's CC... FMOVE's
                out.append(fpu.fpsr if not hasattr(fpu, 'st') else fpu.st.fpsr)
            fpu.tick(400)
            regs = [fpu.st.fp[i].bits80() for i in range(8)] if hasattr(fpu, 'st') \
                else [fpu.fp_bits80(i) for i in range(8)]
            return out, regs, m.mem.read(0x5000 - 24, 12), m.mem.read(0x5000 - 12, 12)
        a = scen(FPU882(latency=lambda c: SLOW))
        B.Biu._fm_apply = spy
        try:
            b = scen(Machine(model=68882))
        finally:
            B.Biu._fm_apply = orig
        self.assertEqual(a, b)
        self.assertEqual(sorted(set(done)), [B.FM_IN, B.FM_RR, B.FM_XOUT])

    def test_random_programs(self):
        rnd = random.Random(68885)
        for t in range(150):
            prog = program(rnd, 30, rnd.choice([0, 0x0400, 0x3C00, 0x2400]))
            a, b = self.both(prog)
            for k in a:
                self.assertEqual(a[k], b[k], f'program {t}: {k} differs')


if __name__ == '__main__':
    unittest.main()
