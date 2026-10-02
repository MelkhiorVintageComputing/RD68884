#!/usr/bin/env python3
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The RTL sequencer against the ISS, clock by clock.

    python3 tools/iss/lockstep.py build/sys-lockstep.log

sim/tb/sys_tb.sv (+lockstep=FILE) records, at every FPU clock, what the BIU
drove into rd68884_seq and what rd68884_seq drove back, and its micro-address.
This feeds the recorded BIU signals to tools/iss/core.py and requires the
same micro-address and the same outputs on every clock -- so the RTL and the
ISS, which is the definition of the microword, cannot drift apart. The BIU
itself is checked at its pins by sim/tb/biu_tb.sv.

An output that is only meaningful with its strobe (resp with resp_we, opr with
opr_we, ...) is compared only when the strobe is set.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from iss.machine import load  # noqa: E402
from iss.core import Core     # noqa: E402

IN = ['arch_reset', 'cmd_pend', 'cmd_cond', 'cmd_word', 'opw_valid', 'opw_data',
      'opr_valid', 'save_req', 'restore_req', 'restore_word', 'fpiar', 'pv',
      'resp_read', 'rsel_read', 'save_read', 'abort']
OUT = ['resp_we', 'resp', 'resp_oneshot', 'expect', 'resp_cond', 'cmd_ack',
       'opw_ack', 'opr_we', 'opr', 'rsel_we', 'rsel', 'rsel_dir', 'save_we',
       'save', 'save_xfer', 'restore_we', 'restore', 'restore_xfer', 'fpiar_we',
       'fpiar', 'clear']
# value -> its strobe
GATED = {'resp': 'resp_we', 'resp_oneshot': 'resp_we', 'expect': 'resp_we',
         'resp_cond': 'resp_we', 'opr': 'opr_we', 'rsel': 'rsel_we',
         'rsel_dir': 'rsel_we', 'save': 'save_we', 'save_xfer': 'save_we',
         'restore': 'restore_we', 'restore_xfer': 'restore_we', 'fpiar': 'fpiar_we'}


def parse(tok):
    if any(c in tok for c in 'xXzZ'):
        return None
    return int(tok, 16)


def main():
    path = sys.argv[1]
    words, labels, comments = load()
    Core.labels = labels
    core = Core(words)
    inv = {v: k for k, v in labels.items()}

    def where(a):
        best = max((v for v in inv if v <= a), default=0)
        return f'{a} ({inv.get(best, "?")}+{a - best})'

    n = bad = 0
    with open(path) as fh:
        for line in fh:
            ins, outs, upc = line.split('|')
            b = dict(zip(IN, (parse(t) for t in ins.split())))
            r = dict(zip(OUT, (parse(t) for t in outs.split())))
            rupc = parse(upc.strip())
            n += 1
            if rupc != core.upc:
                print(f'  clock {n}: micro-address RTL {rupc} ISS {where(core.upc)}')
                return 1
            if any(v is None for v in b.values()):
                print(f'  clock {n}: unknown BIU input at {where(core.upc)}: {b}')
                return 1
            o = core.step(b)
            for k in OUT:
                if k in GATED and not o[GATED[k]]:
                    continue
                if r[k] != o[k]:
                    bad += 1
                    if bad <= 10:
                        print(f'  clock {n} at {where(rupc)}: {k} RTL {r[k]} ISS {o[k]}')
    if bad:
        print(f'FAIL: lockstep, {bad} differences in {n} clocks')
        return 1
    print(f'PASS: lockstep, {n} clocks identical')
    return 0


if __name__ == '__main__':
    sys.exit(main())
