#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Instruction clock counts against FPU section 8 (doc/timing-divergences.md).

    python3 tools/cycles.py gen OUT.S [--only TEXT] [--model 68882]
    python3 tools/cycles.py check DUMP DUMP_FAST [--sync DUMP] [--slow DUMP]
                                  [--doc DOC] [--freeze]
    python3 tools/cycles.py check82 DUMP [--doc DOC] [--freeze]

Adapted from RD68021's tools/cycles.py. `gen` writes a program for
sim/tb/sys_tb.sv: RD68021 as the MC68020 and RD68884 on a 32-bit port, the
system the manual's tables assume (FPU 8.5.1: an MC68020 on a 32-bit bus with
no wait states). Every row times its instruction with the clock count sys_tb
keeps at RES+$88, in CPU clocks:

    <setup>; FNOP                  the FPU idle, as FPU 8.1 assumes
    move.l CNT,D6
    <the instruction>
    FNOP                           waits for whatever the instruction released
    move.l CNT,D7

less the same with no instruction (the first row, CALIBRATE). The FNOP after
it is what puts a released computation's tail in the count: Table 8-2's times
are the whole instruction, the part the MC68881 lets the MPU overlap included
(FPU 8.2). RD68021's instruction cache is left off (its CACR is zero from
reset), as Table 8-2 assumes: "instruction prefetches do not hit in the
MC68020 cache (or it is disabled)" (FPU 8.5.1).

