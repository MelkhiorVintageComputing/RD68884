#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""TestFloat's vectors through the microcode.

    python3 tools/iss/check_testfloat.py [--level 1|2] [--only NAME] [--limit N]

tools/model/check_testfloat.py runs Berkeley TestFloat's vectors through the
golden model, with its skips and its reading of the flags. This runs the same
vectors, with the same skips, through the ISS instead: each one a short
program on the MC68020 driver --

    FMOVE.L #0,FPCR; FMOVE.X #src,FP0; FMOVE.X #dst,FP1
    FMOVE.L #fpcr,FPCR; FMOVE.L #0,FPSR
    <the instruction>                    FP0 to FP1, <ea> to FP1, or FP0 to (A0)
    FMOVE.L FPSR,D0; FMOVE.L #0,FPCR; FMOVE.X FP1,(A1)

-- so the dialogs, the unpacking and the packing are in the path too. The
remainder checks wait for M6.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from model import check_testfloat as T, formats as F  # noqa: E402
from model.mpu import MPU  # noqa: E402
from iss.machine import Machine  # noqa: E402


def cmd(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


class Res:
    def __init__(self, exc, mem):
        self.exc, self.mem = exc, mem


class St:
    def __init__(self, fp1):
        self.fp = [None, fp1]


class Iss:
    def __init__(self):
        self.m = MPU(Machine())
        self.m.a[7] = 0x8000

    def run(self, op, srcreg, dstreg, mode, prec, image=None, fmt=None, out=None):
        m = self.m
        m.mem.b.clear()
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))
        if srcreg is not None:
            m.fgen(cmd(2, F.FMT_X, 0, 0), ('imm', srcreg.bits96(), 12))
        if dstreg is not None:
            m.fgen(cmd(2, F.FMT_X, 1, 0), ('imm', dstreg.bits96(), 12))
        m.fgen(cmd(4, 4, 0, 0), ('imm', (prec << 6) | (mode << 4), 4))
        m.fgen(cmd(4, 2, 0, 0), ('imm', 0, 4))
        mem = None
        if out is not None:
            m.a[0] = 0x4000
            m.fgen(cmd(3, out, 0, 0), ('ind', 0))
            n = F.FMT_BYTES[out]
            mem = m.mem.read(0x4000, n)
        elif fmt is not None:
            m.fgen(cmd(2, fmt, 1, op), ('imm', image, F.FMT_BYTES[fmt]))
        else:
            m.fgen(cmd(0, 0, 1, op))
        m.fgen(cmd(5, 2, 0, 0), ('dn', 0))
        fpsr = m.d[0]
        m.fgen(cmd(4, 4, 0, 0), ('imm', 0, 4))
        m.a[1] = 0x5000
        m.fgen(cmd(3, F.FMT_X, 1, 0), ('ind', 1))
        fp1 = F.reg_from_bits96(m.mem.read(0x5000, 12))
        return Res((fpsr >> 8) & 0xFF, mem), St(fp1)


def main():
    iss = Iss()
    T.run = iss.run
    # FREM arrives with M6; until then the microcode answers it F-line.
    every = T.checks
    T.checks = lambda: [c for c in every() if not c[0].endswith('_rem')]
    return T.main()


if __name__ == '__main__':
    sys.exit(main())
