#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Cross-check the arithmetic model against Berkeley TestFloat.

    python3 tools/model/check_testfloat.py [--level 1|2] [--only NAME]

testfloat_gen (built by `make oracles` into build/oracles/) generates operands
and SoftFloat's results and flags; this script runs each vector through
tools/model/arith.py as the matching MC68881 instruction and compares bits and
flags. SoftFloat is an oracle here, never a source (CLAUDE.md).

Where the MC68881 and SoftFloat legitimately differ, vectors are skipped rather
than "fixed", and the count of skips is printed so nothing is hidden:

  NaN operands or results   FPU 4.5.4 returns the destination NaN and creates
                            an all-ones NaN; SoftFloat's 8086 specialisation
                            does neither.
  extF80 near underflow     FPU table 3-3: an extended exponent of 0 means
                            2^-16383 and may hold a normalised number, where
                            extF80 means 2^-16382. Any operand or result with
                            a biased exponent below 2 is skipped. Single and
                            double, whose denormals the MC68881 shares with
                            IEEE, are checked in full through f32/f64 vectors
                            run in single/double rounding precision.
  extF80 unnormals          not canonical inputs for SoftFloat.
  integer invalid results   SoftFloat returns the x86 integer indefinite; the
                            MC68881 saturates (FPU 6.1.3). Flags still compared.

Flags: SoftFloat's invalid/DZ/overflow/underflow/inexact are compared with the
MC68881 *accrued* byte this one instruction produces (FPU 6.1.10), which is
the IEEE trap-disabled definition SoftFloat implements; with -tininessbefore,
because the MC68881 checks underflow before rounding (FPU 4.5.5.2).
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from model import arith as A, formats as F, xnum as X  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
GEN = os.path.join(ROOT, 'build', 'oracles', 'testfloat', 'testfloat_gen')

SF_INEXACT, SF_UNDERFLOW, SF_OVERFLOW, SF_INFINITE, SF_INVALID = 1, 2, 4, 8, 16
MODES = {'-rnear_even': X.RN, '-rminMag': X.RZ, '-rmin': X.RM, '-rmax': X.RP}


def sf_flags_of(exc):
    a = A.aexc_of(exc)
    f = 0
    if a & A.A_IOP:
        f |= SF_INVALID
    if a & A.A_DZ:
        f |= SF_INFINITE
    if a & A.A_OVFL:
        f |= SF_OVERFLOW
    if a & A.A_UNFL:
        f |= SF_UNDERFLOW
    if a & A.A_INEX:
        f |= SF_INEXACT
    return f


# -- operand classification ---------------------------------------------------
def x80_bad(v):
    e = (v >> 64) & 0x7FFF
    m = v & ((1 << 64) - 1)
    j = m >> 63
    if e == 0x7FFF:
        return m & ((1 << 63) - 1) != 0          # NaN
    if e < 2:
        return m != 0                             # near the extended underflow
    return j == 0 and m != 0                      # unnormal


def x80_canon(v):
    """FPU table 3-3: an infinity's integer bit is a don't-care."""
    if (v >> 64) & 0x7FFF == 0x7FFF and v & ((1 << 63) - 1) == 0:
        return v & ~(1 << 63)
    return v


def x80_nan(v):
    return (v >> 64) & 0x7FFF == 0x7FFF and v & ((1 << 63) - 1) != 0


def f_nan(v, ebits, fbits):
    return (v >> fbits) & ((1 << ebits) - 1) == (1 << ebits) - 1 and v & ((1 << fbits) - 1)


def x80_reg(v):
    return F.Reg((v >> 79) & 1, (v >> 64) & 0x7FFF, v & ((1 << 64) - 1))


