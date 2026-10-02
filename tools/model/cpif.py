# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The MC68881's coprocessor interface, at the level of CIR accesses.

    fpu = FPU881()
    fpu.write(CIR_COMMAND, 0x4822)      # one bus cycle each
    prim = fpu.read(CIR_RESPONSE)

This is the golden model for the bus interface unit and the dialog microcode:
what each CIR access returns and what it changes, FPU section 7 and 6.4. The
port size (8/16/32-bit dynamic sizing) is the BIU's business and is not
modelled; every access here is the whole CIR.

Time is counted in accesses. A computation takes `latency(op)` ticks, during
which the response CIR says the FPU is busy; each access advances one tick and
tick(n) advances more, so a test can make the FPU slow or instant.

A dialog is a list of steps executed by _run(); its position is a plain index,
so a busy state frame can save it and FRESTORE can resume it. That is the same
shape the microcode will have (a resume token, doc/architecture.md).
"""

from dataclasses import dataclass, field
from typing import Optional

from . import arith as A
from . import formats as F
from .formats import Reg, decode_x

# CIR byte offsets, FPU table 7-2.
CIR_RESPONSE, CIR_CONTROL, CIR_SAVE, CIR_RESTORE = 0x00, 0x02, 0x04, 0x06
CIR_OPWORD, CIR_COMMAND, CIR_RSVD0C, CIR_CONDITION = 0x08, 0x0A, 0x0C, 0x0E
CIR_OPERAND, CIR_REGSEL, CIR_RSVD16 = 0x10, 0x14, 0x16
CIR_INSTADDR, CIR_OPADDR = 0x18, 0x1C

# Primitives, FPU table 7-7.
P_FALSE, P_TRUE, P_IDLE, P_RELEASE, P_RELEASE_PC = 0x0800, 0x0801, 0x0802, 0x0900, 0x4900
P_WAIT, P_WAIT_PC = 0x8900, 0xC900
P_PROTOCOL = 0x1D0D
P_BSUN = 0x5C30
# MODEL CHOICE (doc/manual-contradictions.md 5): table 7-7's $1C0B, PC = 0,
# for an illegal command word; FPU 7.4.2.5's text says PC = 1.
P_FLINE = 0x1C0B

# Save/restore format words, FPU 6.4.2 and the FSAVE description in 4.6.
FW_NULL, FW_AGAIN, FW_INVALID = 0x0018, 0x0118, 0x0218
FW_IDLE, FW_BUSY = 0x1F18, 0x1FB4
IDLE_LONGS, BUSY_LONGS = 6, 45

# BIU flag pending codes, FPU table 6-4.
PEND_COND, PEND_GEN, PEND_OPW, PEND_OPR, PEND_NONE = 0b001, 0b011, 0b100, 0b110, 0b111


def prim_eval_ea(to_fpu, pc, valid_ea, length):
    """Evaluate effective address and transfer data, FPU 7.4.2.2, CA = 1."""
    return 0x8000 | (pc << 14) | ((0 if to_fpu else 1) << 13) | 0x1000 \
        | (valid_ea << 8) | length


EA_CTRL_ALT, EA_DATA_ALT, EA_MEM_ALT, EA_ALT, EA_CTRL, EA_DATA, EA_MEM, EA_ANY = range(8)


def default_latency(cmd):
    return 4


@dataclass
class Dialog:
    """One instruction in progress. Everything here goes into a busy frame."""
    kind: str                     # 'gen' or 'cond'
    word: int                     # command word or conditional predicate
    steps: list = field(default_factory=list)
    pc: int = 0                   # index of the current step
    data: list = field(default_factory=list)    # long words in or out
    count: int = 0                # long words still to transfer in this step
    mask: int = 0                 # FMOVEM register mask
    dreg: int = 0                 # the main-processor register value received
    released: bool = False        # the MPU has been let go (computing alone)
    trap: int = 0                 # FMOVE out: vector of a mid-instruction exception

    def first_prim(self):
        for i, s in enumerate(self.steps):
            if s[0] == 'prim':
                return i
        return 0


class FPU881:
    def __init__(self, latency=default_latency):
        self.latency = latency
        self.st = A.State()
        self.reset()

    # ------------------------------------------------------------------ reset
    def reset(self):
        """RESET or an FRESTORE of a null frame, FPU 9.9 and 6.4.2.1."""
        self.st = A.State()
        self.null_state = True       # nothing executed: FSAVE gives a null frame
        self.resp = P_IDLE
        self.dialog: Optional[Dialog] = None
        self.latched: Optional[tuple] = None    # (kind, word) while busy
        self.busy = 0                # ticks of computation left
        self.on_done = None          # what to do when they run out
        self.exc_pend = False
        self.pv = False
        self.etemp = Reg(0, 0, 0)
        self.opreg = 0xFFFFFFFF
        self.frame = None            # FSAVE/FRESTORE transfer in progress
        self.restore_readback = None
        self.log = []

    # ------------------------------------------------------------------- time
    def tick(self, n=1):
        for _ in range(n):
            if self.busy > 0:
                self.busy -= 1
                if self.busy == 0 and self.on_done:
                    fn, self.on_done = self.on_done, None
                    fn()

    # -------------------------------------------------------------- the bus
    def read(self, cir):
        self.tick()
        v = self._read(cir)
        self.log.append(('R', cir, v))
        return v

    def write(self, cir, value):
        self.tick()
        self.log.append(('W', cir, value))
        self._write(cir, value)

    def _read(self, cir):
        if cir == CIR_RESPONSE:
            return self._read_response()
        if cir == CIR_SAVE:
            return self._save()
        if cir == CIR_RESTORE:
            return self.restore_readback if self.restore_readback is not None else 0xFFFF
        if cir == CIR_OPERAND:
            return self._operand_read()
        if cir == CIR_REGSEL:
            return self._regsel_read()
        # FPU 7.2: reads of write-only, reserved and unimplemented CIRs return
        # all ones and are never a protocol violation (6.1.12).
        return 0xFFFFFFFF if cir in (CIR_INSTADDR, CIR_OPADDR) else 0xFFFF

    def _write(self, cir, v):
        if cir == CIR_CONTROL:
            self._abort()
        elif cir == CIR_COMMAND:
            self._start('gen', v & 0xFFFF)
        elif cir == CIR_CONDITION:
            self._start('cond', v & 0xFFFF)
        elif cir == CIR_OPERAND:
            self._operand_write(v)
        elif cir == CIR_RESTORE:
            self._restore(v & 0xFFFF)
        elif cir == CIR_INSTADDR:
            self.st.fpiar = v & 0xFFFFFFFF          # FPU 7.2.10, always
        elif cir == CIR_REGSEL:
            self._violation()                       # FPU 6.1.12, the one write that is
        # $08, $0C, $16, $1C: ignored (FPU 7.2, 7.2.5, 7.2.11).

    # ------------------------------------------------------- expectations
    def _expect(self):
        """What the BIU expects next: 'cmd', 'resp', 'opw', 'opr', 'rsel'."""
        if self.frame is not None:
            return 'opr' if self.frame['dir'] == 'save' else 'opw'
        d = self.dialog
        if d is None or d.released:
            return 'cmd'
        return {'opw': 'opw', 'opr': 'opr', 'rsel': 'rsel'}.get(d.steps[d.pc][0], 'resp')

    def _violation(self):
        """FPU 6.1.12: the response becomes the mid-instruction protocol
        violation primitive; the MPU acknowledges with a control write."""
        self.pv = True
        self.resp = P_PROTOCOL

    # ------------------------------------------------------------- abort
    def _abort(self):
        """FPU 7.2.2: any control write terminates the instruction, clears
        pending exceptions and returns the BIU to idle."""
        self.dialog = None
        self.latched = None
        self.busy = 0
        self.on_done = None
        self.exc_pend = False
        self.pv = False
        self.frame = None
        self.restore_readback = None
        self.resp = P_IDLE

    # ------------------------------------------------- starting an instruction
    def _start(self, kind, word):
        if self.pv:
            return
        if self.frame is not None or self._expect() != 'cmd':
            # FPU 7.2.6/7.2.7: not legal in the initial phase of a general
            # instruction or before a conditional's evaluation is read.
            self._violation()
            return
        self.null_state = False
        if self.busy:
            # FPU 7.2.6: latched, null (CA=1, IA=1) until the APU is free.
            self.latched = (kind, word)          # the computing dialog runs on
            self.resp = P_WAIT
            return
        self._begin(kind, word)

    def _begin(self, kind, word):
        """The instruction's first primitive, FPU 7.5 and 6.4.2.2 (EXC PEND)."""
        self.latched = None
        reports = kind == 'cond' or (word >> 13) in (0, 2, 3)
        if self.exc_pend and reports:
            vec = A.trap_vector((self.st.fpsr >> 8) & 0xFF, self.st.enable) or 49
            self.dialog = Dialog(kind, word, [('prim', 0x1C00 | vec, None)], released=False)
            self.resp = 0x1C00 | vec
            return
        if kind == 'cond':
            self._begin_cond(word)
        else:
            self._begin_gen(word)

    def _pc_bit(self):
        # FPU table 4-13 note 1: "If any exceptions, other than BSUN, are
        # enabled, PC is set to one".
        return 1 if self.st.enable & 0x7F else 0

    def _begin_cond(self, pred):
        p = pred & 0x1F                    # table 4-20 note 3: 1xxxxx = 0xxxxx
        tf, bsun = evaluate_predicate(p, (self.st.fpsr >> 24) & 0xF)
        if bsun:
            self.st.fpsr |= (A.BSUN << 8) | A.A_IOP
            if self.st.enable & A.BSUN:
                self.dialog = Dialog('cond', pred, [('prim', P_BSUN, None)])
                self.resp = P_BSUN
                return
        prim = P_TRUE if tf else P_FALSE
        self.dialog = Dialog('cond', pred, [('prim', prim, P_IDLE), ('done',)])
        self.resp = prim

    def _begin_gen(self, cmd):
        opclass = (cmd >> 13) & 7
        rx = (cmd >> 10) & 7
        pc = self._pc_bit()
        S = []
        if opclass == 1 or (opclass in (0, 2) and not (opclass == 2 and rx == 7)
                            and cmd & 0x40):
            S = [('prim', P_FLINE, None)]                     # FPU 6.1.11
        elif opclass == 0 or (opclass == 2 and rx == 7):
            # Register to register and FMOVECR, FPU 7.5.1.1.
            S = [('prim', P_RELEASE_PC if pc else P_RELEASE, 'release'),
                 ('compute', 'arith'), ('done',)]
        elif opclass == 2:
            n = F.FMT_BYTES[rx]
            vea = EA_DATA if n <= 4 else EA_MEM
            S = [('prim', prim_eval_ea(True, pc, vea, n), P_WAIT),
                 ('opw', (n + 3) // 4), ('release',), ('compute', 'arith'), ('done',)]
        elif opclass == 3:
            n = F.FMT_BYTES[rx]
            vea = EA_DATA_ALT if n <= 4 else EA_MEM_ALT
            if rx == F.FMT_PDYN:
                dn = (cmd >> 4) & 7
                S = [('prim', 0x8C00 | (pc << 14) | dn, P_WAIT), ('opw', 1),
                     ('compute', 'convert'),
                     ('prim', prim_eval_ea(False, 0, vea, n), P_WAIT),
                     ('opr', (n + 3) // 4), ('end_out',)]
            else:
                S = [('prim', P_WAIT_PC if pc else P_WAIT, P_WAIT),
                     ('compute', 'convert'),
                     ('prim', prim_eval_ea(False, 0, vea, n), P_WAIT),
                     ('opr', (n + 3) // 4), ('end_out',)]
        elif opclass in (4, 5):
            lst = rx or 1                  # table 4-17 note 2: 000 is FPIAR
            n = 4 * bin(lst).count('1')
            to_fpu = opclass == 4
            if n == 4 and lst == 1:
                vea = EA_ANY if to_fpu else EA_ALT
                prim = 0x9704 if to_fpu else 0xB304
            else:
                vea = (EA_DATA if to_fpu else EA_DATA_ALT) if n == 4 else \
                      (EA_MEM if to_fpu else EA_MEM_ALT)
                prim = prim_eval_ea(to_fpu, 0, vea, n)
            if to_fpu:
                S = [('prim', prim, P_WAIT), ('opw', n // 4), ('ctl_in', lst), ('end', P_IDLE)]
            else:
                S = [('ctl_out', lst), ('prim', prim, P_WAIT), ('opr', n // 4), ('end', P_IDLE)]
        else:
            dyn = (cmd >> 11) & 1
            to_fpu = opclass == 6
            prim = 0x810C if to_fpu else 0xA10C
            S = []
            if dyn:
                S += [('prim', 0x8C00 | ((cmd >> 4) & 7), P_WAIT), ('opw', 1), ('mask_dyn',)]
            else:
                S += [('mask_static',)]
            if to_fpu:
                S += [('prim', prim, P_WAIT), ('rsel',), ('opw', None), ('regs_in',), ('end', P_IDLE)]
            else:
                S += [('regs_out',), ('prim', prim, P_WAIT), ('rsel',), ('opr', None), ('end', P_IDLE)]
        self.dialog = Dialog('gen', cmd, S)
        self._run()

    # ---------------------------------------------------- the step machine
    def _run(self):
        """Execute steps until one waits for the MPU or for time."""
        d = self.dialog
        while d is not None and d.pc < len(d.steps):
            step = d.steps[d.pc]
            op = step[0]
            if op == 'prim':
                self.resp = step[1]
                return                                  # wait for the read
            if op in ('opw', 'opr'):
                # Entering the step sets the count: opr sends what is in
                # data, opw takes step[1] long words, or three per listed
                # register (FMOVEM) when step[1] is None.
                if op == 'opr' and not d.count:
                    d.count = len(d.data)
                elif op == 'opw' and not d.count and not d.data:
                    d.count = step[1] if step[1] is not None else 3 * len(self._mask_regs(d))
                if d.count:
                    return
                d.pc += 1
                continue
            if op == 'rsel':
                return
            if op == 'release':
                d.released = True
                d.pc += 1
                continue
            if op == 'compute':
                self._compute(d, step[1])
                return
            if op == 'done':
                self.dialog = None
                if self.busy == 0:
                    self.resp = P_IDLE
                return
            if op == 'end':
                self.resp = step[1]
                self.dialog = None
                return
            if op == 'end_out':
                self._end_out(d)
                return
            if op == 'ctl_in':
                self._ctl_in(d, step[1])
            elif op == 'ctl_out':
                self._ctl_out(d, step[1])
            elif op == 'mask_static':
                d.mask = d.word & 0xFF
            elif op == 'mask_dyn':
                d.mask = d.dreg & 0xFF
                d.data = []
            elif op == 'regs_out':
                d.data = []
                for r in self._mask_regs(d):
                    v = self.st.fp[r].bits96()
                    d.data += [(v >> 64) & 0xFFFFFFFF, (v >> 32) & 0xFFFFFFFF, v & 0xFFFFFFFF]
            elif op == 'regs_in':
                for i, r in enumerate(self._mask_regs(d)):
                    w = d.data[3 * i:3 * i + 3]
                    self.st.fp[r] = F.reg_from_bits96((w[0] << 64) | (w[1] << 32) | w[2])
            d.pc += 1

    def _mask_regs(self, d):
        """Registers in transfer order. FPU 4.7.1.6: the list is scanned from
        its most significant bit; mode 0x means bit 7 is FP7 (FP7 first), 1x
        means bit 7 is FP0."""
        fp0_first = (d.word >> 12) & 1
        regs = []
        for bit in range(7, -1, -1):
            if d.mask >> bit & 1:
                regs.append(7 - bit if fp0_first else bit)
        return regs

    def _read_response(self):
        r = self.resp
        d = self.dialog
        if self.pv or d is None or d.released:
            return r
        step = d.steps[d.pc]
        if step[0] == 'prim' and r == step[1]:
            nxt = step[2]
            if nxt is None:
                return r                    # a take-exception primitive persists
            d.pc += 1
            if nxt == 'release':
                d.released = True
            else:
                self.resp = nxt
            self._run()
        return r

    def _operand_write(self, v):
        if self.frame is not None and self.frame['dir'] == 'restore':
            self.frame['data'].append(v & 0xFFFFFFFF)
            self.opreg = v & 0xFFFFFFFF
            if len(self.frame['data']) == self.frame['n']:
                self._restore_done()
            return
        if self._expect() != 'opw':
            self._violation()
            return
        d = self.dialog
        d.data.append(v & 0xFFFFFFFF)
        self.opreg = v & 0xFFFFFFFF
        d.count -= 1
        if d.count == 0:
            if d.steps[d.pc][0] == 'opw' and d.steps[d.pc][1] == 1 and \
                    d.pc + 1 < len(d.steps) and d.steps[d.pc + 1][0] in ('mask_dyn', 'compute'):
                d.dreg = d.data[0]
            d.pc += 1
            self._run()

    def _operand_read(self):
        if self.frame is not None and self.frame['dir'] == 'save':
            f = self.frame
            v = f['data'][f['n'] - 1 - f['i']]       # highest address first
            f['i'] += 1
            if f['i'] == f['n']:
                self._save_done()
            return v
        if self._expect() != 'opr':
            self._violation()
            return 0xFFFFFFFF
        d = self.dialog
        v = d.data[len(d.data) - d.count]
        self.opreg = v
        d.count -= 1
        if d.count == 0:
            d.pc += 1
            self._run()
        return v

    def _regsel_read(self):
        if self._expect() != 'rsel':
            self._violation()
            return 0xFFFF
        d = self.dialog
        d.pc += 1
        self._run()
        return d.mask << 8           # FPU 7.2.9: the low byte reads as zeros

    # -------------------------------------------------------------- compute
    def _compute(self, d, what):
        """Start a computation; its effects land when the ticks run out."""
        self.busy = max(1, self.latency(d.word))
        self.resp = P_RELEASE if d.released else P_WAIT

        def done():
            if self.dialog is not d:
                return
            if what == 'arith':
                self._arith(d)
            else:
                self._convert(d)
            d.pc += 1
            self._run()
            if self.dialog is None and self.latched:
                self._begin(*self.latched)
        self.on_done = done

    def _image_of(self, d):
        rx = (d.word >> 10) & 7
        n = F.FMT_BYTES[rx]
        w = d.data
        if n <= 4:
            return w[0] >> (8 * (4 - n))             # FPU figure 7-4: MS-aligned
        if n == 8:
            return (w[0] << 32) | w[1]
        return (w[0] << 64) | (w[1] << 32) | w[2]

    def _arith(self, d):
        opclass = (d.word >> 13) & 7
        image = self._image_of(d) if opclass == 2 and ((d.word >> 10) & 7) != 7 else None
        res = A.general(self.st, d.word, image)
        A.apply(self.st, res)
        if res.etemp is not None:
            self.etemp = res.etemp
        if A.trap_vector(res.exc, self.st.enable) is not None:
            self.exc_pend = True          # reported at the next instruction (FPU 6.1)

    def _convert(self, d):
        res = A.general(self.st, d.word, None, d.dreg)
        A.apply(self.st, res)
        if res.etemp is not None:
            self.etemp = res.etemp
        n = res.mem_bytes
        v = res.mem << (8 * (4 - n)) if n < 4 else res.mem
        nl = (n + 3) // 4
        d.data = [(v >> (32 * (nl - 1 - i))) & 0xFFFFFFFF for i in range(nl)]
        d.count = 0
        d.trap = A.trap_vector(res.exc, self.st.enable) or 0

    def _end_out(self, d):
        """FPU 7.5.4.2: a mid-instruction exception in place of the final null."""
        trap = d.trap
        self.dialog = None
        if trap:
            self.exc_pend = True
            self.resp = 0x1D00 | trap
        else:
            self.resp = P_IDLE

    def _ctl_in(self, d, lst):
        w = list(d.data)
        if lst & 4:
            self.st.fpcr = w.pop(0) & 0x0000FFFF       # FPU 2.2: 31-16 read as zero
        if lst & 2:
            self.st.fpsr = w.pop(0) & 0x0FFFFFF8       # MODEL CHOICE: unused bits 0
        if lst & 1:
            self.st.fpiar = w.pop(0)

    def _ctl_out(self, d, lst):
        d.data = []
        if lst & 4:
            d.data.append(self.st.fpcr)
        if lst & 2:
            d.data.append(self.st.fpsr)
        if lst & 1:
            d.data.append(self.st.fpiar)

    # ------------------------------------------------------------ FSAVE
    def _save(self):
        """FPU 6.4.3 and 7.2.3."""
        if self.frame is not None:
            return FW_INVALID                      # nested save/restore, 6.2.8
        if self.null_state:
            return FW_NULL
        if self.busy:
            return FW_AGAIN                        # end/middle phase: come again
        d = self.dialog
        # A dialog whose first primitive has not been read has transferred
        # nothing: the idle frame's pending-instruction code can restart it.
        fresh = d is not None and d.pc == d.first_prim()
        if d is None or fresh or self.pv:
            longs = self._idle_frame(d)
            fw = FW_IDLE
        else:
            longs = self._busy_frame()
            fw = FW_BUSY
        self.frame = {'dir': 'save', 'data': longs, 'n': len(longs), 'i': 0}
        return fw

    def _biu_flags(self, d):
        code = PEND_NONE
        if d is not None:
            code = PEND_COND if d.kind == 'cond' else PEND_GEN
        flags = (code << 28) | 0xFFFF
        if self.pv:
            flags |= 1 << 31
        if not self.exc_pend:
            flags |= 1 << 27                       # active low (FPU 6.4.2.2)
        flags |= 1 << 26                           # no operand move pending
        return flags

    def _idle_frame(self, d):
        """FPU figure 6-4, in address order $04..$18."""
        word = d.word if d is not None else 0xFFFF
        e = self.etemp.bits96()
        return [(word << 16) | 0xFFFF,
                (e >> 64) & 0xFFFFFFFF, (e >> 32) & 0xFFFFFFFF, e & 0xFFFFFFFF,
                self.opreg, self._biu_flags(d)]

    def _busy_frame(self):
        """Opaque (FPU 6.4.2.3): our own layout, see doc/model.md."""
        d = self.dialog
        e = self.etemp.bits96()
        longs = [0x52443838,                                   # 'RD88' tag
                 (d.word << 16) | (1 if d.kind == 'cond' else 0) << 8 | d.pc,
                 (d.count << 16) | d.mask,
                 d.dreg, self.resp, self._biu_flags(d) & ~0xFF | d.trap,
                 (e >> 64) & 0xFFFFFFFF, (e >> 32) & 0xFFFFFFFF, e & 0xFFFFFFFF,
                 len(d.data) | (1 << 8 if d.released else 0)]
        longs += d.data
        longs += [0xFFFFFFFF] * (BUSY_LONGS - len(longs))
        assert len(longs) == BUSY_LONGS
        return longs

    def _save_done(self):
        """FPU 6.4.3: after the frame, idle with no pending exceptions."""
        self.frame = None
        self.dialog = None
        self.latched = None
        self.exc_pend = False
        self.pv = False
        self.resp = P_IDLE

    # --------------------------------------------------------- FRESTORE
    def _restore(self, fw):
        """FPU 6.4.4 and 7.2.4: abort, validate, then take the frame."""
        self._abort()
        if fw >> 8 == 0:
            self.reset()                            # 6.4.2.1: the reset function
            self.restore_readback = fw
            return
        if fw == FW_IDLE:
            n = IDLE_LONGS
        elif fw == FW_BUSY:
            n = BUSY_LONGS
        else:
            self.restore_readback = FW_INVALID
            return
        self.restore_readback = fw
        self.frame = {'dir': 'restore', 'data': [], 'n': n, 'fw': fw}

    def _restore_done(self):
        f = self.frame
        self.frame = None
        self.null_state = False
        w = f['data']
        if f['fw'] == FW_IDLE:
            flags = w[5]
            self.etemp = F.reg_from_bits96((w[1] << 64) | (w[2] << 32) | w[3])
            self.opreg = w[4]
            self.exc_pend = not (flags >> 27) & 1
            self.pv = bool(flags >> 31 & 1)
            code = (flags >> 28) & 7
            self.resp = P_PROTOCOL if self.pv else P_IDLE
            if code in (PEND_GEN, PEND_COND) and not self.pv:
                # Regenerate the first primitive (doc/architecture.md).
                self._begin('cond' if code == PEND_COND else 'gen', w[0] >> 16)
            return
        word = w[1] >> 16
        kind = 'cond' if (w[1] >> 8) & 1 else 'gen'
        self.dialog = None
        self._begin(kind, word)                     # rebuild the step list
        d = self.dialog
        d.pc = w[1] & 0xFF
        d.count = w[2] >> 16
        d.mask = w[2] & 0xFFFF
        d.dreg = w[3]
        self.resp = w[4]
        flags = w[5]
        self.exc_pend = not (flags >> 27) & 1
        d.trap = flags & 0xFF
        self.etemp = F.reg_from_bits96((w[6] << 64) | (w[7] << 32) | w[8])
        nd = w[9] & 0xFF
        d.released = bool(w[9] >> 8 & 1)
        d.data = w[10:10 + nd]


# -----------------------------------------------------------------------------
# Conditional predicates, FPU 4.4 / table 4-22
# -----------------------------------------------------------------------------
def evaluate_predicate(p, cc):
    """Returns (true/false, BSUN). cc is FPSR bits 27-24: N Z I NAN."""
    N, Z, NAN = bool(cc & 8), bool(cc & 4), bool(cc & 1)
    base = p & 0x0F
    eq = Z
    gt = not (NAN or Z or N)
    lt = N and not (NAN or Z)
    un = NAN
    table = {
        0x0: False,                 # F / SF
        0x1: eq,                    # EQ / SEQ
        0x2: gt,                    # OGT / GT
        0x3: gt or eq,              # OGE / GE   Z v ~(NAN v N)
        0x4: lt,                    # OLT / LT
        0x5: lt or eq,              # OLE / LE   Z v (N ^ ~NAN)
        0x6: gt or lt,              # OGL / GL   ~(NAN v Z)
        0x7: not un,                # OR / GLE
        0x8: un,                    # UN / NGLE
        0x9: un or eq,              # UEQ / NGL
        0xA: un or gt,              # UGT / NLE  NAN v ~(N v Z)
        0xB: un or gt or eq,        # UGE / NLT  NAN v Z v ~N
        0xC: un or lt,              # ULT / NGE  NAN v (N ^ ~Z)
        0xD: un or lt or eq,        # ULE / NGT  NAN v Z v N
        0xE: not eq,                # NE / SNE
        0xF: True,                  # T / ST
    }
    tf = table[base]
    # FPU 4.4.1/4.4.3: the 01xxxx predicates are the IEEE-nonaware ones and set
    # BSUN when NAN is set; EQ (000001) and NE (001110) never do, but SEQ and
    # SNE (010001, 011110) do.
    bsun = bool(p & 0x10) and NAN
    return tf, bsun
