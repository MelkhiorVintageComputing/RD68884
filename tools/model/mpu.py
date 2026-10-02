# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The main processor's half of the coprocessor interface, for testing.

An MC68020 executing F-line instructions, reduced to what UM section 7 says
it does with each response primitive. It talks to anything with read(cir) and
write(cir, value): the Python model in cpif.py today, and later the RTL
through a bus functional model.

    m = MPU(fpu)
    m.fgen(0x4822, ea=('imm', 3, 4))   # FADD.L #3,FP0 (command word only)
    m.fcond(0x01)                       # the dialog of FBEQ, FScc, FTRAPcc ...
    m.fsave(('predec', 7)); m.frestore(('postinc', 7))

Effective addresses are tuples: ('dn', n), ('an', n), ('ind', n),
('postinc', n), ('predec', n), ('abs', addr), ('imm', value, nbytes).
"""

from .cpif import (CIR_RESPONSE, CIR_CONTROL, CIR_SAVE, CIR_RESTORE,
                   CIR_COMMAND, CIR_CONDITION, CIR_OPERAND, CIR_REGSEL,
                   CIR_INSTADDR)


class Memory:
    def __init__(self):
        self.b = {}

    def read(self, addr, n):
        v = 0
        for i in range(n):
            v = (v << 8) | self.b.get((addr + i) & 0xFFFFFFFF, 0)
        return v

    def write(self, addr, n, v):
        for i in range(n):
            self.b[(addr + i) & 0xFFFFFFFF] = (v >> (8 * (n - 1 - i))) & 0xFF


class CPException(Exception):
    """The MPU took an exception: kind is 'pre', 'mid', 'fline', 'format'."""

    def __init__(self, kind, vector):
        super().__init__(f'{kind} exception, vector {vector}')
        self.kind = kind
        self.vector = vector


# Table 7-4 valid-EA codes against the categories of table 4-10.
def ea_ok(ea, code):
    mode = ea[0]
    data = mode != 'an'
    memory = mode not in ('dn', 'an')
    control = mode in ('ind', 'abs')
    alterable = mode != 'imm'
    return {0: control and alterable, 1: data and alterable,
            2: memory and alterable, 3: alterable, 4: control, 5: data,
            6: memory, 7: True}[code]


class MPU:
    def __init__(self, fpu, mem=None, poll_hook=None):
        self.fpu = fpu
        self.mem = mem or Memory()
        self.d = [0] * 8
        self.a = [0] * 8
        self.pc = 0x1000                 # address of the current F-line word
        self.poll_hook = poll_hook       # called where interrupts are taken
        self.reads = 0                   # response reads, for latency tests
        self.pc_passed = 0

    # ------------------------------------------------------------- operands
    def _ea_read(self, ea, n):
        mode = ea[0]
        if mode == 'dn':
            return self.d[ea[1]] & ((1 << (8 * n)) - 1)
        if mode == 'an':
            return self.a[ea[1]] & ((1 << (8 * n)) - 1)
        if mode == 'imm':
            return ea[1]
        return self.mem.read(self._addr(ea, n), n)

    def _ea_write(self, ea, n, v):
        mode = ea[0]
        mask = (1 << (8 * n)) - 1
        if mode == 'dn':
            self.d[ea[1]] = (self.d[ea[1]] & ~mask) | (v & mask)
        elif mode == 'an':
            self.a[ea[1]] = v & 0xFFFFFFFF
        else:
            self.mem.write(self._addr(ea, n), n, v)

    def _addr(self, ea, n):
        mode, r = ea[0], ea[1] if len(ea) > 1 else None
        if mode == 'ind':
            return self.a[r]
        if mode == 'abs':
            return r
        step = 2 if (n == 1 and r == 7) else n      # UM: A7 stays word-aligned
        if mode == 'postinc':
            a = self.a[r]
            self.a[r] = (a + step) & 0xFFFFFFFF
            return a
        if mode == 'predec':
            self.a[r] = (self.a[r] - step) & 0xFFFFFFFF
            return self.a[r]
        raise ValueError(ea)

    # ------------------------------------------------------- the primitives
    def _service(self, ea):
        """Read and service primitives until a null with CA = 0.

        Returns the last primitive. Raises CPException for a take-exception
        primitive or an invalid effective address (UM 7.4)."""
        f = self.fpu
        while True:
            p = f.read(CIR_RESPONSE)
            self.reads += 1
            ca, pc, dr = p >> 15 & 1, p >> 14 & 1, p >> 13 & 1
            if pc:
                f.write(CIR_INSTADDR, self.pc)
                self.pc_passed += 1
            if (p >> 8) & 0x3F in (0x1C, 0x1D) and not ca:
                f.write(CIR_CONTROL, 2)              # exception acknowledge
                raise CPException('pre' if (p >> 8) & 0x3F == 0x1C else 'mid', p & 0xFF)
            if (p >> 9) & 0xF == 0b0100:             # null
                if not ca:
                    return p
                if p >> 8 & 1 and self.poll_hook:    # IA: interrupts allowed
                    self.poll_hook(self)
                continue
            if (p >> 11) & 3 == 0b10:                # evaluate EA and transfer
                n = p & 0xFF
                if ea is None or not ea_ok(ea, (p >> 8) & 7):
                    f.write(CIR_CONTROL, 1)
                    raise CPException('fline', 11)
                self._transfer(ea, n, dr)
            elif (p >> 8) & 0x1F == 0x0C:            # transfer single main register
                r = p & 7
                f.write(CIR_OPERAND, self.a[r] if p & 8 else self.d[r])
            elif (p >> 8) & 0x1F == 0x01:            # transfer multiple coproc regs
                self._transfer_multiple(ea, p & 0xFF, dr)
            else:
                raise RuntimeError(f'primitive ${p:04X} not handled')
            if not ca:
                return p

    def _transfer(self, ea, n, dr):
        f = self.fpu
        if not dr:
            v = self._ea_read(ea, n)
            if n < 4:
                f.write(CIR_OPERAND, v << (8 * (4 - n)))
            else:
                for i in range(n // 4):
                    f.write(CIR_OPERAND, (v >> (32 * (n // 4 - 1 - i))) & 0xFFFFFFFF)
        else:
            if n < 4:
                v = f.read(CIR_OPERAND) >> (8 * (4 - n))
            else:
                v = 0
                for _ in range(n // 4):
                    v = (v << 32) | f.read(CIR_OPERAND)
            self._ea_write(ea, n, v)

    def _transfer_multiple(self, ea, length, dr):
        f = self.fpu
        mask = f.read(CIR_REGSEL) >> 8
        count = bin(mask).count('1')
        mode, r = ea[0], ea[1]
        if mode == 'predec':
            for _ in range(count):
                self.a[r] = (self.a[r] - length) & 0xFFFFFFFF
                self._xfer_one(self.a[r], length, dr)
        else:
            addr = self.a[r] if mode in ('ind', 'postinc') else r
            for i in range(count):
                self._xfer_one(addr + i * length, length, dr)
            if mode == 'postinc':
                self.a[r] = (self.a[r] + count * length) & 0xFFFFFFFF

    def _xfer_one(self, addr, length, dr):
        f = self.fpu
        for i in range(length // 4):
            if dr:
                self.mem.write(addr + 4 * i, 4, f.read(CIR_OPERAND))
            else:
                f.write(CIR_OPERAND, self.mem.read(addr + 4 * i, 4))

    # -------------------------------------------------------- instructions
    def fgen(self, cmd, ea=None):
        """cpGEN: write the command word, then follow the primitives."""
        self.fpu.write(CIR_COMMAND, cmd)
        return self._service(ea)

    def fcond(self, pred):
        """cpBcc/cpScc/cpDBcc/cpTRAPcc: returns the TF bit."""
        self.fpu.write(CIR_CONDITION, pred)
        return self._service(None) & 1

    def fsave(self, ea):
        f = self.fpu
        while True:
            fw = f.read(CIR_SAVE)
            if fw >> 8 == 0x01:
                if self.poll_hook:
                    self.poll_hook(self)
                continue
            break
        if fw >> 8 == 0x02:
            f.write(CIR_CONTROL, 1)
            raise CPException('format', 14)
        n = 0 if fw >> 8 == 0 else fw & 0xFF
        if ea[0] == 'predec':
            self.a[ea[1]] = (self.a[ea[1]] - n - 4) & 0xFFFFFFFF
            base = self.a[ea[1]]
        else:
            base = self.a[ea[1]] if ea[0] == 'ind' else ea[1]
        self.mem.write(base, 4, fw << 16)
        # FPU 6.4.3: filled from higher addresses to lower.
        for i in range(n // 4 - 1, -1, -1):
            self.mem.write(base + 4 + 4 * i, 4, f.read(CIR_OPERAND))
        return fw

    def frestore(self, ea):
        f = self.fpu
        base = self.a[ea[1]] if ea[0] in ('ind', 'postinc') else ea[1]
        fw = self.mem.read(base, 4) >> 16
        f.write(CIR_RESTORE, fw)
        rb = f.read(CIR_RESTORE)
        if rb >> 8 == 0x02:
            f.write(CIR_CONTROL, 1)
            raise CPException('format', 14)
        n = 0 if fw >> 8 == 0 else fw & 0xFF
        for i in range(n // 4):
            f.write(CIR_OPERAND, self.mem.read(base + 4 + 4 * i, 4))
        if ea[0] == 'postinc':
            self.a[ea[1]] = (self.a[ea[1]] + n + 4) & 0xFFFFFFFF
        return fw
