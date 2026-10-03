#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Instruction clock counts against FPU section 8 (doc/timing-divergences.md).

    python3 tools/cycles.py gen OUT.S [--only TEXT]
    python3 tools/cycles.py check DUMP DUMP_FAST [--doc DOC] [--freeze]

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

`check` reads two dumps of the results: one at the design's clocks (the FPU's
core three times the bus clock, sys_tb's default) and one with the FPU's core
thirty times as fast, where what is left is close to the main processor and
the protocol alone. It compares the count with the frozen one
(tools/cycles.frozen) and fails if any row moved -- faster or slower, a change
someone has to look at and then freeze with --freeze -- and it regenerates the
table in DOC between its markers.

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

    def row(name, setup, timed, data, man, how, iss):
        rows.append((name, setup, timed, data, man, how))
        ISS_SPEC[name] = iss

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
            dict(regs=regs, cmd=cmdw(0, 0, ry, OPMODE[op]), ea=None))
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
                     data=IMG[f](v)))
    # Footnote **: an MPU data register as the source, five clocks less.
    row('FADD.L D0,FP1', ['fmove.x ' + lbl_x(Fraction(7, 4)) + ',%fp1', 'moveq #3,%d0'],
        ['fadd.l %d0,%fp1'], [], T82['FADD'][1] - 5, f"{T82['FADD'][1]} - 5",
        dict(regs=[(1, Fraction(7, 4))], cmd=cmdw(2, 0, 1, OPMODE['FADD']), ea=('dn', 0), d0=3))
    # FMOVE to memory, from FP0 = 3.140625 (and to D0: two clocks less).
    pi = Fraction(201, 64)
    setup = [f'fmove.x {lbl_x(pi)},%fp0']
    for k, f in enumerate(FMTS):
        ins = f'fmove.{f} %fp0,(%a1)' + ('{#17}' if f == 'p' else '')
        row(f'FMOVE.{f.upper()} FP0,(A1)' + (' {#17}' if f == 'p' else ''), setup, [ins], [],
            T82_OUT[k] + EA_AN, f'{T82_OUT[k]} + (An) {EA_AN}',
            dict(regs=[(0, pi)], cmd=cmdw(3, FMT_CODE[f], 0, 17 if f == 'p' else 0), ea=('ind', 1)))
    row('FMOVE.L FP0,D0', setup, ['fmove.l %fp0,%d0'], [], T82_OUT[0] - 2, f'{T82_OUT[0]} - 2',
        dict(regs=[(0, pi)], cmd=cmdw(3, 0, 0, 0), ea=('dn', 0)))
    row('FMOVECR #0,FP1', [], ['fmovecr #0,%fp1'], [], 29, '29',
        dict(regs=[], cmd=cmdw(2, 7, 1, 0), ea=None))
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

def gen(path, only=None):
    rows = all_rows()
    if only:
        rows = [r for r in rows if r[0] == 'CALIBRATE' or only in r[0]]
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
    for i, (name, setup, timed, data, _, _) in enumerate(rows):
        w(f'| {i}: {name}')
        w(f'        lea     d{i},%a0')
        for s in setup:
            if s != NO_FNOP:
                w(f'        {s}')
        if NO_FNOP not in setup:
            w('        fnop')
        w('        .balignw 4,0x4E71')           # with NOPs, so no row's count hangs on another's length
        w('        move.l  CNT,%d6')
        for t in timed:
            w(f'        {t}' if not t.endswith(':') else t)
        w('        fnop')
        w('        move.l  CNT,%d7')
        w('        sub.l   %d6,%d7')
        w('        move.l  %d7,(%a5)+')
    w(f'        move.l  #{len(rows)},RES+0x14')
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


def load_frozen():
    fr = {}
    if os.path.exists(FROZEN):
        for line in open(FROZEN):
            if line.strip() and not line.startswith('#'):
                name, n = line.rstrip('\n').rsplit('\t', 1)
                fr[name] = int(n)
    return fr


def measure(dump, fast):
    rows = all_rows()
    a, b = load(dump, len(rows)), load(fast, len(rows))
    out = []
    for i, r in enumerate(rows[1:], 1):
        out.append((r, a[i] - a[0], b[i] - b[0]))
    return out