`check` reads the dumps of the results: one at the design's clocks (the FPU's
core three times the bus clock, sys_tb's default), one with the FPU's core
thirty times as fast, where what is left is close to the main processor and
the protocol alone, and optionally one with the same-clock BIU (BUS_SYNC = 1,
no wait states: the FPU on the CPU's clock) and one with the asynchronous BIU
at that clock, for reference (doc/bus-timing.md). It compares the design's
count with the frozen one (tools/cycles.frozen), and the same-clock count with
tools/cycles-sync.frozen, and fails if any row moved -- faster or slower, a
change someone has to look at and then freeze with --freeze -- and it
regenerates the tables in DOC between their markers.

For the arithmetic rows it also runs the instruction on the ISS alone
(iss_clocks): RD68884's core clocks, with a main processor that never waits.
That is the comparison at the same clock as the manual's.

The manual's number, MAN, is its cache case where it has one (Tables 8-6, 8-7,
8-8) and Table 8-2's otherwise, plus Table 8-1's cache-case effective-address
time for the operand's mode, (An) 2. The sum is written out in the row.
"""

import os
import struct
import sys
from fractions import Fraction

FROZEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles.frozen')
FROZEN_SYNC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles-sync.frozen')
FROZEN82 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles-68882.frozen')

RES = 0xC000
CNT = RES + 0x88
OUT = RES + 0x100

# Table 8-2, MC68881 overall execution times (FPU p. 8-14; checked against the
# page image): FPn to FPm, then integer, single, double, extended, packed.
T82 = {
    'FABS':    (35, 62, 54, 60, 58, 872),
    'FACOS':   (625, 652, 644, 650, 648, 1462),
    'FADD':    (51, 80, 72, 78, 76, 888),
    'FASIN':   (581, 608, 600, 606, 604, 1418),
    'FATAN':   (403, 430, 422, 428, 426, 1240),
    'FATANH':  (693, 720, 712, 718, 716, 1530),
    'FCMP':    (33, 62, 54, 60, 58, 870),
    'FCOS':    (391, 418, 410, 416, 414, 1228),
    'FCOSH':   (607, 634, 626, 632, 630, 1444),
    'FDIV':    (103, 132, 124, 130, 128, 940),
    'FETOX':   (497, 524, 516, 522, 520, 1334),
    'FETOXM1': (545, 572, 564, 570, 568, 1382),
    'FGETEXP': (45, 72, 64, 70, 68, 882),
    'FGETMAN': (31, 58, 50, 56, 54, 868),
    'FINT':    (55, 82, 74, 80, 78, 892),
    'FINTRZ':  (55, 82, 74, 80, 78, 892),
    'FLOGN':   (525, 552, 544, 550, 548, 1362),
    'FLOGNP1': (571, 598, 590, 596, 594, 1408),
    'FLOG10':  (581, 608, 600, 606, 604, 1418),
    'FLOG2':   (581, 608, 600, 606, 604, 1418),
    'FMOD':    (70, 99, 91, 97, 95, 907),
    'FMOVE':   (33, 60, 52, 58, 56, 870),
    'FMUL':    (71, 100, 92, 98, 96, 908),
    'FNEG':    (35, 62, 54, 60, 58, 872),
    'FREM':    (100, 129, 121, 127, 125, 937),
    'FSCALE':  (41, 70, 62, 68, 66, 878),
    'FSGLDIV': (69, 98, 90, 96, 94, 906),
    'FSGLMUL': (59, 88, 80, 86, 84, 896),
    'FSIN':    (391, 418, 410, 416, 414, 1228),
    'FSINCOS': (451, 478, 470, 476, 474, 1288),
    'FSINH':   (687, 714, 706, 712, 710, 1524),
    'FSQRT':   (107, 134, 126, 132, 130, 944),
    'FSUB':    (51, 80, 72, 78, 76, 888),
    'FTAN':    (473, 500, 492, 498, 496, 1310),
    'FTANH':   (661, 688, 680, 686, 684, 1498),
    'FTENTOX': (567, 594, 586, 592, 590, 1404),
    'FTST':    (33, 60, 52, 58, 56, 870),
    'FTWOTOX': (567, 594, 586, 592, 590, 1404),
}
T82_OUT = (100, 80, 86, 72, 2002)              # FMOVE to memory: I S D X P

# Table 8-3, MC68882 overall execution times (FPU p. 8-15; checked against the
# page image): tail and total for FPn to FPm, integer, single, double,
# extended, packed. The head is the column's (H83), but for FMOVE (T83_FMOVE).
H83 = (17, 21, 30, 36, 42, 13)
T83 = {
    'FABS':    ((17, 38), (28, 68), (20, 51), (20, 57), (20, 63), (811, 893)),
    'FACOS':   ((607, 628), (618, 658), (610, 641), (610, 647), (610, 653), (1401, 1483)),
    'FADD':    ((35, 56), (54, 94), (38, 69), (38, 75), (38, 81), (827, 909)),
    'FASIN':   ((563, 584), (574, 614), (566, 597), (566, 603), (566, 609), (1357, 1439)),
    'FATAN':   ((385, 406), (396, 436), (388, 419), (388, 425), (388, 431), (1179, 1261)),
    'FATANH':  ((675, 696), (686, 726), (678, 709), (678, 715), (678, 721), (1469, 1551)),
    'FCMP':    ((17, 38), (36, 76), (20, 51), (20, 57), (20, 63), (809, 891)),
    'FCOS':    ((373, 394), (384, 424), (376, 407), (376, 413), (376, 419), (1167, 1249)),
    'FCOSH':   ((589, 610), (600, 640), (592, 623), (592, 629), (592, 635), (1383, 1465)),
    'FDIV':    ((87, 108), (106, 146), (90, 121), (90, 127), (90, 133), (879, 961)),
    'FETOX':   ((479, 500), (490, 530), (482, 513), (482, 519), (482, 525), (1273, 1355)),
    'FETOXM1': ((527, 548), (538, 578), (530, 561), (530, 567), (530, 573), (1321, 1403)),
    'FGETEXP': ((27, 48), (38, 78), (30, 61), (30, 67), (30, 73), (821, 903)),
    'FGETMAN': ((13, 34), (24, 64), (16, 47), (16, 53), (16, 59), (807, 889)),
    'FINT':    ((37, 58), (48, 88), (40, 71), (40, 77), (40, 83), (831, 913)),
    'FINTRZ':  ((37, 58), (48, 88), (40, 71), (40, 77), (40, 83), (831, 913)),
    'FLOGN':   ((507, 528), (518, 558), (510, 541), (510, 547), (510, 553), (1301, 1383)),
    'FLOGNP1': ((553, 574), (564, 604), (556, 587), (556, 593), (556, 599), (1347, 1429)),
    'FLOG10':  ((563, 584), (574, 614), (566, 597), (566, 603), (566, 609), (1357, 1439)),
    'FLOG2':   ((563, 584), (574, 614), (566, 597), (566, 603), (566, 609), (1357, 1439)),
    'FMOD':    ((54, 75), (73, 113), (57, 88), (57, 94), (57, 100), (846, 928)),
    'FMUL':    ((55, 76), (74, 114), (58, 89), (58, 95), (58, 101), (847, 929)),
    'FNEG':    ((17, 38), (28, 68), (20, 51), (20, 57), (20, 63), (811, 893)),
    'FREM':    ((84, 105), (103, 143), (87, 118), (87, 124), (87, 130), (876, 958)),
    'FSCALE':  ((25, 46), (44, 84), (28, 59), (28, 65), (28, 71), (817, 899)),
    'FSGLDIV': ((53, 74), (72, 112), (56, 87), (56, 93), (56, 99), (845, 927)),
    'FSGLMUL': ((43, 64), (62, 102), (46, 77), (46, 83), (46, 89), (835, 917)),
    'FSIN':    ((373, 394), (384, 424), (376, 407), (376, 413), (376, 419), (1167, 1249)),
    'FSINCOS': ((433, 454), (444, 484), (436, 467), (436, 473), (436, 479), (1227, 1309)),
    'FSINH':   ((669, 690), (680, 720), (672, 703), (672, 709), (672, 715), (1463, 1545)),
    'FSQRT':   ((89, 110), (100, 140), (92, 123), (92, 129), (92, 135), (883, 965)),
    'FSUB':    ((35, 56), (54, 94), (38, 69), (38, 75), (38, 81), (827, 909)),
    'FTAN':    ((455, 476), (466, 506), (458, 489), (458, 495), (458, 501), (1249, 1331)),
    'FTANH':   ((643, 664), (654, 694), (646, 677), (646, 683), (646, 689), (1437, 1519)),
    'FTENTOX': ((549, 570), (560, 600), (552, 583), (552, 589), (552, 595), (1343, 1425)),
    'FTST':    ((15, 36), (26, 66), (18, 49), (18, 55), (18, 61), (809, 891)),
    'FTWOTOX': ((549, 570), (560, 600), (552, 583), (552, 589), (552, 595), (1343, 1425)),
}
# FMOVE to FPn (no conflict; "*": no tail, fully concurrent), to memory,
# FMOVECR: (H, T, total) per column, None where the table has a dash.
T83_FMOVE = ((21, 0, 21), (21, 8, 48), (34, 0, 34), (40, 0, 40), (46, 0, 46), (13, 809, 891))
T83_OUT = ((0, 0, 110), (38, 0, 38), (44, 0, 44), (50, 0, 50), (0, 0, 2006))   # I S D X P
T83_MOVECR = (10, 0, 32)
FMTS = ('l', 's', 'd', 'x', 'p')
FMT_NAME = {'l': 'integer (.L)', 's': 'single', 'd': 'double', 'x': 'extended', 'p': 'packed'}
EA_AN = 2                                       # Table 8-1, (An), cache case

DYADIC = {'FADD', 'FCMP', 'FDIV', 'FMOD', 'FMUL', 'FREM', 'FSCALE', 'FSGLDIV',
          'FSGLMUL', 'FSUB'}

# The source operand: in range and normal, as FPU 8.5.1 assumes ("typical").
# FP1, the destination of the dyadic operations, holds 1.75 (100.25 for FMOD
# and FREM). The integer column uses 3, and is left out where no integer is in
# the domain but 0 and +-1.
ARG = {'FSCALE': Fraction(3), 'FMOD': Fraction(3), 'FREM': Fraction(3),
       'FINT': Fraction(11, 4), 'FINTRZ': Fraction(11, 4)}
DEFAULT_ARG = Fraction(3, 4)
INT_ARG = 3
NO_INT = {'FACOS', 'FASIN', 'FATANH'}


# ---- operand images ------------------------------------------------------------

def img_x(v):
    v = Fraction(v)
    if v == 0:
        return [0, 0, 0]
    s = 1 if v < 0 else 0
    v = abs(v)
    e = v.numerator.bit_length() - v.denominator.bit_length()
    while Fraction(2) ** e > v:
        e -= 1
    while Fraction(2) ** (e + 1) <= v:
        e += 1
    m = v / Fraction(2) ** e * 2 ** 63
    assert m.denominator == 1
    m = int(m)
    return [((s << 15) | (e + 16383)) << 16, m >> 32, m & 0xFFFFFFFF]


def img_d(v):
    b = struct.unpack('>Q', struct.pack('>d', float(v)))[0]
    return [b >> 32, b & 0xFFFFFFFF]


def img_s(v):
    return [struct.unpack('>I', struct.pack('>f', float(v)))[0]]


def img_l(v):
    return [int(v) & 0xFFFFFFFF]


def img_p(v):
    """Packed decimal (FPU 3.1.3): exact here, the arguments being short."""
    v = Fraction(v)
    s = 1 if v < 0 else 0
    v = abs(v)
    e = 0
    while v >= 10:
        v /= 10
        e += 1
    while v < 1:
        v *= 10
        e -= 1
    digits = []
    for _ in range(17):
        d = int(v)
        digits.append(d)
        v = (v - d) * 10
    assert v == 0, 'not exact in 17 digits'
    se = 1 if e < 0 else 0
    e = abs(e)
    w0 = (s << 31) | (se << 30) | ((e // 100) << 24) | (((e // 10) % 10) << 20) | ((e % 10) << 16) | digits[0]
    frac = 0
    for d in digits[1:]:
        frac = (frac << 4) | d
    return [w0, frac >> 32, frac & 0xFFFFFFFF]


IMG = {'l': img_l, 's': img_s, 'd': img_d, 'x': img_x, 'p': img_p}


# ---- the rows ---------------------------------------------------------------------
#
# (name, setup lines, timed lines, data longs for A0, manual, how it adds up)
# A0 points at the row's data, A1 at a scratch buffer, A2 at a second one.

# The arithmetic rows also carry what the ISS needs to run them on its own
# (iss_clocks): FP0/FP1's values, the command word, the effective address
# (None, ('ind', 0) for A0, ('ind', 1) for A1, ('dn', 0)), A0's data, D0.
ISS_SPEC = {}
MAN83 = {}                     # row -> Table 8-3's (H, T, total), EA time added
FMT_CODE = {'l': 0, 's': 1, 'x': 2, 'p': 3, 'd': 5}       # FPU table 4-12
OPMODE = dict(FMOVE=0x00, FINT=0x01, FSINH=0x02, FINTRZ=0x03, FSQRT=0x04,
              FLOGNP1=0x06, FETOXM1=0x08, FTANH=0x09, FATAN=0x0A, FASIN=0x0C,
              FATANH=0x0D, FSIN=0x0E, FTAN=0x0F, FETOX=0x10, FTWOTOX=0x11,
              FTENTOX=0x12, FLOGN=0x14, FLOG10=0x15, FLOG2=0x16, FABS=0x18,
              FCOSH=0x19, FNEG=0x1A, FACOS=0x1C, FCOS=0x1D, FGETEXP=0x1E,
              FGETMAN=0x1F, FDIV=0x20, FMOD=0x21, FADD=0x22, FMUL=0x23,
              FSGLDIV=0x24, FREM=0x25, FSCALE=0x26, FSGLMUL=0x27, FSUB=0x28,
              FSINCOS=0x32, FCMP=0x38, FTST=0x3A)          # FPU table 4-13; FSINCOS to FP2:FP1


def cmdw(opclass, rx, ry, ext):
    return (opclass << 13) | (rx << 10) | (ry << 7) | ext


def arith_rows():
    rows = []

    def row(name, setup, timed, data, man, how, iss, man83=None):
        rows.append((name, setup, timed, data, man, how))
        ISS_SPEC[name] = iss
        if man83 is not None:
            MAN83[name] = man83

    def m83(op, k, ea=0):
        """Table 8-3 for column k (0 FPn, then I S D X P), the effective
        address's time added to the head and the total (note ***)."""
        if op == 'FMOVE':
            h, t, tot = T83_FMOVE[k]
        else:
            (t, tot), h = T83[op][k], H83[k]
        return (h + ea, t, tot + ea)

    for op, t in T82.items():
        a = ARG.get(op, DEFAULT_ARG)
        dst = Fraction(401, 4) if op in ('FMOD', 'FREM') else Fraction(7, 4)
        setup = [f'fmove.x {lbl_x(a)},%fp0', f'fmove.x {lbl_x(dst)},%fp1']
        regs = [(0, a), (1, dst)]
        m = op.lower()
        ry = 0 if op == 'FTST' else 1
        if op == 'FSINCOS':
            reg = ['fsincos.x %fp0,%fp2:%fp1']
            mem = lambda f: [f'fsincos.{f} (%a0),%fp2:%fp1']
        elif op == 'FTST':
            reg = ['ftst.x %fp0']
            mem = lambda f: [f'ftst.{f} (%a0)']
        else:
            reg = [f'{m}.x %fp0,%fp1']
            mem = lambda f: [f'{m}.{f} (%a0),%fp1']
        rname = {'FTST': 'FTST.X FP0', 'FSINCOS': 'FSINCOS.X FP0,FP2:FP1'}.get(op, f'{op}.X FP0,FP1')
        row(rname, setup, reg, [], t[0], f'{t[0]}',
            dict(regs=regs, cmd=cmdw(0, 0, ry, OPMODE[op]), ea=None), m83(op, 0))
        for k, f in enumerate(FMTS):
            if f == 'l' and op in NO_INT:
                continue
            v = INT_ARG if f == 'l' else a
            if op == 'FSINCOS':
                name = f'FSINCOS.{f.upper()} (A0),FP2:FP1'
            elif op == 'FTST':
                name = f'FTST.{f.upper()} (A0)'
            else:
                name = f'{op}.{f.upper()} (A0),FP1'
            row(name, setup, mem(f), IMG[f](v), t[k + 1] + EA_AN, f'{t[k + 1]} + (An) {EA_AN}',
                dict(regs=regs, cmd=cmdw(2, FMT_CODE[f], ry, OPMODE[op]), ea=('ind', 0),
                     data=IMG[f](v)), m83(op, k + 1, EA_AN))
    # Footnote **: an MPU data register as the source, five clocks less.
    row('FADD.L D0,FP1', ['fmove.x ' + lbl_x(Fraction(7, 4)) + ',%fp1', 'moveq #3,%d0'],
        ['fadd.l %d0,%fp1'], [], T82['FADD'][1] - 5, f"{T82['FADD'][1]} - 5",
        dict(regs=[(1, Fraction(7, 4))], cmd=cmdw(2, 0, 1, OPMODE['FADD']), ea=('dn', 0), d0=3),
        (H83[1], T83['FADD'][1][0], T83['FADD'][1][1] - 5))
    # FMOVE to memory, from FP0 = 3.140625 (and to D0: two clocks less).
    pi = Fraction(201, 64)
    setup = [f'fmove.x {lbl_x(pi)},%fp0']
    for k, f in enumerate(FMTS):
        ins = f'fmove.{f} %fp0,(%a1)' + ('{#17}' if f == 'p' else '')
        row(f'FMOVE.{f.upper()} FP0,(A1)' + (' {#17}' if f == 'p' else ''), setup, [ins], [],
            T82_OUT[k] + EA_AN, f'{T82_OUT[k]} + (An) {EA_AN}',
            dict(regs=[(0, pi)], cmd=cmdw(3, FMT_CODE[f], 0, 17 if f == 'p' else 0), ea=('ind', 1)),
            T83_OUT[k][:2] + (T83_OUT[k][2] + EA_AN,))
    row('FMOVE.L FP0,D0', setup, ['fmove.l %fp0,%d0'], [], T82_OUT[0] - 2, f'{T82_OUT[0]} - 2',
        dict(regs=[(0, pi)], cmd=cmdw(3, 0, 0, 0), ea=('dn', 0)),
        T83_OUT[0][:2] + (T83_OUT[0][2] - 2,))
    row('FMOVECR #0,FP1', [], ['fmovecr #0,%fp1'], [], 29, '29',
        dict(regs=[], cmd=cmdw(2, 7, 1, 0), ea=None), T83_MOVECR)
    return rows


