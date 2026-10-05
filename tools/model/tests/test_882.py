# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The MC68882 model (cpif.FPU882) against FPU sections 5-7.

Two kinds of test:
- the dialogs, primitive by primitive, where the MC68882 differs from the
  MC68881 (figures 7-17 to 7-19, FPU 5.1.1.2, 6.1, 7.2.2, 7.2.10);
- programs run on an MC68881 and on an MC68882 with a slow APU, so that
  every instruction overlaps the one before it: the two must end in the
  same state (FPU 5.1.1: "the system apparently executes the instructions
  in sequence"), exceptions and their handlers included.
"""

import random
import unittest

from model import cpif as C, formats as F
from model.arith import OP
from model.cpif import FPU881, FPU882
from model.mpu import MPU, CPException
from model.xnum import fin


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def x96(v):
    return F.encode_x(v).bits96()


def d64(v):
    return F.encode_d(v)


SLOW = 60                       # APU clocks: the next instruction always overlaps


def responses(fpu):
    return [v for rw, cir, v in fpu.log if rw == 'R' and cir == C.CIR_RESPONSE]


def handler(m):
    """FPU figure 5-6, the minimum MC68882 exception handler."""
    m.fsave(('predec', 7))
    base = m.a[7]
    fw = m.mem.read(base, 4) >> 16
    if fw >> 8:
        size = fw & 0xFF
        m.mem.write(base + size, 1, m.mem.read(base + size, 1) | 0x08)
    m.frestore(('postinc', 7))


class Dialogs(unittest.TestCase):

    def setUp(self):
        self.fpu = FPU882(latency=lambda c: SLOW)
        self.m = MPU(self.fpu)
        self.m.a[7] = 0x8000

    def load(self, reg, v):
        """FMOVE.X #v,FPreg, and wait for it."""
        self.m.fgen(cmd(2, F.FMT_X, reg, OP['FMOVE']), ('imm', x96(v), 12))
        self.fpu.tick(SLOW + 1)

    def test_frames(self):
        """FPU table 6-6 and 5-8: idle $1F38 (60 bytes), busy $1FD4."""
        self.load(0, fin(0, 1, 0))
        self.assertEqual(self.m.fsave(('predec', 7)), C.FW82_IDLE)
        self.assertEqual(self.m.a[7], 0x8000 - 0x3C)
        self.m.frestore(('postinc', 7))
        self.assertEqual(self.m.a[7], 0x8000)

    def test_mc68881_frame_refused(self):
        """FPU p. 5-14: an MC68881 frame is a format error on an MC68882."""
        self.load(0, fin(0, 1, 0))
        self.m.mem.write(0x7000, 4, C.FW_IDLE << 16)
        self.m.a[0] = 0x7000
        with self.assertRaises(CPException) as e:
            self.m.frestore(('ind', 0))
        self.assertEqual(e.exception.kind, 'format')

    def test_ca0_load(self):
        """Figure 7-19: S, D, X sources answer with CA = 0, and the MPU goes
        on after the operand, without reading the response again."""
        self.fpu.log.clear()
        p = self.m.fgen(cmd(2, F.FMT_D, 1, OP['FMOVE']), ('imm', d64(fin(0, 3, 0)), 8))
        self.assertEqual(p, 0x1608)
        self.assertEqual(responses(self.fpu), [0x1608])
        self.fpu.tick(SLOW + 1)
        self.assertEqual(F.decode_x(self.fpu.st.fp[1]), fin(0, 3, 0))

    def test_overlap(self):
        """FPU 5.1.1.2: the second instruction's dialog runs while the first
        computes; neither waits for the APU."""
        self.load(0, fin(0, 2, 0))
        self.load(1, fin(0, 3, 0))
        self.fpu.log.clear()
        self.m.fgen(cmd(0, 0, 1, OP['FMUL']))                       # FMUL FP0,FP1
        self.m.fgen(cmd(2, F.FMT_S, 2, OP['FMOVE']), ('imm', F.encode_s(fin(0, 5, 0)), 4))
        self.assertEqual(responses(self.fpu), [C.P_RELEASE, 0x1504])
        self.assertIsNotNone(self.fpu.cu)
        self.fpu.tick(2 * SLOW + 2)
        self.assertEqual(F.decode_x(self.fpu.st.fp[1]), fin(0, 6, 0))
        self.assertEqual(F.decode_x(self.fpu.st.fp[2]), fin(0, 5, 0))
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), C.P_IDLE)

    def test_third_waits(self):
        """FPU 5.1.1.2: a third instruction gets the null (CA=1, IA=1)
        primitive until the CU hands its instruction to the APU."""
        self.load(0, fin(0, 2, 0))
        self.m.fgen(cmd(0, 0, 1, OP['FMOVE']))
        self.m.fgen(cmd(0, 0, 2, OP['FADD']))
        self.fpu.log.clear()
        self.m.fgen(cmd(0, 0, 3, OP['FADD']))
        r = responses(self.fpu)
        self.assertIn(C.P_WAIT, r)
        self.assertEqual(r[-1], C.P_RELEASE)
        self.assertEqual(set(r[:-1]), {C.P_WAIT})

    def test_byte_source_waits(self):
        """FPU 5.1.1.2: a B, W or L source is fetched, then the CU waits for
        the APU with the null (CA=1, IA=1) primitive."""
        self.load(0, fin(0, 2, 0))
        self.m.fgen(cmd(0, 0, 1, OP['FMOVE']))
        self.fpu.log.clear()
        self.m.fgen(cmd(2, F.FMT_B, 2, OP['FMOVE']), ('imm', 7, 1))
        r = responses(self.fpu)
        self.assertEqual(r[0], 0x9501)
        self.assertEqual(r[-1], C.P_RELEASE)
        self.assertIn(C.P_WAIT, r)

    def test_conditional_waits_for_both(self):
        """FPU 5.1.1.2, table 5-6: a conditional waits for the CU and APU."""
        self.load(0, fin(0, 2, 0))
        self.m.fgen(cmd(0, 0, 1, OP['FMOVE']))
        self.m.fgen(cmd(0, 0, 2, OP['FNEG']))
        self.assertEqual(self.m.fcond(0x0E), 1)                    # FBNE: FP2 = -2
        self.assertIsNone(self.fpu.cu)
        self.assertEqual(self.fpu.busy, 0)
        self.assertEqual(F.decode_x(self.fpu.st.fp[2]), fin(1, 2, 0))

    def test_mandatory_pc(self):
        """FPU 7.2.10, 6.1.12: the MPU must pass the PC when asked."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))           # DZ enabled
        self.fpu.write(C.CIR_COMMAND, cmd(0, 0, 1, OP['FADD']))
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), C.P_RELEASE_PC)
        self.fpu.write(C.CIR_COMMAND, cmd(0, 0, 1, OP['FADD']))     # instead of the PC
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), C.P_PROTOCOL)

    def test_unasked_pc(self):
        """FPU 6.1.12 item 1: an instruction address write while the FPU
        expects a command."""
        self.fpu.write(C.CIR_INSTADDR, 0x1234)
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), C.P_PROTOCOL)

    def test_fpiar_follows_the_instruction(self):
        """FPU 7.2.10: FPIAR is the APU's: it changes when the instruction
        reaches the APU, not when its PC is passed."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        self.load(0, fin(0, 2, 0))
        self.m.pc = 0x100
        self.m.fgen(cmd(0, 0, 1, OP['FMOVE']))
        self.assertEqual(self.fpu.st.fpiar, 0x100)
        self.m.pc = 0x104
        self.m.fgen(cmd(0, 0, 2, OP['FMOVE']))
        self.assertEqual(self.fpu.st.fpiar, 0x100)                 # in the CU
        self.fpu.tick(SLOW + 2)
        self.assertEqual(self.fpu.st.fpiar, 0x104)

    def test_exception_persists(self):
        """FPU 7.2.2, 7.4.2.5: the acknowledge does not clear it; FSAVE and
        EXC PEND set in the frame do (figure 5-6)."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        self.load(0, fin(0, 1, 0))
        self.load(1, fin(0, 0, 0))
        self.m.fgen(cmd(0, 1, 0, OP['FDIV']))                       # 1/0
        self.fpu.tick(SLOW + 1)
        for _ in range(2):
            with self.assertRaises(CPException) as e:
                self.m.fcond(0x0F)
            self.assertEqual((e.exception.kind, e.exception.vector), ('pre', 50))
        handler(self.m)
        self.assertEqual(self.m.fcond(0x0F), 1)

    def test_exception_holds_the_cu(self):
        """FPU 6.1: an exception in the APU is reported before the CU's
        instruction runs; FSAVE carries that instruction in the idle frame,
        and FRESTORE gives it back."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        self.load(0, fin(0, 1, 0))
        self.load(1, fin(0, 0, 0))
        self.m.pc = 0x200
        self.m.fgen(cmd(0, 1, 0, OP['FDIV']))                       # FP0 = 1/0: DZ
        self.m.pc = 0x204
        self.m.fgen(cmd(2, F.FMT_S, 2, OP['FMOVE']), ('imm', F.encode_s(fin(0, 9, 0)), 4))
        self.fpu.tick(SLOW + 2)
        self.assertIsNotNone(self.fpu.cu)                          # held
        self.assertEqual(F.decode_x(self.fpu.st.fp[2]).kind, F.decode_x(F.RESET_REG).kind)
        self.m.pc = 0x208
        with self.assertRaises(CPException) as e:
            self.m.fcond(0x0F)
        self.assertEqual((e.exception.kind, e.exception.vector), ('pre', 50))
        self.assertEqual(self.fpu.st.fpiar, 0x200)
        self.m.fsave(('predec', 7))
        frame = [self.m.mem.read(self.m.a[7] + 4 * i, 4) for i in range(15)]
        self.assertEqual(frame[0] >> 16, C.FW82_IDLE)
        self.assertEqual(frame[2] >> 16, cmd(2, F.FMT_S, 2, OP['FMOVE']))
        self.assertEqual(frame[2] >> 15 & 1, 0)                    # the CU holds it
        self.assertEqual(frame[14] >> 27 & 1, 0)                   # EXC PEND
        self.m.mem.write(self.m.a[7] + 0x38, 1, self.m.mem.read(self.m.a[7] + 0x38, 1) | 8)
        self.m.frestore(('postinc', 7))
        self.fpu.tick(SLOW + 2)
        self.assertEqual(F.decode_x(self.fpu.st.fp[2]), fin(0, 9, 0))
        self.assertEqual(self.fpu.st.fpiar, 0x204)
        self.assertEqual(self.m.fcond(0x0F), 1)

    def test_mid_instruction_report(self):
        """FPU 6.1, the FMUL.B example: the CU's instruction, still in its
        dialog, reports the APU's exception as a mid-instruction one. The
        handler's FSAVE takes a busy frame; after it, the instruction goes
        on."""
        self.m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        self.load(0, fin(0, 1, 0))
        self.load(1, fin(0, 0, 0))
        self.m.fgen(cmd(0, 1, 0, OP['FDIV']))
        with self.assertRaises(CPException) as e:
            self.m.fgen(cmd(2, F.FMT_B, 2, OP['FMOVE']), ('imm', 5, 1))
        self.assertEqual((e.exception.kind, e.exception.vector), ('mid', 50))
        self.m.fsave(('predec', 7))
        self.assertEqual(self.m.mem.read(self.m.a[7], 4) >> 16, C.FW82_BUSY)
        base = self.m.a[7]
        self.assertEqual(self.m.mem.read(base + 0xD4, 4) >> 27 & 1, 0)   # EXC PEND, last
        self.m.mem.write(base + 0xD4, 1, self.m.mem.read(base + 0xD4, 1) | 8)
        self.m.frestore(('postinc', 7))
        self.assertEqual(self.m.resume(), C.P_RELEASE)
        self.fpu.tick(SLOW + 2)
        self.assertEqual(F.decode_x(self.fpu.st.fp[2]), fin(0, 5, 0))

    def test_abort_spares_the_apu(self):
        """FPU 7.2.2: AB aborts the instruction in its window; the one in the
        APU completes."""
        self.load(0, fin(0, 2, 0))
        self.m.fgen(cmd(0, 0, 1, OP['FMOVE']))                      # FP1 = 2
        self.fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_D, 2, OP['FMOVE']))
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), 0x1608)
        self.fpu.write(C.CIR_CONTROL, 1)
        self.fpu.tick(SLOW + 2)
        self.assertEqual(F.decode_x(self.fpu.st.fp[1]), fin(0, 2, 0))
        self.assertIsNone(self.fpu.cu)
        self.assertEqual(self.fpu.read(C.CIR_RESPONSE), C.P_IDLE)

    def test_save_drains(self):
        """FPU table 6-5: FSAVE answers come-again while the APU computes,
        here twice over (the CU's instruction follows the APU's), then
        saves an idle frame with the CU empty."""
        self.load(0, fin(0, 2, 0))
        self.m.fgen(cmd(0, 0, 1, OP['FMUL']))
        self.m.fgen(cmd(2, F.FMT_X, 3, OP['FMOVE']), ('imm', x96(fin(0, 11, 0)), 12))
        self.fpu.log.clear()
        self.assertEqual(self.m.fsave(('predec', 7)), C.FW82_IDLE)
        self.assertIn(('R', C.CIR_SAVE, C.FW_AGAIN), self.fpu.log)
        self.assertEqual(self.m.mem.read(self.m.a[7] + 8, 4), 0xFFFFFFFF)   # CU empty
        self.assertEqual(F.decode_x(self.fpu.st.fp[3]), fin(0, 11, 0))
        self.m.frestore(('postinc', 7))