def check(dump, fast, doc=None, freeze=False):
    res = measure(dump, fast)
    if freeze:
        with open(FROZEN, 'w') as f:
            f.write('# tools/cycles.py --freeze: each row\'s count, in CPU clocks.\n')
            for r, n, _ in res:
                f.write(f'{r[0]}\t{n}\n')
    fr = load_frozen()
    bad = 0
    for r, n, _ in res:
        if fr.get(r[0]) != n:
            print(f'  FAIL: {r[0]}: {n} clocks, frozen at {fr.get(r[0])}')
            bad += 1
    man = sum(r[4] for r, _, _ in res)
    ours = sum(w for _, w, _ in res)
    faster = sum(1 for r, w, _ in res if w < r[4])
    slower = sum(1 for r, w, _ in res if w > r[4])
    print(f'  cycles: {len(res)} rows, {faster} faster than the MC68881, {slower} slower, '
          f'{len(res) - faster - slower} exact; {ours} clocks where the manual adds up to {man}')
    if doc:
        splice(doc, res, iss_clocks())
    if bad:
        print(f'FAIL: cycles -- {bad} row(s) moved; look, then make cycles FREEZE=1')
        return 1
    print('PASS: cycles')
    return 0


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
    t = ['<!-- cycles:begin -- generated by tools/cycles.py; do not edit -->',
         '| Instruction | FPU 8 | | RD68884 | Δ | ratio | FPU ×30 | ISS | ratio |',
         '|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for r, n, fast in res:
        d = n - r[4]
        ds = f'**+{d}**' if d > 0 else (str(d) if d < 0 else '')
        i = iss.get(r[0])
        ic = f'{i} | {i / r[4]:.2f}' if i is not None else ' | '
        t.append(f'| `{r[0]}` | {r[4]} | {r[5]} | {n} | {ds} | {n / r[4]:.2f} | {fast} | {ic} |')
    t.append('<!-- cycles:end -->')
    s = splice_between(open(doc).read(), 'cycles', '\n'.join(t))
    c = ['<!-- categories:begin -- generated by tools/cycles.py; do not edit -->',
         '| Rows | | FPU 8 | RD68884 | ratio | FPU ×30 | faster | slower | ISS | ratio |',
         '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k in CATS:
        g = [(r, n, f) for r, n, f in res if category(r[0]) == k]
        man, us, fl = (sum(r[4] for r, _, _ in g), sum(n for _, n, _ in g),
                       sum(f for _, _, f in g))
        gi = [(r[4], iss[r[0]]) for r, _, _ in g if r[0] in iss]
        ic = (f'{sum(i for _, i in gi)} | {sum(i for _, i in gi) / sum(m for m, _ in gi):.2f}'
              if gi else ' | ')
        c.append(f'| {k} | {len(g)} | {man} | {us} | {us / man:.2f} | {fl} | '
                 f'{sum(1 for r, n, _ in g if n < r[4])} | {sum(1 for r, n, _ in g if n > r[4])} | {ic} |')
    c.append('<!-- categories:end -->')
    s = splice_between(s, 'categories', '\n'.join(c))
    # The summary line, between its own markers.
    man = sum(r[4] for r, _, _ in res)
    ours = sum(w for _, w, _ in res)
    faster = sum(1 for r, w, _ in res if w < r[4])
    slower = sum(1 for r, w, _ in res if w > r[4])
    summ = (f'<!-- summary:begin -->**{len(res)} rows: {faster} faster than the MC68881, '
            f'{slower} slower, {len(res) - faster - slower} exact; {ours} clocks where the '
            f'manual adds up to {man}** ({100 * (ours - man) / man:+.0f} %).<!-- summary:end -->')
    s = splice_between(s, 'summary', summ)
    open(doc, 'w').write(s)


def main():
    if len(sys.argv) in (3, 5) and sys.argv[1] == 'gen':
        gen(sys.argv[2], sys.argv[4] if len(sys.argv) == 5 and sys.argv[3] == '--only' else None)
        return 0
    if len(sys.argv) >= 4 and sys.argv[1] == 'check':
        args = sys.argv[4:]
        doc = args[args.index('--doc') + 1] if '--doc' in args else None
        return check(sys.argv[2], sys.argv[3], doc, '--freeze' in args)
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
