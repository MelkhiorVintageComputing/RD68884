# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The microcoded FPU, driven at the level of CIR accesses.

    m = Machine()                   # reads build/ucode.json
    m = Machine(model=68882)        # RD68885: build/ucode-68882.json
    m.write(CIR_COMMAND, cmd); m.read(CIR_RESPONSE)

Same interface as tools/model/cpif.FPU881, so tools/model/mpu.py drives both
and tools/iss/tests compares them. Each access lets the core run a few clocks
first (the bus cycle and its synchronisers), then waits clock by clock until
the BIU can complete it.
"""

import json
import os

from .biu import Biu
from .core import Core, FP

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
UCODE = os.path.join(ROOT, 'build', 'ucode.json')


def ucode_path(model=68881):
    return os.path.join(ROOT, 'build', 'ucode-68882.json' if model == 68882 else 'ucode.json')


def load(model=68881):
    with open(ucode_path(model)) as fh:
        j = json.load(fh)
    return j['words'], j['labels'], j.get('comments', [])


class Machine:
    BUS_CLOCKS = 4          # core clocks a bus cycle takes before it can complete
    MAX_WAIT = 20000

    def __init__(self, model=68881):
        self.model = model
        words, labels, self.comments = load(model)
        self.core = Core(words, labels=labels, model=model)
        self.biu = Biu(model=model)
        self.clocks = 0
        self.log = []
        self.trace = None
        self.run(200)                 # the reset microcode

    def clock(self):
        b = self.biu.outputs()
        if self.trace is not None:
            self.trace.append(self.core.upc)
        o = self.core.step(b)
        self.biu.from_core(o)
        self.clocks += 1

    def run(self, n):
        for _ in range(n):
            self.clock()

    tick = run

    def _access(self, cir, write, value=0):
        self.run(self.BUS_CLOCKS)
        n = 0
        while not self.biu.ready(cir, write):
            self.clock()
            n += 1
            if n > self.MAX_WAIT:
                raise TimeoutError(f'access to CIR ${cir:02X} never completes '
                                   f'(upc {self.core.upc})')
        v = self.biu.access(cir, write, value)
        self.clock()                   # the events are seen on this clock
        return v

    def read(self, cir):
        v = self._access(cir, False)
        self.log.append(('R', cir, v))
        return v

    def write(self, cir, value):
        self.log.append(('W', cir, value))
        self._access(cir, True, value)

    # -------------------------------------------------- the programmer's model
    def fp_bits80(self, i):
        a = self.core.rf[i]
        return (a.sign << 79) | (((a.exp + 16383) & 0x7FFF) << 64) | (a.mant >> 8)

    @property
    def fpcr(self):
        return self.core.fpcr

    @property
    def fpsr(self):
        return self.core.fpsr

    @property
    def fpiar(self):
        return self.biu.fpiar
