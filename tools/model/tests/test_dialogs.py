# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The coprocessor-interface model against FPU section 7 and 6.4.

Each test runs an instruction through tools/model/mpu.py the way an MC68020
would, or drives the CIRs by hand where the point is an access the MC68020
would never make, and checks the primitives and their effects.
"""

import unittest

from model import cpif as C, formats as F, xnum as X
from model.arith import OP
from model.cpif import FPU881
from model.mpu import MPU, CPException
from model.xnum import fin


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def fmove_l_imm(m, value, reg):
    """FMOVE.L #value,FPreg"""
    return m.fgen(cmd(2, F.FMT_L, reg, OP['FMOVE']), ('imm', value & 0xFFFFFFFF, 4))


def fmove_l_out(m, reg, dn):
    return m.fgen(cmd(3, F.FMT_L, reg, 0), ('dn', dn))


class Basic(unittest.TestCase):

    def test_reset_frame_is_null(self):
        fpu = FPU881()
        m = MPU(fpu)
        m.a[7] = 0x8000
        self.assertEqual(m.fsave(('predec', 7)), C.FW_NULL)
        self.assertEqual(m.a[7], 0x8000 - 4)

    def test_register_to_register(self):
        """FPU 7.5.1.1 and figure 7-17: one null, CA = 0, releases the MPU."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fmove_l_imm(m, 3, 0)
        fmove_l_imm(m, 4, 1)
        fpu.log.clear()
        p = m.fgen(cmd(0, 0, 1, OP['FADD']))
        self.assertEqual(p, C.P_RELEASE)
        self.assertEqual(fpu.log, [('W', C.CIR_COMMAND, cmd(0, 0, 1, OP['FADD'])),
                                   ('R', C.CIR_RESPONSE, C.P_RELEASE)])
        fpu.tick(4)
        self.assertEqual(F.decode_x(fpu.st.fp[1]), fin(0, 7, 0))
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_IDLE)

    def test_external_to_register(self):
        """FPU 7.5.1.2, figure 7-18: evaluate EA ($9504), operand, null."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fpu.log.clear()
        fmove_l_imm(m, 0xFFFFFFFE, 2)
        reads = [v for (rw, cir, v) in fpu.log if cir == C.CIR_RESPONSE]
        self.assertEqual(reads[0], 0x9504)
        self.assertEqual(reads[-1] & 0x0800, 0x0800)       # a null, CA = 0
        self.assertEqual(reads[-1] >> 15, 0)
        fpu.tick(2)
        self.assertEqual(F.decode_x(fpu.st.fp[2]), fin(1, 2, 0))

    def test_pc_passed_when_exceptions_enabled(self):
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0200, 4))       # FMOVE.L #INEX2 enable,FPCR
        self.assertEqual(fpu.st.fpcr, 0x0200)
        m.pc = 0x2468
        fpu.log.clear()
        fmove_l_imm(m, 1, 0)
        self.assertEqual(fpu.log[1][2], 0xD504)          # PC = 1
        self.assertEqual(fpu.st.fpiar, 0x2468)
        m.pc = 0x3000
        fpu.tick(3)
        p = m.fgen(cmd(0, 0, 1, OP['FMOVE']))
        self.assertEqual(p, C.P_RELEASE_PC)
        self.assertEqual(fpu.st.fpiar, 0x3000)

    def test_move_out(self):
        """FPU 7.5.1.3, figure 7-20."""
        fpu = FPU881(latency=lambda c: 3)
        m = MPU(fpu)
        fmove_l_imm(m, 1234, 3)
        fpu.tick(4)
        fpu.log.clear()
        fmove_l_out(m, 3, 5)
        self.assertEqual(m.d[5], 1234)
        reads = [v for (rw, cir, v) in fpu.log if cir == C.CIR_RESPONSE]
        self.assertEqual(reads[0], C.P_WAIT)
        self.assertIn(0xB104, reads)
        self.assertEqual(reads[-1], C.P_IDLE)

    def test_move_out_extended_and_packed(self):
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fmove_l_imm(m, -5, 0)
        fpu.tick(2)
        m.a[0] = 0x4000
        m.fgen(cmd(3, F.FMT_X, 0, 0), ('ind', 0))
        self.assertEqual(m.mem.read(0x4000, 12), F.encode_x(fin(1, 5, 0)).bits96())
        m.d[1] = 3                                        # dynamic k = 3
        m.fgen(cmd(3, F.FMT_PDYN, 0, 1 << 4), ('ind', 0))
        self.assertEqual(m.mem.read(0x4000, 12), (1 << 95) | (5 << 64))   # -5.00E+0

    def test_control_registers(self):
        """FPU 7.5.1.4: no PC, FPIAR untouched."""
        fpu = FPU881()
        m = MPU(fpu)
        m.a[0] = 0x5000
        m.mem.write(0x5000, 12, (0x00000030 << 64) | (0x0F000000 << 32) | 0x1234)
        m.fgen(cmd(4, 7, 0, 0), ('ind', 0))
        self.assertEqual((fpu.st.fpcr, fpu.st.fpsr, fpu.st.fpiar), (0x30, 0x0F000000, 0x1234))
        m.fgen(cmd(5, 2, 0, 0), ('dn', 2))
        self.assertEqual(m.d[2], 0x0F000000)
        fpu.log.clear()
        m.fgen(cmd(5, 1, 0, 0), ('an', 3))               # FMOVE FPIAR,A3
        self.assertEqual(m.a[3], 0x1234)
        self.assertEqual(fpu.log[1][2], 0xB304)

    def test_fmovem(self):
        """FPU 7.5.1.5: static and dynamic lists, both directions."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        for i, v in enumerate((10, 20, 30)):
            fmove_l_imm(m, v, i)
            fpu.tick(2)
        m.a[7] = 0x9000
        # FMOVEM.X FP0-FP2,-(A7): mode 00, list bit 7 = FP7 ... bit 0 = FP0.
        m.fgen((7 << 13) | (0 << 11) | 0x07, ('predec', 7))
        self.assertEqual(m.a[7], 0x9000 - 36)
        self.assertEqual(m.mem.read(0x9000 - 36, 12), F.encode_x(fin(0, 10, 0)).bits96())
        self.assertEqual(m.mem.read(0x9000 - 12, 12), F.encode_x(fin(0, 30, 0)).bits96())
        # FMOVEM.X (A7)+,FP3-FP5 with a dynamic list in D4 (mode 11, FP0 = bit 7).
        m.d[4] = 0b00011100
        m.fgen((6 << 13) | (3 << 11) | (4 << 4), ('postinc', 7))
        self.assertEqual(m.a[7], 0x9000)
        self.assertEqual([F.decode_x(fpu.st.fp[i]) for i in (3, 4, 5)],
                         [fin(0, 10, 0), fin(0, 20, 0), fin(0, 30, 0)])

    def test_fmovem_waits_but_does_not_report(self):
        fpu = FPU881(latency=lambda c: 20)
        m = MPU(fpu)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))      # enable DZ
        fmove_l_imm(m, 1, 0)
        fpu.tick(30)
        m.fgen(cmd(2, F.FMT_L, 0, OP['FDIV']), ('imm', 0, 4))   # 1/0: DZ pending
        m.a[7] = 0x9000
        m.fgen((7 << 13) | 0x80, ('predec', 7))          # FMOVEM FP0,-(A7)
        self.assertTrue(fpu.exc_pend)                    # FPU 6.4.5: not reported
        with self.assertRaises(CPException) as e:
            m.fcond(0x00)                                # FNOP reports it
        self.assertEqual((e.exception.kind, e.exception.vector), ('pre', 50))
        self.assertEqual(m.fcond(0x00), 0)               # and is then clear


class Conditionals(unittest.TestCase):

    def test_predicates(self):
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fmove_l_imm(m, -3, 0)
        fpu.tick(2)
        self.assertEqual(m.fcond(0x14), 1)   # LT
        self.assertEqual(m.fcond(0x12), 0)   # GT
        self.assertEqual(m.fcond(0x0E), 1)   # NE
        self.assertEqual(m.fcond(0x34), 1)   # 1xxxxx aliases 0xxxxx (table 4-20)

    def test_bsun(self):
        """FPU 6.1.1 and 7.5.4.4: $5C30 with the PC."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fpu.st.fp[0] = F.Reg(0, 0x7FFF, 0xC000000000000000)
        m.fgen(cmd(0, 0, 0, OP['FTST']))
        fpu.tick(2)
        self.assertEqual(m.fcond(0x02), 0)   # OGT: aware, no BSUN
        self.assertEqual(fpu.st.fpsr & 0x8000, 0)
        self.assertEqual(m.fcond(0x12), 0)   # GT, BSUN trap disabled
        self.assertTrue(fpu.st.fpsr & 0x8000 and fpu.st.fpsr & 0x80)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x8000, 4))
        m.pc = 0x4444
        with self.assertRaises(CPException) as e:
            m.fcond(0x12)
        self.assertEqual(e.exception.vector, 48)
        self.assertEqual(fpu.st.fpiar, 0x4444)

    def test_waits_for_previous(self):
        fpu = FPU881(latency=lambda c: 10)
        m = MPU(fpu)
        fmove_l_imm(m, 0, 0)
        m.reads = 0
        self.assertEqual(m.fcond(0x01), 1)   # EQ, after the move completes
        self.assertGreater(m.reads, 3)       # null CA=1 IA=1 polled meanwhile