def iss_clocks():
    """Each arithmetic row on the ISS alone, in RD68884 core clocks: from the
    command word to the instruction's end, as a following FNOP finds it, less
    a lone FNOP. The main processor is tools/model/mpu.py's, which never
    waits, and every bus access takes the ISS's BUS_CLOCKS: what is left is
    the microcode, at the same clock as the manual's numbers."""
    sys.path[:0] = [os.path.dirname(os.path.abspath(__file__))]
    from model.mpu import MPU
    from iss.machine import Machine
    out = {}
    for name, sp in ISS_SPEC.items():
        mpu = MPU(Machine())
        mpu.a[7] = 0x8000
        for r, v in sp['regs']:
            w = img_x(v)
            mpu.fgen(cmdw(2, 2, r, 0), ('imm', (w[0] << 64) | (w[1] << 32) | w[2], 12))
        mpu.fpu.tick(2000)
        for i, w in enumerate(sp.get('data', [])):
            mpu.mem.write(0x4000 + 4 * i, 4, w)
        mpu.a[0], mpu.a[1], mpu.d[0] = 0x4000, 0x5000, sp.get('d0', 0)
        c = mpu.fpu.clocks
        mpu.fcond(0)
        base = mpu.fpu.clocks - c
        c = mpu.fpu.clocks
        mpu.fgen(sp['cmd'], sp['ea'])
        mpu.fcond(0)
        out[name] = mpu.fpu.clocks - c - base
    return out