# -- running one instruction on the model -------------------------------------
def run(op, srcreg, dstreg, mode, prec, image=None, fmt=None, out=None):
    """One general instruction; returns (OpResult, State after)."""
    st = A.State()
    st.fpcr = (prec << 6) | (mode << 4)
    st.fp[1] = dstreg if dstreg is not None else F.RESET_REG
    if out is not None:
        st.fp[0] = srcreg
        cmd = (3 << 13) | (out << 10) | (0 << 7)
    elif fmt is not None:
        cmd = (2 << 13) | (fmt << 10) | (1 << 7) | op
    else:
        st.fp[0] = srcreg
        cmd = (0 << 13) | (0 << 10) | (1 << 7) | op
    res = A.general(st, cmd, image)
    A.apply(st, res)
    return res, st


# Each check: name, testfloat function, operand types, how to run, how to read.
def checks():
    c = []
    for fn, op in (('add', 0x22), ('sub', 0x28), ('mul', 0x23), ('div', 0x20),
                   ('rem', 0x25), ('sqrt', 0x04)):
        c.append(('extF80_' + fn, 'x', op))
        c.append(('f32_' + fn, 's', op))
        c.append(('f64_' + fn, 'd', op))
    c.append(('extF80_roundToInt', 'x', 'rti'))
    c.append(('extF80_to_f32', 'x', 'out_s'))
    c.append(('extF80_to_f64', 'x', 'out_d'))
    c.append(('extF80_to_i32', 'x', 'out_l'))
    c.append(('i32_to_extF80', 'i', 'in_l'))
    c.append(('f32_to_extF80', 's', 'in_s'))
    c.append(('f64_to_extF80', 'd', 'in_d'))
    c.append(('f32_to_f64', 's', 'in_s_dprec'))
    c.append(('f64_to_f32', 'd', 'in_d_sprec'))
    return c


def one(name, kind, op, mode_opt, level, limit):
    args = [GEN, '-level', str(level), mode_opt, '-tininessbefore']
    if name == 'extF80_roundToInt' or name.endswith('_to_i32'):
        args.append('-exact')
    args.append(name)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
    mode = MODES[mode_opt]
    n = skipped = bad = 0
    shown = 0
    for line in proc.stdout:
        f = line.split()
        if not f:
            continue
        n += 1
        if limit and n > limit:
            break
        vals = [int(t, 16) for t in f[:-1]]
        sf_flags = int(f[-1], 16)
        exp_out = vals[-1]
        ins = vals[:-1]
        got, gflags, skip = evaluate(name, kind, op, mode, ins, exp_out, sf_flags)
        if skip:
            skipped += 1
            continue
        if name.endswith('extF80') or (name.startswith('extF80_')
                                       and not name.startswith('extF80_to_')):
            got, exp_out = x80_canon(got), x80_canon(exp_out)
        if got != exp_out or gflags != sf_flags:
            bad += 1
            if shown < 8:
                shown += 1
                print(f'    MISMATCH {name} {mode_opt}: in {" ".join(f[:-2])} '
                      f'want {f[-2]} {f[-1]} got {got:X} {gflags:02X}')
    proc.stdout.close()
    proc.kill()
    proc.wait()
    return n, skipped, bad


