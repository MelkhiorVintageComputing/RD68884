# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The microword: one table, read by the assembler, the RTL package generator
and the instruction-set simulator, so the three cannot disagree.

Each field is (name, values). values is a list of mnemonics, encoded 0..n-1,
or an int: a numeric field of that many bits. The first mnemonic of every
enumerated field is its do-nothing value, so an omitted field is inert.

doc/microcode.md describes what each value does; tools/iss/core.py is the
executable definition and rtl/rd68884_seq.sv the hardware one.
"""

UADDR_BITS = 12          # 4K words

FIELDS = [
    # ---- sequencing ----------------------------------------------------------
    # NEXT  upc+1
    # JUMP  TGT
    # CALL  push upc+1, TGT
    # RET   pop
    # BR    COND ? TGT : upc+1
    # WAIT  COND ? upc+1 : upc        (stay here until the condition holds)
    # DISP  TGT | index(IDX)          (a jump table)
    # LOOP  CTR != 0 ? (CTR--, TGT) : upc+1
    ('SEQ', ['NEXT', 'JUMP', 'CALL', 'RET', 'BR', 'WAIT', 'DISP', 'LOOP']),
    ('NEG', 1),                         # invert COND
    # RESP_READ, RSEL_READ and SAVE_READ are sticky copies of the BIU's
    # one-clock events (doc/microcode.md), re-armed by the action that makes
    # the next one meaningful: a response write, RSEL_WR, SAVE_WR.
    ('COND', ['TRUE', 'CMD_PEND', 'CMD_COND', 'OPW_VALID', 'OPR_VALID',
              'RESP_READ', 'RSEL_READ', 'SAVE_REQ', 'SAVE_READ', 'EXC_PEND',
              'NULL_STATE', 'CTR_ZERO', 'MASK_ZERO', 'TF', 'BSUN', 'BSUN_EN',
              'REST_NULL', 'REST_IDLE', 'FLINE', 'REPORTS', 'PCEN', 'LST_CR',
              'LST_SR', 'LST_IAR', 'DYN_LIST', 'PV', 'PEND_GEN', 'PEND_COND',
              'IS_COND', 'CMD_FMOVE',
              # M5: the datapath (doc/microcode.md)
              'A_ZERO', 'A_INF', 'A_NAN', 'A_SNAN', 'A_SIGN', 'B_ZERO', 'B_INF',
              'B_NAN', 'B_SNAN', 'B_SIGN', 'A_J', 'AE_LT_EMIN', 'AE_GT_EMAX',
              'AE_LT_XMIN', 'AE_LT_B', 'AE_GE_IMM', 'AE_ODD', 'RINEX', 'RX0',
              'OVF_INF', 'INT_OVF', 'AE_EQ_B', 'RX1', 'TRAP', 'SUPPRESS',
              'PREC_X', 'SIGN_XOR', 'RM_MODE', 'PREC_SGLX',
              # M6: packed decimal (XI holds the image), the k-factor in
              # MASK[6:0], the quotient's parity in C[0]
              'P_SPECIAL', 'P_SE', 'K_GT17', 'K_POS', 'Q_ODD',
              # M7: A's mantissa exactly 1 (a power of two)
              'A_POW2']),
    ('IDX', ['OPCLASS', 'RX', 'OPMODE']),
    ('TGT', UADDR_BITS),
    ('IMM', 16),

    # ---- the response CIR and the rest of the BIU ------------------------------
    # RESP: WR writes IMM | ORS into the response, WRC the same but only if no
    # command has been latched (rd68884_biu resp_cond_i).
    ('RESP', ['NONE', 'WR', 'WRC']),
    ('ORS', ['NONE', 'PC', 'TF', 'VEC', 'DN', 'DNPC']),
    ('ONESHOT', 1),
    ('EXPECT', ['CMD', 'RESP', 'OPW', 'OPR', 'RSEL']),
    # BIU actions. CMD_ACK also copies the latched word into CMD and its kind
    # into IS_COND; OPW_ACK takes the operand long word; OPR_WR offers TBUS
    # for reading; SAVE_WR and RESTORE_WR answer with TBUS[15:0], XFER long
    # words to follow; FPIAR_WR writes TBUS to FPIAR; CLEAR returns the BIU to
    # idle (rd68884_biu clear_i).
    ('BIU', ['NONE', 'CMD_ACK', 'OPW_ACK', 'OPR_WR', 'RSEL_WR', 'SAVE_WR',
             'RESTORE_WR', 'FPIAR_WR', 'CLEAR']),
    ('XFER', 6),

    # ---- the 32-bit transfer bus ---------------------------------------------
    # TBUS = TSRC; T <= TBUS; TDST <= TBUS, all in the same clock.
    ('TSRC', ['T', 'OPW', 'IMM', 'FPCR', 'FPSR', 'FPIAR', 'CMDW', 'XI0', 'XI1',
              'XI2', 'FLAGS', 'RESTW', 'ONES']),
    ('TDST', ['NONE', 'FPCR', 'FPSR', 'MASK', 'XI0', 'XI1', 'XI2', 'CMD',
              'FLAGS']),

    # ---- the floating-point registers -------------------------------------------
    # RF READ: RFQ <= RF[addr] (available to the next microinstruction).
    # RF WRITE: RF[addr] <= A (A as it was at the start of the clock).
    # CROM: RFQ <= the constant ROM (tools/ucode/crom.py) at IMM + an offset:
    # RFA IMM none, CMD the command word's bits 6-0, ELO A's exponent bits
    # 5-0, EHI its bits 12-6, EXP its bits 9-0; QSTK <= the entry's sticky
    # bit (0 on a READ).
    ('RF', ['NONE', 'READ', 'WRITE', 'CROM']),
    # FPC: the command word's bits 2-0 (FSINCOS's cosine register).
    # PSR (CROM): the offset is the precision register.
    ('RFA', ['IMM', 'RX', 'RY', 'RN', 'ETEMP', 'CTR', 'CMD', 'ELO', 'EHI', 'EXP', 'FPC',
             'PSR']),
    # A <= RFQ, unpack(XI) in a format, the default NaN, zero, or B (with
    # the sticky bit from its saved copy: B's save and A's restore).
    ('ASRC', ['NONE', 'RFQ', 'UNPACKX', 'NAN', 'ZERO', 'B', 'UNPACKS',
              'UNPACKD', 'UNPACKL', 'UNPACKW', 'UNPACKB']),
    # B <= RFQ, or A (and STKB <= STK).
    ('BSRC', ['NONE', 'RFQ', 'A']),
    # XI <= A packed: extended, single, double; an integer (B/W/L from the
    # command word's format); a NaN's top bits as an integer; the saturated
    # integer of A's sign (FPU 6.1.3).
    # Packed decimal (FPU figure 3-11): DIGL shifts the mantissa digits
    # {XI0[3:0], XI1, XI2} left a digit, EDIGL the exponent digits XI0[27:16];
    # DIGR shifts the mantissa digits right a digit, the new one (the
    # remainder of a division by ten, doc/microcode.md) entering at XI0[3:0];
    # EDIGR the same for XI0[27:16]; EDIG3 puts it in XI0[15:12]; PINIT
    # clears XI but for SM = A's sign and SE = A's exponent's sign.
    ('XOP', ['NONE', 'PACKX', 'PACKS', 'PACKD', 'PACKI', 'PACKNI', 'PACKSAT',
             'DIGL', 'EDIGL', 'DIGR', 'EDIGR', 'EDIG3', 'PINIT']),
    # FPSR: the condition codes from A, clear the exception byte, accrue it
    # (FPU 2.3.4), or set the condition codes to IMM[3:0].
    # QSIGN: the quotient byte's sign <= A's sign xor B's; QBITS: its seven
    # bits <= C[6:0] (FMOD, FREM).
    ('FPSR', ['NONE', 'CC', 'CLREXC', 'CC_CLREXC', 'ACCRUE', 'CCIMM', 'QSIGN',
              'QBITS']),
    ('EXCSET', 8),                    # OR into the FPSR exception byte

    # ---- the arithmetic ---------------------------------------------------------
    # Mantissa operations, one per clock (doc/microcode.md has each one).
    ('MOP', ['NONE', 'ADD', 'SUB', 'NEG', 'SHRA', 'SHRB', 'NORM', 'RSH1',
             'ROUND', 'ROUNDX', 'CLRQ', 'CMPM',
             'MULSTEP', 'MULFIN', 'DIVSTEP', 'DIVFIN', 'SQSTEP', 'SQFIN',
             'EXPF', 'QUIET', 'INFA', 'ZEROM', 'MUL10', 'ADDDIG', 'QINC']),
    # Exponent and shift-amount operations.
    ('EOP', ['NONE', 'ADDB', 'SUBB', 'ADDI', 'LDI', 'SA_AB', 'SA_IA', 'SA_IMM',
             'SA_EMIN', 'LDEMIN', 'HALF', 'LDB', 'ADDBI', 'NEGE', 'EXP10', 'LOG10',
             'LDK', 'SUBK', 'LDM', 'LO6']),
    ('SGN', ['NONE', 'NEG', 'ABS', 'XOR', 'RMZ', 'XI0']),
    # The precision and rounding mode registers every precision-dependent
    # operation reads (PSR: X, S, D or SGLX; RMR: a rounding mode).
    ('PSR', ['NONE', 'FPCR', 'X', 'S', 'D', 'SGLX']),
    ('RMR', ['NONE', 'FPCR', 'RZ', 'RN', 'RM']),

    # ---- counters, the FMOVEM mask, flags ---------------------------------------
    ('CTR', ['NONE', 'LOAD', 'DEC', 'LOADE']),
    # LOAD: MASK <= the command word's static list. NEXT: RN <= the next
    # register of the list in transfer order (FPU 4.7.1.6), and clear it.
    ('MASK', ['NONE', 'LOAD', 'NEXT']),
    ('FLAG', ['NONE', 'SET_EXC', 'CLR_EXC', 'SET_NULL', 'CLR_NULL', 'BSUN',
              'RESET', 'PEND_NONE', 'PEND_CMD', 'SET_COND', 'CLR_COND', 'SET_STK',
              'CLR_STK']),
]


def layout():
    """[(name, lsb, width, values)] from bit 0 up, and the total width."""
    out = []
    pos = 0
    for name, vals in FIELDS:
        w = vals if isinstance(vals, int) else max(1, (len(vals) - 1).bit_length())
        out.append((name, pos, w, vals))
        pos += w
    return out, pos


def enum(field, mnemonic):
    for name, vals in FIELDS:
        if name == field:
            return vals.index(mnemonic)
    raise KeyError(field)


def width():
    return layout()[1]