def control_rows():
    z = [0, 0, 0]
    x8 = [w for v in (1, 2, 3, 4, 5, 6, 7, 8) for w in img_x(Fraction(v))]
    fill = ['fmove.l #1,%fp0', 'fmove.l #2,%fp1', 'fmove.l #3,%fp2', 'fmove.l #4,%fp3']
    return [
        # Table 8-6, cache case
        ('FMOVE.L FPCR,D0', [], ['fmove.l %fpcr,%d0'], [], 31, '31'),
        ('FMOVE.L FPCR,(A1)', [], ['fmove.l %fpcr,(%a1)'], [], 33 + EA_AN, f'33 + (An) {EA_AN}'),
        ('FMOVE.L D0,FPCR', ['moveq #0,%d0'], ['fmove.l %d0,%fpcr'], [], 28, '28'),
        ('FMOVE.L (A0),FPCR', [], ['fmove.l (%a0),%fpcr'], [0], 33 + EA_AN, f'33 + (An) {EA_AN}'),
        ('FMOVE.L #0,FPCR', [], ['fmove.l #0,%fpcr'], [], 30, '30'),
        ('FMOVEM.L FPCR/FPSR/FPIAR,(A1)', [], ['fmovem.l %fpcr/%fpsr/%fpiar,(%a1)'], [],
         27 + 18 + EA_AN, f'27 + 6×3 + (An) {EA_AN}'),
        ('FMOVEM.L (A0),FPCR/FPSR/FPIAR', [], ['fmovem.l (%a0),%fpcr/%fpsr/%fpiar'], z,
         27 + 18 + EA_AN, f'27 + 6×3 + (An) {EA_AN}'),
        ('FMOVEM.X FP0,(A1)', fill, ['fmovem.x %fp0,(%a1)'], [], 37 + 25 + EA_AN,
         f'37 + 25×1 + (An) {EA_AN}'),
        ('FMOVEM.X FP0-FP3,(A1)', fill, ['fmovem.x %fp0-%fp3,(%a1)'], [], 37 + 100 + EA_AN,
         f'37 + 25×4 + (An) {EA_AN}'),
        ('FMOVEM.X (A0),FP0', [], ['fmovem.x (%a0),%fp0'], x8, 35 + 31 + EA_AN,
         f'35 + 31×1 + (An) {EA_AN}'),
        ('FMOVEM.X (A0),FP0-FP3', [], ['fmovem.x (%a0),%fp0-%fp3'], x8, 35 + 124 + EA_AN,
         f'35 + 31×4 + (An) {EA_AN}'),
        ('FMOVEM.X D0,(A1), 4 registers', fill + ['move.l #0xF0,%d0'], ['fmovem.x %d0,(%a1)'], [],
         51 + 100 + EA_AN, f'51 + 25×4 + (An) {EA_AN}'),
        ('FMOVEM.X (A0),D0, 4 registers', ['move.l #0xF0,%d0'], ['fmovem.x (%a0),%d0'], x8,
         49 + 124 + EA_AN, f'49 + 31×4 + (An) {EA_AN}'),
        # Table 8-7, cache case. FTST of zero beforehand: EQ true, NE false.
        ('FBEQ.W, taken', ['ftst.x ' + lbl_x(0)], ['fbeq 7f', '7:'], [], 20, '20'),
        ('FBNE.W, not taken', ['ftst.x ' + lbl_x(0)], ['fbne 7f', '7:'], [], 18, '18'),
        ('FBEQ.L, taken', ['ftst.x ' + lbl_x(0)], ['fbeq.l 7f', '7:'], [], 20, '20'),
        ('FBNE.L, not taken', ['ftst.x ' + lbl_x(0)], ['fbne.l 7f', '7:'], [], 18, '18'),
        ('FDBEQ, true, not taken', ['ftst.x ' + lbl_x(0), 'moveq #5,%d0'], ['fdbeq %d0,7f', '7:'],
         [], 20, '20'),
        ('FDBNE, false, not taken', ['ftst.x ' + lbl_x(0), 'moveq #0,%d0'], ['fdbne %d0,7f', '7:'],
         [], 24, '24'),
        ('FDBNE, false, taken', ['ftst.x ' + lbl_x(0), 'moveq #5,%d0'], ['fdbne %d0,7f', '7:'],
         [], 20, '20'),
        ('FNOP', [], ['fnop'], [], 18, '18'),
        ('FSEQ D0', ['ftst.x ' + lbl_x(0)], ['fseq %d0'], [], 18, '18'),
        ('FSEQ (A1)', ['ftst.x ' + lbl_x(0)], ['fseq (%a1)'], [], 20 + EA_AN, f'20 + (An) {EA_AN}'),
        ('FTRAPEQ, taken, and RTE', ['ftst.x ' + lbl_x(0)], ['ftrapeq'], [], 39 + 21,
         '39 + UM 8 RTE (six word) 21'),
        ('FTRAPNE, not taken', ['ftst.x ' + lbl_x(0)], ['ftrapne'], [], 18, '18'),
        ('FTRAPNE.W, not taken', ['ftst.x ' + lbl_x(0)], ['ftrapne.w #1'], [], 20, '20'),
        ('FTRAPNE.L, not taken', ['ftst.x ' + lbl_x(0)], ['ftrapne.l #1'], [], 22, '22'),
        # Table 8-8, MC68881, cache case
        ('FSAVE (A1), null', ['frestore nullframe', NO_FNOP], ['fsave (%a1)'], [], 16 + EA_AN,
         f'16 + (An) {EA_AN}'),
        ('FSAVE (A1), idle', ['fmove.l #1,%fp2'], ['fsave (%a1)'], [], 52 + EA_AN,
         f'52 + (An) {EA_AN}'),
        ('FRESTORE (A0), null', [], ['frestore (%a0)'], [0], 21 + EA_AN, f'21 + (An) {EA_AN}'),
        ('FRESTORE (A2), idle', ['fmove.l #1,%fp2', 'fnop', 'fsave (%a2)'], ['frestore (%a2)'], [],
         57 + EA_AN, f'57 + (An) {EA_AN}'),
    ]


