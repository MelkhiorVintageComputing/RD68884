#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""Compare sim/programs/fparith.c's buffer, as RD68021 and RD68884 wrote it,
with the host's. Copied from RD68021's tools/mh882_compare.py.

    fparith_compare.py EXPECT DUMP EXACT_LONGS

The first EXACT_LONGS long words must match bit for bit. The doubles after
them are the transcendental functions, reported in ulps and never failed: the
FPU's algorithms and the host's libm are both approximations.

Exits 1 if an exact long word differs, or if the dump is the wrong length.
"""

import struct
import sys


def longs(path):
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


def as_double(hi, lo):
    return struct.unpack('>d', struct.pack('>II', hi, lo))[0]


def ordered(hi, lo):
    """A double's bits, as an integer that orders the same way the values do."""
    u = (hi << 32) | lo
    return (1 << 63) - (u & ((1 << 63) - 1)) if u >> 63 else u + (1 << 63)


def main():
    expect, dump, exact = longs(sys.argv[1]), longs(sys.argv[2]), int(sys.argv[3])
    ok = True
    if len(dump) != len(expect):
        print(f'  FAIL: {len(dump)} long words dumped, {len(expect)} expected')
        ok = False
    bad = 0
    for i in range(min(exact, len(dump), len(expect))):
        if dump[i] != expect[i]:
            bad += 1
            if bad <= 20:
                print(f'  FAIL: long word {i}: {dump[i]:08x}, expected {expect[i]:08x}')
    if bad:
        print(f'  FAIL: {bad} of {exact} exact long words differ')
        ok = False
    else:
        print(f'  {exact} exact long words match')

    worst = 0
    names = ['sin', 'cos', 'tan', 'atan', 'etox', 'logn', 'log10', 'twotox']
    rows = []
    for k, i in enumerate(range(exact, min(len(dump), len(expect)) - 1, 2)):
        got = ordered(dump[i], dump[i + 1])
        want = ordered(expect[i], expect[i + 1])
        ulps = abs(got - want)
        worst = max(worst, ulps)
        rows.append((names[k % len(names)], as_double(expect[i], expect[i + 1]),
                     as_double(dump[i], dump[i + 1]), ulps))
    if rows:
        print(f'  transcendentals, against the host libm (reported, not checked): '
              f'worst {worst} ulp')
        for name, want, got, ulps in rows:
            if ulps:
                print(f'    {name:7s} {want!r:>24} got {got!r:>24}  {ulps} ulp')
    print('PASS: fparith' if ok else 'FAIL: fparith')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
