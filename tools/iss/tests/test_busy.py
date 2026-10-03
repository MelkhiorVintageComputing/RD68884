# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Busy frames (FPU 6.4.2.3, 7.5.4.3): FSAVE in the middle of a transfer,
other work on the FPU, FRESTORE, and the transfer finishing.

Each scenario runs on the golden model and on the ISS. The frame is opaque
(our own layout on both, and not the same one), so its bytes are left out;
everything else -- the registers, the transfers' results, the main
processor's view -- must agree.
"""

import unittest

from model import cpif as C, formats as F
from model.cpif import FPU881
from model.mpu import MPU
from model.xnum import fin
from iss.machine import Machine

FRAME_TOP = 0x8000                   # the kernel's stack: frames below it


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def x96(v):
    return F.encode_x(v).bits96()


def longs(v, n=3):
    return [(v >> (32 * (n - 1 - i))) & 0xFFFFFFFF for i in range(n)]


def prim(fpu):
    """The next primitive, past the come-again nulls an MPU polls through."""
    while True:
        p = fpu.read(C.CIR_RESPONSE)
        if p != C.P_WAIT:
            return p


def kernel(fpu, work):
    """A context switch: save, run `work` on the FPU, restore."""
    k = MPU(fpu)
    k.a[7] = FRAME_TOP
    fw = k.fsave(('predec', 7))
    # The user's registers, as a kernel keeps them across a switch.
    k.fgen((7 << 13) | (0 << 11) | 0xFF, ('predec', 7))         # FMOVEM FP0-FP7,-(A7)
    k.fgen(cmd(5, 7, 0, 0), ('predec', 7))                       # FPCR/FPSR/FPIAR
    work(k)
    fpu.tick(200)
    k.fgen(cmd(4, 7, 0, 0), ('postinc', 7))
    k.fgen((6 << 13) | (2 << 11) | 0xFF, ('postinc', 7))
    k.frestore(('postinc', 7))
    assert k.a[7] == FRAME_TOP
    return fw


def other_work(k):
    k.fgen(cmd(4, 4, 0, 0), ('imm', 0x30, 4))                   # FPCR: RM
    k.fgen(cmd(2, F.FMT_L, 0, 0), ('imm', 99, 4))               # FP0 = 99
    k.fgen(cmd(2, F.FMT_L, 3, 0x22), ('imm', 1, 4))             # FP3 += 1
    k.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))
    k.fgen(cmd(4, 2, 0, 0), ('imm', 0, 4))


def state(fpu):
    if isinstance(fpu, FPU881):
        regs = [fpu.st.fp[i].bits80() for i in range(8)]
        ctl = (fpu.st.fpcr, fpu.st.fpsr)
    else:
        regs = [fpu.fp_bits80(i) for i in range(8)]
        ctl = (fpu.fpcr, fpu.fpsr)
    return regs, ctl


class Busy(unittest.TestCase):

    def both(self, scenario):
        out = []
        for fpu in (FPU881(latency=lambda c: 3), Machine()):
            r = scenario(fpu)
            fpu.tick(400)
            out.append((r, state(fpu)))
        (rm, sm), (ri, si) = out
        self.assertEqual(rm, ri, 'results differ')
        self.assertEqual(sm, si, 'FPU states differ')
        return ri

    def test_mid_fmovem_in(self):
        regs = [fin(0, n, 0) for n in (11, 22)]
        img = [w for r in regs for w in longs(x96(r))]

        def s(fpu):
            fpu.write(C.CIR_COMMAND, (6 << 13) | (2 << 11) | 0xC0)   # FP0/FP1 from memory
            p = prim(fpu)
            sel = fpu.read(C.CIR_REGSEL)
            for w in img[:4]:
                fpu.write(C.CIR_OPERAND, w)
            fw = kernel(fpu, other_work)
            for w in img[4:]:
                fpu.write(C.CIR_OPERAND, w)
            return p, sel, fw, prim(fpu)
        self.assertEqual(self.both(s)[2], C.FW_BUSY)

    def test_mid_fmovem_out(self):
        def s(fpu):
            m = MPU(fpu)
            m.fgen(cmd(2, F.FMT_L, 2, 0), ('imm', 5, 4))
            m.fgen(cmd(2, F.FMT_L, 5, 0), ('imm', -7 & 0xFFFFFFFF, 4))
            fpu.tick(100)
            fpu.write(C.CIR_COMMAND, (7 << 13) | (2 << 11) | 0x24)   # FP2/FP5 to memory
            p = prim(fpu)
            sel = fpu.read(C.CIR_REGSEL)
            got = [fpu.read(C.CIR_OPERAND) for _ in range(2)]
            fw = kernel(fpu, other_work)
            got += [fpu.read(C.CIR_OPERAND) for _ in range(4)]
            return p, sel, fw, got, prim(fpu)
        self.assertEqual(self.both(s)[2], C.FW_BUSY)

    def test_mid_fmove_x_in(self):
        v = longs(x96(fin(1, 0x1234567, -9)))

        def s(fpu):
            fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_X, 4, 0x22))      # FADD.X <ea>,FP4
            p = prim(fpu)
            fpu.write(C.CIR_OPERAND, v[0])
            fw = kernel(fpu, other_work)
            for w in v[1:]:
                fpu.write(C.CIR_OPERAND, w)
            return p, fw, prim(fpu)
        self.assertEqual(self.both(s)[1], C.FW_BUSY)

    def test_mid_fmove_x_out(self):
        def s(fpu):
            m = MPU(fpu)
            m.fgen(cmd(2, F.FMT_L, 6, 0), ('imm', 31337, 4))
            fpu.tick(100)
            fpu.write(C.CIR_COMMAND, cmd(3, F.FMT_X, 6, 0))
            p = prim(fpu)
            got = [fpu.read(C.CIR_OPERAND)]
            fw = kernel(fpu, other_work)
            got += [fpu.read(C.CIR_OPERAND) for _ in range(2)]
            return p, fw, got, prim(fpu)
        self.assertEqual(self.both(s)[1], C.FW_BUSY)

    def test_mid_control_out(self):
        def s(fpu):
            m = MPU(fpu)
            m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0010, 4))
            fpu.write(C.CIR_COMMAND, cmd(5, 7, 0, 0))                # FPCR/FPSR/FPIAR out
            p = prim(fpu)
            got = [fpu.read(C.CIR_OPERAND)]
            fw = kernel(fpu, other_work)
            got += [fpu.read(C.CIR_OPERAND) for _ in range(2)]
            return p, fw, got, prim(fpu)
        self.assertEqual(self.both(s)[1], C.FW_BUSY)

    def test_interrupt_while_polling_move_out(self):
        """FPU 7.5.4.3: the handler saves while FMOVE out still computes, uses
        the FPU, restores; the main processor's next poll finds the transfer.
        Which frame it gets is timing: an idle one if the FPU had not yet
        taken the command (and FRESTORE starts it again), else a busy one."""
        def s(fpu):
            m = MPU(fpu)
            m.fgen(cmd(2, F.FMT_L, 0, 0), ('imm', 4321, 4))
            fpu.tick(100)
            done = []

            def handler(mpu):
                if done:
                    return
                done.append(kernel(fpu, other_work))
            m.poll_hook = handler
            m.fgen(cmd(3, F.FMT_L, 0, 0), ('dn', 6))
            return bool(done) and done[0] in (C.FW_IDLE, C.FW_BUSY), m.d[6]
        ok, d6 = self.both(s)
        self.assertTrue(ok)
        self.assertEqual(d6, 4321)

    def test_save_with_violation_is_idle(self):
        def s(fpu):
            fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_X, 1, 0))
            prim(fpu)
            fpu.read(C.CIR_OPERAND)                                  # a read now: a violation
            m = MPU(fpu)
            m.a[7] = FRAME_TOP
            fw = m.fsave(('predec', 7))
            flags = m.mem.read(m.a[7] + 0x18, 4)
            return fw, flags >> 31
        self.assertEqual(self.both(s)[0], C.FW_IDLE)


if __name__ == '__main__':
    unittest.main()