# A setup that must not end with FNOP: FNOP is an FPU instruction, after which
# FSAVE no longer gives a null frame.
NO_FNOP = '!no-fnop'

_xconst = {}


def lbl_x(v):
    """An extended constant in the data section, by label."""
    v = Fraction(v)
    if v not in _xconst:
        _xconst[v] = f'xc{len(_xconst)}'
    return _xconst[v]


def all_rows():
    _xconst.clear()
    rows = [('CALIBRATE', [], [], [], None, '')]
    rows += arith_rows() + control_rows()
    return rows


# ---- gen ----------------------------------------------------------------------------

def slots(rows, model):
    """What the program times, in order: each row's total; for the MC68882
    (RD68885) also, for each row with a Table 8-3 entry, its release (no FNOP
    after it) and its head (after a long FETOX); and two calibrations."""
    out = []
    for i, r in enumerate(rows):
        out.append((i, 'total'))
        if model == 68882:
            if i == 0:
                out += [(0, 'cal0'), (0, 'long')]
            elif r[0] in MAN83:
                out += [(i, 'release'), (i, 'head')]
    return out


LONG = 'fetox.x %fp7'          # FP7 = 2: a long tail, and no row's register


def gen(path, only=None, model=68881):
    rows = all_rows()
    if only:
        rows = [r for r in rows if r[0] == 'CALIBRATE' or only in r[0]]
    two = lbl_x(Fraction(2))
    o = []
    w = o.append
    w('| Generated by tools/cycles.py; do not edit. One row per instruction,')
    w('| (FBcc without a size is the word form: GNU as has no .w.)')
    w('| timed with sys_tb\'s clock count at RES+$88.')
    w(f'        .equ    STACK_TOP, 0x0000F000')
    w(f'        .equ    RES, 0x{RES:X}')
    w(f'        .equ    CNT, 0x{CNT:X}')
    w('        .section .vectors, "a"')
    w('        .long   STACK_TOP, _start')
    w('        .rept   5')
    w('        .long   unexpected')
    w('        .endr')
    w('        .long   h_trapcc                    | 7  TRAPcc, FTRAPcc')
    w('        .rept   248')
    w('        .long   unexpected')
    w('        .endr')
    w('        .text')
    w('        .globl  _start')
    w('_start: move.l  #STACK_TOP,%sp')
    w('        lea     RES,%a0')
    w('        moveq   #63,%d0')
    w('1:      clr.l   (%a0)+')
    w('        dbf     %d0,1b')
    w(f'        lea     0x{OUT:X},%a5')
    w('        lea     buf,%a1')
    w('        lea     buf2,%a2')
    for i, kind in slots(rows, model):
        name, setup, timed, data, _, _ = rows[i]
        if kind == 'cal0':
            setup, timed = [], []
        elif kind == 'long':
            setup, timed = [], [LONG]
        w(f'| {i}: {name} ({kind})')
        w(f'        lea     d{i},%a0')
        for s in setup:
            if s != NO_FNOP:
                w(f'        {s}')
        if kind in ('head', 'long'):
            w(f'        fmove.x {two},%fp7')
        if NO_FNOP not in setup:
            w('        fnop')
        w('        .balignw 4,0x4E71')           # with NOPs, so no row's count hangs on another's length
        w('        move.l  CNT,%d6')
        if kind == 'head':
            w(f'        {LONG}')
        for t in timed:
            w(f'        {t}' if not t.endswith(':') else t)
        if kind not in ('release', 'cal0'):
            w('        fnop')
        w('        move.l  CNT,%d7')
        w('        sub.l   %d6,%d7')
        w('        move.l  %d7,(%a5)+')
        if kind == 'release':
            w('        fnop')                    # its tail, outside the count
    w(f'        move.l  #{len(slots(rows, model))},RES+0x14')
    w('        move.l  #0x444F4E45,RES')
    w('_done:  bra     _done')
    w('h_trapcc:')
    w('        rte')
    w('unexpected:')
    w('        move.w  6(%sp),%d0')
    w('        andi.l  #0x0FFF,%d0')
    w('        lsr.l   #2,%d0')
    w('        move.l  %d0,RES+0x10')
    w('        move.l  #0x444F4E45,RES')
    w('        bra     _done')
    w('        .data')
    w('        .balign 4')
    w('nullframe: .long 0')
    for v, lbl in _xconst.items():
        w(f'{lbl}:   .long   ' + ', '.join(f'0x{x:08X}' for x in img_x(v)))
    for i, (_, _, _, data, _, _) in enumerate(rows):
        w(f'd{i}:    ' + ('.long   ' + ', '.join(f'0x{x:08X}' for x in data) if data else '.long   0'))
    w('        .balign 4')
    w('buf:    .space  256')
    w('buf2:   .space  256')
    with open(path, 'w') as f:
        f.write('\n'.join(o) + '\n')