# ---------------------------------------------------------------------------
# Programs on both chips
# ---------------------------------------------------------------------------
VALUES = [fin(0, 0, 0), fin(1, 0, 0), fin(0, 1, 0), fin(1, 3, -1), fin(0, 7, 4),
          fin(0, 5, 100), fin(1, 9, -200), fin(0, 0xFFFFFF, -30), fin(0, 1, 16300)]


def program(rnd, n, enable=0):
    prog = [('gen', cmd(4, 4, 0, 0), ('imm', enable, 4))] if enable else []
    for _ in range(n):
        k = rnd.random()
        r1, r2 = rnd.randrange(8), rnd.randrange(8)
        if k < 0.3:
            op = rnd.choice(['FADD', 'FSUB', 'FMUL', 'FDIV', 'FMOVE', 'FNEG', 'FSQRT', 'FABS'])
            prog.append(('gen', cmd(0, r1, r2, OP[op]), None))
        elif k < 0.65:
            op = rnd.choice(['FADD', 'FSUB', 'FMUL', 'FDIV', 'FMOVE', 'FCMP'])
            v = rnd.choice(VALUES)
            fmt = rnd.choice([F.FMT_S, F.FMT_D, F.FMT_X, F.FMT_L, F.FMT_W, F.FMT_B])
            try:
                img = {F.FMT_S: lambda: (F.encode_s(v), 4), F.FMT_D: lambda: (F.encode_d(v), 8),
                       F.FMT_X: lambda: (x96(v), 12), F.FMT_L: lambda: (rnd.randrange(1 << 32), 4),
                       F.FMT_W: lambda: (rnd.randrange(1 << 16), 2),
                       F.FMT_B: lambda: (rnd.randrange(256), 1)}[fmt]()
            except AssertionError:                  # not representable in S or D
                fmt, img = F.FMT_X, (x96(v), 12)
            prog.append(('gen', cmd(2, fmt, r2, OP[op]), ('imm',) + img))
        elif k < 0.8:
            fmt = rnd.choice([F.FMT_S, F.FMT_D, F.FMT_X, F.FMT_L])
            prog.append(('out', cmd(3, fmt, r1, 0), fmt))
        elif k < 0.88:
            prog.append(('cond', rnd.randrange(0x10), None))
        elif k < 0.94:
            prog.append(('gen', (7 << 13) | 0xFF, 'mem'))         # FMOVEM FP0-FP7,-(A6)
        else:
            en = rnd.choice([0, 0x0400, 0x2000, 0x1000, 0x0800, 0x3C00])
            prog.append(('gen', cmd(4, 4, 0, 0), ('imm', en, 4)))  # FMOVE #,FPCR
    prog.append(('cond', 0, None))                                # FNOP
    return prog