def evaluate(name, kind, op, mode, ins, exp_out, sf_flags):
    """Returns (result bits, flags, skip)."""
    # -- the three precisions of the dyadic/monadic arithmetic --------------
    if kind == 'x' and op not in ('rti', 'out_s', 'out_d', 'out_l'):
        if any(x80_bad(v) for v in ins) or x80_bad(exp_out):
            return 0, 0, True
        if sf_flags & SF_UNDERFLOW and exp_out & ((1 << 79) - 1) == 0:
            return 0, 0, True                     # flushed below extF80's range
        if op == 0x04:
            res, st = run(op, x80_reg(ins[0]), None, mode, X.PREC_X)
        else:
            res, st = run(op, x80_reg(ins[1]), x80_reg(ins[0]), mode, X.PREC_X)
        return st.fp[1].bits80(), sf_flags_of(res.exc), False
    if kind in ('s', 'd') and isinstance(op, int):
        eb, fb = (8, 23) if kind == 's' else (11, 52)
        dec = F.decode_s if kind == 's' else F.decode_d
        enc = F.encode_s if kind == 's' else F.encode_d
        if any(f_nan(v, eb, fb) for v in ins) or f_nan(exp_out, eb, fb):
            return 0, 0, True
        prec = X.PREC_S if kind == 's' else X.PREC_D
        regs = [F.encode_x(dec(v)) for v in ins]
        if op == 0x04:
            res, st = run(op, regs[0], None, mode, prec)
        else:
            res, st = run(op, regs[1], regs[0], mode, prec)
        return enc(F.decode_x(st.fp[1])), sf_flags_of(res.exc), False
    if op == 'rti':
        if x80_bad(ins[0]) or x80_bad(exp_out):
            return 0, 0, True
        res, st = run(0x01, x80_reg(ins[0]), None, mode, X.PREC_X)
        return st.fp[1].bits80(), sf_flags_of(res.exc), False
    if op in ('out_s', 'out_d', 'out_l'):
        if x80_bad(ins[0]):
            return 0, 0, True
        fmt = {'out_s': F.FMT_S, 'out_d': F.FMT_D, 'out_l': F.FMT_L}[op]
        if op == 'out_s' and f_nan(exp_out, 8, 23):
            return 0, 0, True
        if op == 'out_d' and f_nan(exp_out, 11, 52):
            return 0, 0, True
        res, st = run(None, x80_reg(ins[0]), None, mode, X.PREC_X, out=fmt)
        got = res.mem
        if op == 'out_l' and sf_flags & SF_INVALID:
            got = exp_out                 # value differs by design; flags only
        return got, sf_flags_of(res.exc), False
    if op == 'in_l':
        res, st = run(0x00, None, None, mode, X.PREC_X, image=ins[0], fmt=F.FMT_L)
        return st.fp[1].bits80(), sf_flags_of(res.exc), False
    if op in ('in_s', 'in_d', 'in_s_dprec', 'in_d_sprec'):
        s = op.startswith('in_s')
        if f_nan(ins[0], 8, 23) if s else f_nan(ins[0], 11, 52):
            return 0, 0, True
        prec = {'in_s': X.PREC_X, 'in_d': X.PREC_X,
                'in_s_dprec': X.PREC_D, 'in_d_sprec': X.PREC_S}[op]
        res, st = run(0x00, None, None, mode, prec, image=ins[0],
                      fmt=F.FMT_S if s else F.FMT_D)
        v = F.decode_x(st.fp[1])
        if op == 'in_s_dprec':
            return F.encode_d(v), sf_flags_of(res.exc), False
        if op == 'in_d_sprec':
            return F.encode_s(v), sf_flags_of(res.exc), False
        if x80_bad(exp_out):
            return 0, 0, True
        return st.fp[1].bits80(), sf_flags_of(res.exc), False
    raise ValueError(op)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--level', type=int, default=1)
    ap.add_argument('--only', default=None)
    ap.add_argument('--limit', type=int, default=0,
                    help='at most this many vectors per function and mode')
    a = ap.parse_args()
    if not os.path.exists(GEN):
        sys.exit(f'no {GEN}: run `make oracles` first')
    total_bad = 0
    for name, kind, op in checks():
        if a.only and a.only not in name:
            continue
        for mopt in MODES:
            n, sk, bad = one(name, kind, op, mopt, a.level, a.limit)
            total_bad += bad
            status = 'ok' if bad == 0 else f'{bad} MISMATCHES'
            print(f'  {name:20s} {mopt:12s} {n:7d} vectors, {sk:6d} skipped: {status}')
    if total_bad:
        print(f'FAIL: testfloat, {total_bad} mismatches')
        return 1
    print('PASS: testfloat')
    return 0


if __name__ == '__main__':
    sys.exit(main())