# ---- check -----------------------------------------------------------------------

def load(path, n):
    with open(path) as f:
        v = [int(x, 16) for x in f.read().split()]
    if len(v) != n:
        sys.exit(f'FAIL: cycles -- {path} has {len(v)} values, wanted {n}')
    return v


def load_frozen(path):
    fr = {}
    if os.path.exists(path):
        for line in open(path):
            if line.strip() and not line.startswith('#'):
                name, n = line.rstrip('\n').rsplit('\t', 1)
                fr[name] = int(n)
    return fr


def measure(dumps):
    """Each row: (row, {column: clocks}), less the calibration row, for every
    dump given (a dict of column -> path)."""
    rows = all_rows()
    got = {k: load(p, len(rows)) for k, p in dumps.items() if p}
    return [(r, {k: v[i] - v[0] for k, v in got.items()}) for i, r in enumerate(rows[1:], 1)]


def check(dumps, doc=None, freeze=False):
    """dumps: 'n' (the design point), 'fast' (FPU x30), and optionally 'sync'
    (the same-clock BIU, frozen too) and 'slow' (the asynchronous BIU at the
    bus clock, for reference)."""
    res = measure(dumps)
    bad = 0
    for col, path, what in (('n', FROZEN, ''), ('sync', FROZEN_SYNC, ', same clock')):
        if col not in res[0][1]:
            continue
        if freeze:
            with open(path, 'w') as f:
                f.write(f'# tools/cycles.py --freeze: each row\'s count{what}, in CPU clocks.\n')
                for r, c in res:
                    f.write(f'{r[0]}\t{c[col]}\n')
        fr = load_frozen(path)
        for r, c in res:
            if fr.get(r[0]) != c[col]:
                print(f'  FAIL: {r[0]}{what}: {c[col]} clocks, frozen at {fr.get(r[0])}')
                bad += 1
    man = sum(r[4] for r, _ in res)
    ours = sum(c['n'] for _, c in res)
    faster = sum(1 for r, c in res if c['n'] < r[4])
    slower = sum(1 for r, c in res if c['n'] > r[4])
    print(f'  cycles: {len(res)} rows, {faster} faster than the MC68881, {slower} slower, '
          f'{len(res) - faster - slower} exact; {ours} clocks where the manual adds up to {man}')
    if 'sync' in res[0][1]:
        print(f'  cycles: the same-clock BIU, {sum(c["sync"] for _, c in res)} clocks')
    if doc:
        splice(doc, res, iss_clocks())
    if bad:
        print(f'FAIL: cycles -- {bad} row(s) moved; look, then make cycles FREEZE=1')
        return 1
    print('PASS: cycles')
    return 0