class Exceptions(unittest.TestCase):

    def test_pre_instruction(self):
        """FPU 7.5.4.1: a pending exception preempts the next instruction,
        which then reruns cleanly after the acknowledge."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x1000, 4))      # enable OVFL
        fpu.st.fp[0] = F.encode_x(fin(0, 1, 16000))
        m.fgen(cmd(0, 0, 0, OP['FMUL']))                 # 2^32000: overflow
        fpu.tick(2)
        self.assertTrue(fpu.exc_pend)
        with self.assertRaises(CPException) as e:
            fmove_l_imm(m, 1, 1)
        self.assertEqual((e.exception.kind, e.exception.vector), ('pre', 53))
        fmove_l_imm(m, 1, 1)                             # the RTE rerun
        fpu.tick(2)
        self.assertEqual(F.decode_x(fpu.st.fp[1]), fin(0, 1, 0))

    def test_mid_instruction(self):
        """FPU 7.5.4.2: FMOVE out stores, then reports."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x2000, 4))      # enable OPERR
        fpu.st.fp[0] = F.encode_x(fin(0, 1, 40))
        with self.assertRaises(CPException) as e:
            fmove_l_out(m, 0, 3)
        self.assertEqual((e.exception.kind, e.exception.vector), ('mid', 52))
        self.assertEqual(m.d[3], 0x7FFFFFFF)            # written before the report

    def test_illegal_command(self):
        """FPU 7.5.4.5: F-line through the response CIR."""
        fpu = FPU881()
        m = MPU(fpu)
        with self.assertRaises(CPException) as e:
            m.fgen(0x0045)
        self.assertEqual((e.exception.kind, e.exception.vector), ('pre', 11))
        fmove_l_imm(m, 1, 0)                             # and the FPU is idle again

    def test_protocol_violation(self):
        """FPU 6.1.12 case 1: an operand access when a command is expected."""
        fpu = FPU881()
        fpu.write(C.CIR_OPERAND, 0)
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_PROTOCOL)
        fpu.write(C.CIR_CONTROL, 2)
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_IDLE)

    def test_command_in_initial_phase_is_violation(self):
        fpu = FPU881()
        fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_L, 0, 0))
        fpu.write(C.CIR_COMMAND, cmd(2, F.FMT_L, 0, 0))
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_PROTOCOL)

    def test_reserved_cirs(self):
        """FPU 7.2: all ones, never a violation."""
        fpu = FPU881()
        for cir in (C.CIR_CONTROL, C.CIR_COMMAND, C.CIR_OPWORD, C.CIR_RSVD0C,
                    C.CIR_CONDITION, C.CIR_RSVD16):
            self.assertEqual(fpu.read(cir), 0xFFFF)
        self.assertEqual(fpu.read(C.CIR_OPADDR), 0xFFFFFFFF)
        fpu.write(C.CIR_OPWORD, 0x1234)
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_IDLE)