def run(fpu, prog):
    m = MPU(fpu)
    m.a[7], m.a[6], m.a[5] = 0x8000, 0x6000, 0x4000
    trail = []
    for i, (kind, word, ea) in enumerate(prog):
        m.pc = 0x1000 + 4 * i
        for attempt in range(4):
            try:
                if kind == 'gen':
                    m.fgen(word, ('predec', 6) if ea == 'mem' else ea)
                elif kind == 'out':
                    n = F.FMT_BYTES[ea]
                    m.fgen(word, ('predec', 5))
                    trail.append(('out', m.mem.read(m.a[5], n)))
                else:
                    trail.append(('cond', m.fcond(word)))
                break
            except CPException as e:
                trail.append(('exc', e.vector, fpu.st.fpiar))
                handler(m)
                if e.kind == 'mid' and kind != 'out':
                    m.resume(('predec', 6) if ea == 'mem' else ea)
                    break
                if e.kind == 'mid':
                    trail.append(('out', m.mem.read(m.a[5], F.FMT_BYTES[ea])))
                    break
    fpu.tick(1000)
    st = fpu.st
    return dict(regs=[st.fp[i].bits80() for i in range(8)], fpcr=st.fpcr,
                fpsr=st.fpsr, a=list(m.a), trail=trail,
                mem={k: v for k, v in m.mem.b.items() if k < 0x7000})   # not the frames


class Sequential(unittest.TestCase):
    """FPU 5.1.1: the MC68882 must look like the MC68881."""

    def test_random_programs(self):
        rnd = random.Random(68882)
        for t in range(600):
            prog = program(rnd, 30, rnd.choice([0, 0x0400, 0x3C00, 0x2400]))
            a = run(FPU881(latency=lambda c: 3), prog)
            b = run(FPU882(latency=lambda c: SLOW), prog)
            for k in a:
                self.assertEqual(a[k], b[k], f'program {t}: {k} differs')


if __name__ == '__main__':
    unittest.main()