def measure82(path):
    """(row, total, release, head) for every row; release and head None
    where Table 8-3 has nothing."""
    rows = all_rows()
    sl = slots(rows, 68882)
    v = load(path, len(sl))
    raw = {s_: v[j] for j, s_ in enumerate(sl)}
    cal, cal0 = raw[(0, 'total')], raw[(0, 'cal0')]
    lng = raw[(0, 'long')] - cal
    out = []
    for i, r in enumerate(rows[1:], 1):
        tot = raw[(i, 'total')] - cal
        if r[0] in MAN83:
            rel = raw[(i, 'release')] - cal0
            head = lng + tot - (raw[(i, 'head')] - cal)
            out.append((r, tot, rel, head))
        else:
            out.append((r, tot, None, None))
    return out


def check82(path, doc=None, freeze=False):
    """The MC68882 build (RD68885): each row's total, release and head,
    against tools/cycles-68882.frozen, and against Table 8-3."""
    res = measure82(path)
    keys = []
    for r, tot, rel, head in res:
        keys.append((r[0], tot))
        if rel is not None:
            keys += [(r[0] + ' [release]', rel), (r[0] + ' [head]', head)]
    if freeze:
        with open(FROZEN82, 'w') as f:
            f.write('# tools/cycles.py --freeze, MODEL=68882: each row\'s total, its release\n'
                    '# (no FNOP after it) and its head (its overlap after a FETOX), CPU clocks.\n')
            for k, n in keys:
                f.write(f'{k}\t{n}\n')
    fr = load_frozen(FROZEN82)
    bad = 0
    for k, n in keys:
        if fr.get(k) != n:
            print(f'  FAIL: {k}: {n} clocks, frozen at {fr.get(k)}')
            bad += 1
    a = [(r, tot, rel, head) for r, tot, rel, head in res if rel is not None]
    man = sum(MAN83[r[0]][2] for r, *_ in a)
    us = sum(tot for _, tot, _, _ in a)
    mh = sum(MAN83[r[0]][0] for r, *_ in a)
    uh = sum(head for *_, head in a)
    print(f'  cycles: MC68882, {len(a)} rows of Table 8-3: {us} clocks where it adds up to {man}; '
          f'heads {uh} where it adds up to {mh}')
    if doc:
        splice82(doc, res)
    if bad:
        print(f'FAIL: cycles -- {bad} value(s) moved; look, then make cycles MODEL=68882 FREEZE=1')
        return 1
    print('PASS: cycles')
    return 0