class ContextSwitch(unittest.TestCase):

    def test_idle_frame_round_trip_keeps_pending_exception(self):
        """FPU 6.4.2.2: the pending exception travels in the idle frame."""
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x0400, 4))
        fmove_l_imm(m, 1, 0)
        fpu.tick(2)
        m.fgen(cmd(2, F.FMT_L, 0, OP['FDIV']), ('imm', 0, 4))
        fpu.tick(2)
        m.a[7] = 0x8000
        self.assertEqual(m.fsave(('predec', 7)), C.FW_IDLE)
        self.assertEqual(m.a[7], 0x8000 - 28)
        flags = m.mem.read(0x8000 - 4, 4)
        self.assertEqual(flags >> 27 & 1, 0)               # EXC PEND, active low
        self.assertEqual(flags >> 28 & 7, C.PEND_NONE)
        self.assertFalse(fpu.exc_pend)                      # 6.4.3: cleared
        # The handler's own work, bracketed by the user-visible save and
        # restore of figure 6-7: FMOVEM FPCR/FPSR/FPIAR,-(A7) and back.
        m.fgen(cmd(5, 7, 0, 0), ('predec', 7))
        fmove_l_imm(m, 7, 2)
        fpu.tick(2)
        m.fgen(cmd(4, 7, 0, 0), ('postinc', 7))
        m.frestore(('postinc', 7))
        self.assertEqual(m.a[7], 0x8000)
        with self.assertRaises(CPException) as e:
            m.fcond(0)
        self.assertEqual(e.exception.vector, 50)

    def test_come_again_while_computing(self):
        fpu = FPU881(latency=lambda c: 12)
        m = MPU(fpu)
        fmove_l_imm(m, 1, 0)
        fpu.log.clear()
        m.a[7] = 0x8000
        self.assertEqual(m.fsave(('predec', 7)), C.FW_IDLE)
        saves = [v for (rw, cir, v) in fpu.log if cir == C.CIR_SAVE]
        self.assertEqual(saves[0], C.FW_AGAIN)
        self.assertEqual(F.decode_x(fpu.st.fp[0]), fin(0, 1, 0))

    def test_busy_frame_mid_fmovem(self):
        """A fault in the middle of FMOVEM: the kernel saves a busy frame, runs
        something else, restores, and the transfer finishes (doc/model.md)."""
        fpu = FPU881(latency=lambda c: 1)
        regs = [F.encode_x(fin(0, n, 0)) for n in (11, 22)]
        fpu.write(C.CIR_COMMAND, (6 << 13) | (2 << 11) | 0xC0)   # FP0/FP1 from memory
        self.assertEqual(fpu.read(C.CIR_RESPONSE), 0x810C)
        self.assertEqual(fpu.read(C.CIR_REGSEL), 0xC000)
        img = [(r.bits96() >> s) & 0xFFFFFFFF for r in regs for s in (64, 32, 0)]
        for w in img[:4]:
            fpu.write(C.CIR_OPERAND, w)
        # The page fault: the OS saves the FPU and gives it to another task.
        k = MPU(fpu)
        k.a[7] = 0x8000
        self.assertEqual(k.fsave(('predec', 7)), C.FW_BUSY)
        self.assertEqual(k.a[7], 0x8000 - 184)
        fmove_l_imm(k, 99, 0)
        fpu.tick(2)
        k.frestore(('postinc', 7))
        for w in img[4:]:
            fpu.write(C.CIR_OPERAND, w)
        self.assertEqual(fpu.read(C.CIR_RESPONSE), C.P_IDLE)
        self.assertEqual(fpu.st.fp[0], regs[0])
        self.assertEqual(fpu.st.fp[1], regs[1])

    def test_interrupt_while_polling_move_out(self):
        """FPU 7.5.4.3: an interrupt handler with FSAVE/FRESTORE between two
        polls of FMOVE out; the dialog resumes with the right primitive."""
        fpu = FPU881(latency=lambda c: 6)
        m = MPU(fpu)
        fmove_l_imm(m, 4321, 0)
        fpu.tick(8)
        done = []

        def handler(mpu):
            if done:
                return
            done.append(1)
            h = MPU(fpu)
            h.a[7] = 0x8000
            self.assertEqual(h.fsave(('predec', 7)), C.FW_BUSY)
            h.fgen((7 << 13) | (2 << 11) | 0x80, ('predec', 7))   # FMOVEM FP0,-(A7)
            fmove_l_imm(h, 5, 0)                                # clobbers FP0
            fpu.tick(8)
            h.fgen((6 << 13) | (2 << 11) | 0x80, ('postinc', 7))  # FMOVEM (A7)+,FP0
            h.frestore(('postinc', 7))
            self.assertEqual(h.a[7], 0x8000)
        m.poll_hook = handler
        fmove_l_out(m, 0, 6)
        self.assertTrue(done)
        self.assertEqual(m.d[6], 4321)

    def test_invalid_frame(self):
        fpu = FPU881()
        fpu.write(C.CIR_RESTORE, 0x3F18)
        self.assertEqual(fpu.read(C.CIR_RESTORE), C.FW_INVALID)
        fpu.write(C.CIR_CONTROL, 1)

    def test_nested_save_is_format_error(self):
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fmove_l_imm(m, 1, 0)
        fpu.tick(2)
        self.assertEqual(fpu.read(C.CIR_SAVE), C.FW_IDLE)
        fpu.read(C.CIR_OPERAND)                            # one of six
        self.assertEqual(fpu.read(C.CIR_SAVE), C.FW_INVALID)   # FPU 6.2.8
        fpu.write(C.CIR_CONTROL, 1)

    def test_null_restore_resets(self):
        fpu = FPU881(latency=lambda c: 1)
        m = MPU(fpu)
        fmove_l_imm(m, 1, 0)
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0x30, 4))
        m.a[0] = 0x100
        m.mem.write(0x100, 4, 0)
        m.frestore(('ind', 0))
        self.assertEqual(fpu.st.fp[0], F.RESET_REG)
        self.assertEqual(fpu.st.fpcr, 0)
        m.a[7] = 0x8000
        self.assertEqual(m.fsave(('predec', 7)), C.FW_NULL)


if __name__ == '__main__':
    unittest.main()