def splice82(doc, res):
    t = ['<!-- cycles82:begin -- generated by tools/cycles.py; do not edit -->',
         '| Instruction | 8-3 H | T | total | RD68885 head | tail | total | ratio |',
         '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r, tot, rel, head in res:
        if rel is None:
            continue
        h, tl, mt = MAN83[r[0]]
        t.append(f'| `{r[0]}` | {h} | {tl} | {mt} | {head} | {tot - rel} | {tot} | {tot / mt:.2f} |')
    t.append('<!-- cycles82:end -->')
    s = splice_between(open(doc).read(), 'cycles82', '\n'.join(t))
    a = [(r, tot, rel, head) for r, tot, rel, head in res if rel is not None]
    man = sum(MAN83[r[0]][2] for r, *_ in a)
    us = sum(tot for _, tot, _, _ in a)
    mh = sum(MAN83[r[0]][0] for r, *_ in a)
    uh = sum(head for *_, head in a)
    mt = sum(MAN83[r[0]][1] for r, *_ in a)
    ut = sum(tot - rel for _, tot, rel, _ in a)
    summ = (f'<!-- summary82:begin -->**{len(a)} rows of Table 8-3: totals {us} clocks where '
            f'the manual adds up to {man} ({100 * (us - man) / man:+.0f} %); heads {uh} '
            f'against {mh}; tails {ut} against {mt}.**<!-- summary82:end -->')
    s = splice_between(s, 'summary82', summ)
    open(doc, 'w').write(s)


TRANS = {'FACOS', 'FASIN', 'FATAN', 'FATANH', 'FCOS', 'FCOSH', 'FETOX', 'FETOXM1',
         'FLOGN', 'FLOGNP1', 'FLOG10', 'FLOG2', 'FSIN', 'FSINCOS', 'FSINH', 'FTAN',
         'FTANH', 'FTENTOX', 'FTWOTOX'}
CATS = ['arithmetic, FPn to FPm', 'arithmetic, from memory: integer, single, double',
        'arithmetic, from memory: extended', 'transcendental, FPn to FPm',
        'transcendental, from memory: binary formats', 'packed decimal in',
        'FMOVE out', 'control registers and FMOVEM', 'conditionals', 'FSAVE and FRESTORE']


def category(name):
    op = name.split('.')[0].split(' ')[0]
    if name.startswith(('FB', 'FDB', 'FNOP', 'FSEQ', 'FTRAP')):
        return 'conditionals'
    if name.startswith(('FSAVE', 'FRESTORE')):
        return 'FSAVE and FRESTORE'
    if 'FPCR' in name or name.startswith('FMOVEM'):
        return 'control registers and FMOVEM'
    if name.startswith('FMOVE') and ' FP0,' in name and 'FP1' not in name:
        return 'FMOVE out'
    if '.P (A0)' in name:
        return 'packed decimal in'
    if op in TRANS:
        return 'transcendental, ' + ('from memory: binary formats' if '(A0)' in name else 'FPn to FPm')
    if '.X (A0)' in name:
        return 'arithmetic, from memory: extended'
    if '(A0)' in name or ' D0,' in name:
        return 'arithmetic, from memory: integer, single, double'
    return 'arithmetic, FPn to FPm'


def splice_between(s, tag, text):
    a = s.index(f'<!-- {tag}:begin')
    b = s.index(f'<!-- {tag}:end -->') + len(f'<!-- {tag}:end -->')
    return s[:a] + text + s[b:]


def splice(doc, res, iss):
    sync = 'sync' in res[0][1]
    t = ['<!-- cycles:begin -- generated by tools/cycles.py; do not edit -->',
         '| Instruction | FPU 8 | | RD68884 | Δ | ratio | FPU ×30 | ISS | ratio |'
         + (' same clock | ratio |' if sync else ''),
         '|---|---:|---|---:|---:|---:|---:|---:|---:|' + ('---:|---:|' if sync else '')]
    for r, c in res:
        n = c['n']
        d = n - r[4]
        ds = f'**+{d}**' if d > 0 else (str(d) if d < 0 else '')
        i = iss.get(r[0])
        ic = f'{i} | {i / r[4]:.2f}' if i is not None else ' | '
        sc = f' {c["sync"]} | {c["sync"] / r[4]:.2f} |' if sync else ''
        t.append(f'| `{r[0]}` | {r[4]} | {r[5]} | {n} | {ds} | {n / r[4]:.2f} | {c["fast"]} | {ic} |{sc}')
    t.append('<!-- cycles:end -->')
    s = splice_between(open(doc).read(), 'cycles', '\n'.join(t))
    extra = [k for k in ('sync', 'slow') if k in res[0][1]]
    head = {'sync': 'same clock', 'slow': 'asynchronous, core = bus'}
    c = ['<!-- categories:begin -- generated by tools/cycles.py; do not edit -->',
         '| Rows | | FPU 8 | RD68884 | ratio | FPU ×30 | faster | slower | ISS | ratio |'
         + ''.join(f' {head[k]} | ratio |' for k in extra),
         '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|' + '---:|---:|' * len(extra)]
    for k in CATS:
        g = [(r, x) for r, x in res if category(r[0]) == k]
        man = sum(r[4] for r, _ in g)
        us = sum(x['n'] for _, x in g)
        fl = sum(x['fast'] for _, x in g)
        gi = [(r[4], iss[r[0]]) for r, _ in g if r[0] in iss]
        ic = (f'{sum(i for _, i in gi)} | {sum(i for _, i in gi) / sum(m for m, _ in gi):.2f}'
              if gi else ' | ')
        ex = ''.join(f' {sum(x[e] for _, x in g)} | {sum(x[e] for _, x in g) / man:.2f} |'
                     for e in extra)
        c.append(f'| {k} | {len(g)} | {man} | {us} | {us / man:.2f} | {fl} | '
                 f'{sum(1 for r, x in g if x["n"] < r[4])} | {sum(1 for r, x in g if x["n"] > r[4])} | {ic} |'
                 + ex)
    if extra:
        tm = sum(r[4] for r, _ in res)
        c.append(f'| **all** | {len(res)} | {tm} | {sum(x["n"] for _, x in res)} | '
                 f'{sum(x["n"] for _, x in res) / tm:.2f} | {sum(x["fast"] for _, x in res)} | | | | |'
                 + ''.join(f' {sum(x[e] for _, x in res)} | {sum(x[e] for _, x in res) / tm:.2f} |'
                           for e in extra))
    c.append('<!-- categories:end -->')
    s = splice_between(s, 'categories', '\n'.join(c))
    man = sum(r[4] for r, _ in res)
    ours = sum(x['n'] for _, x in res)
    faster = sum(1 for r, x in res if x['n'] < r[4])
    slower = sum(1 for r, x in res if x['n'] > r[4])
    summ = (f'<!-- summary:begin -->**{len(res)} rows: {faster} faster than the MC68881, '
            f'{slower} slower, {len(res) - faster - slower} exact; {ours} clocks where the '
            f'manual adds up to {man}** ({100 * (ours - man) / man:+.0f} %).<!-- summary:end -->')
    s = splice_between(s, 'summary', summ)
    open(doc, 'w').write(s)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == 'gen':
        a = sys.argv[3:]
        gen(sys.argv[2], a[a.index('--only') + 1] if '--only' in a else None,
            int(a[a.index('--model') + 1]) if '--model' in a else 68881)
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == 'check82':
        a = sys.argv[3:]
        return check82(sys.argv[2], a[a.index('--doc') + 1] if '--doc' in a else None,
                       '--freeze' in a)
    if len(sys.argv) >= 4 and sys.argv[1] == 'check':
        args = sys.argv[4:]

        def opt(name):
            return args[args.index(name) + 1] if name in args else None
        dumps = {'n': sys.argv[2], 'fast': sys.argv[3], 'sync': opt('--sync'), 'slow': opt('--slow')}
        return check(dumps, opt('--doc'), '--freeze' in args)
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
