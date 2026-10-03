# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""The transcendental microcode (FPU 4.6, accuracy FPU 4.3.2: one unit in
the last place of double at worst).

Every function is computed in the 72-bit working format, truncating, with
an error of a few units of 2^-71 relative: some 2^-60 at worst, far inside
the bound; the result is then rounded as any result (`post`), STK forced
on. The methods are the table-driven ones of P. T. P. Tang (ACM TOMS 15:2,
1989, exp; 16:4, 1990, log) as Motorola's FPSP arranges them, with Taylor
coefficients (the intervals are small enough that minimax buys nothing);
trigonometric arguments are reduced exactly, by Cody and Waite below 2^20
and by Payne and Hanek's method above (doc/microcode.md).

The special operands follow each instruction's operation table in FPU 4.6,
as tools/model/transcend.py reads them.
"""

import crom

C = crom.addr
# Register-file temporaries for the transcendentals (doc/microcode.md).
T = {n: 24 + i for i, n in enumerate(
    ['X', 'N', 'R', 'P', 'M', 'TJ', 'RES', 'U', 'U2', 'K', 'FR', 'J', 'F', 'A1', 'A2',
     'IDX', 'TT', 'MI', 'H0', 'L0', 'H1', 'L1', 'H2', 'L2', 'H3', 'L3', 'V0', 'V1',
     'V2', 'KM', 'Q', 'S', 'CC', 'T1', 'T2', 'X2'])}
OPERR, DZ = 0x20, 0x04

FUNCS = {0x02: 'op_fsinh', 0x06: 'op_flognp1', 0x08: 'op_fetoxm1', 0x09: 'op_ftanh',
         0x0A: 'op_fatan', 0x0C: 'op_fasin', 0x0D: 'op_fatanh', 0x0E: 'op_fsin',
         0x0F: 'op_ftan', 0x10: 'op_fetox', 0x11: 'op_ftwotox', 0x12: 'op_ftentox',
         0x14: 'op_flogn', 0x15: 'op_flog10', 0x16: 'op_flog2', 0x19: 'op_fcosh',
         0x1C: 'op_facos', 0x1D: 'op_fcos', 0x30: 'op_fsincos'}
# FPU table 4-13 note 3, as the model reads it (tools/model/arith.py).
ALIAS = {0x07: 0x06, 0x0B: 0x0A, 0x13: 0x12, 0x17: 0x16}
for _c in range(0x31, 0x38):
    ALIAS[_c] = 0x30


def emit(p):
    L, u = p.L, p.u
    n = [0]

    def lab(base):
        n[0] += 1
        return f'{base}_{n[0]}'

    # ---- the small pieces ----------------------------------------------------
    def ld(t):
        u(RF='READ', RFA='IMM', IMM=T[t])
        u(ASRC='RFQ')

    def ldb(t):
        u(RF='READ', RFA='IMM', IMM=T[t])
        u(BSRC='RFQ')

    def st(t):
        u(RF='WRITE', RFA='IMM', IMM=T[t])

    def ldc(name):
        u(RF='CROM', RFA='IMM', IMM=C(name))
        u(ASRC='RFQ')

    def ldcb(name):
        u(RF='CROM', RFA='IMM', IMM=C(name))
        u(BSRC='RFQ')

    def call(name):
        u(SEQ='CALL', TGT=name)

    def mul():
        call('mulz')

    def add():
        call('addz')

    def sub():                     # A = A - B
        u(SGN='NEG')
        call('addz')
        u(SGN='NEG')

    def div():
        call('divz')

    def horner(coefs, var):
        """A = sum coefs[i] var^i (CROM names), Horner from the top."""
        ldc(coefs[-1])
        for c in reversed(coefs[:-1]):
            ldb(var)
            mul()
            ldcb(c)
            add()

    def toexp():
        """A, an integer value below 2^17, into A's exponent (EOP LDM)."""
        u(FLAG='CLR_STK', EOP='SA_IA', IMM=63)
        u(MOP='SHRA')
        u(EOP='LDM')

    def result(sign_from=None):
        """Round A (inexact) as the result, and finish."""
        if sign_from:
            ldb(sign_from)
            u(SGN='XOR')
        u(FLAG='SET_STK', SEQ='CALL', TGT='post')
        u(SEQ='JUMP', TGT='finish')

    # =========================================================================
    # Arithmetic on internal values: zeros allowed (a zero mantissa, any
    # exponent), truncating, no flags.
    # =========================================================================
    L('mulz')                      # A = A x B
    u(SEQ='BR', COND='A_ZERO', TGT='mulz_r')
    u(SEQ='BR', NEG=1, COND='B_ZERO', TGT='mulc')
    u(MOP='ZEROM')
    L('mulz_r')
    u(SEQ='RET')

    L('addz')                      # A = A + B
    u(SEQ='BR', COND='B_ZERO', TGT='mulz_r')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='addz_1')
    u(ASRC='B', SEQ='RET')
    L('addz_1')
    u(SEQ='BR', NEG=1, COND='AE_LT_B', TGT='addz_2')
    u(ASRC='B', BSRC='A')
    L('addz_2')
    u(EOP='SA_AB', FLAG='CLR_STK')
    u(MOP='SHRB')
    u(SEQ='BR', COND='SIGN_XOR', TGT='addz_s')
    u(MOP='ADD')
    u(MOP='RSH1', SEQ='RET')
    L('addz_s')
    u(MOP='SUB')
    u(SEQ='BR', NEG=1, COND='RX0', TGT='addz_3')
    u(MOP='NEG', SGN='NEG')
    L('addz_3')
    u(SEQ='BR', COND='A_ZERO', TGT='addz_z')
    u(MOP='NORM', SEQ='RET')
    L('addz_z')
    u(MOP='ZEROM', SGN='ABS', SEQ='RET')

    L('divz')                      # A = A / B, B not zero
    u(SEQ='BR', COND='A_ZERO', TGT='mulz_r')
    u(SGN='XOR', SEQ='JUMP', TGT='divc')

    # Rounding to an integer value, to nearest (rintn) or down (rintm).
    for name, mode in (('rintn', 'RN'), ('rintm', 'RM')):
        L(name)
        u(SEQ='BR', COND='A_ZERO', TGT='mulz_r')
        # From 2^64 up an extended value has no fraction bits.
        u(SEQ='BR', COND='AE_GE_IMM', IMM=64, TGT='mulz_r')
        u(RMR=mode, FLAG='CLR_STK', EOP='SA_IA', IMM=63)
        u(MOP='SHRA')
        u(MOP='ROUNDX')
        u(RMR='FPCR', SEQ='BR', COND='A_ZERO', TGT=f'{name}_z')
        u(EOP='LDI', IMM=63)
        u(MOP='NORM', SEQ='RET')
        L(f'{name}_z')
        u(MOP='ZEROM', SGN='ABS', SEQ='RET')

    # =========================================================================
    # e^x, 2^x, 10^x (Tang): N = round(x 64 / ln 2), r = x - N ln2/64 in two
    # parts, 2^(N/64) = 2^M 2^(j/64) from the table, e^r - 1 by Taylor.
    # |x| < 2^17; the exponent goes out to +-65534 (catastrophic, as the
    # model's) when |M| reaches 2^16.
    # =========================================================================
    for kind in ('e', '2', '10'):
        L(f'exp_{kind}')
        st('X')
        if kind == 'e':
            ldcb('C64_LN2')
            mul()
        elif kind == '2':
            u(EOP='ADDI', IMM=6)
        else:
            ldcb('C64_LOG2_10')
            mul()
        call('rintn')
        st('N')
        if kind == '2':
            u(EOP='ADDI', IMM=-6 & 0xFFFF)
            st('R')
            ld('X')
            ldb('R')
            sub()
            ldcb('LN2')
            mul()
        else:
            part = 'LN2_64' if kind == 'e' else 'LOG10_2_64'
            ldcb(part + '_1')
            mul()
            st('R')
            ld('X')
            ldb('R')
            sub()
            st('R')
            ld('N')
            ldcb(part + '_2')
            mul()
            st('P')
            ld('R')
            ldb('P')
            sub()
            if kind == '10':
                ldcb('LN10')
                mul()
        u(SEQ='JUMP', TGT='exp_tail')
    L('exp_tail')
    st('R')
    horner([f'FACT{k}' for k in range(1, 8)], 'R')        # (e^r - 1) / r
    ldb('R')
    mul()
    st('P')
    ld('N')
    u(EOP='ADDI', IMM=-6 & 0xFFFF)
    call('rintm')                                           # M = floor(N / 64)
    st('M')
    u(EOP='ADDI', IMM=6)
    u(SGN='NEG')
    ldb('N')
    add()                                                   # j = N - 64 M
    toexp()
    u(RF='CROM', RFA='ELO', IMM=C('EXP2T0'))
    u(ASRC='RFQ')
    st('TJ')
    ldb('TJ')
    ld('P')
    mul()
    ldb('TJ')
    add()                                                   # 2^(j/64) (1 + p)
    st('RES')
    ld('M')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=16, TGT='exp_clamp')
    toexp()
    u(BSRC='A')
    ld('RES')
    u(EOP='ADDB', FLAG='SET_STK', SEQ='RET')
    L('exp_clamp')
    u(SEQ='BR', COND='A_SIGN', TGT='exp_clampn')
    ld('RES')
    u(EOP='LDI', IMM=0x7FFF)
    u(EOP='ADDI', IMM=0x7FFF, FLAG='SET_STK', SEQ='RET')
    L('exp_clampn')
    ld('RES')
    u(EOP='LDI', IMM=-0x7FFF & 0xFFFF)
    u(EOP='ADDI', IMM=-0x7FFF & 0xFFFF, FLAG='SET_STK', SEQ='RET')

    # expm1: |y| < 1/4 by Taylor, else e^y - 1.
    L('expm1')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=-2 & 0xFFFF, TGT='expm1_big')
    st('U')
    horner([f'FACT{k}' for k in range(1, 16)], 'U')
    ldb('U')
    u(SEQ='JUMP', TGT='mulz')
    L('expm1_big')
    call('exp_e')
    ldcb('ONE')
    sub()
    u(SEQ='RET')

    # One, exactly; A's sign set from a register.
    L('t_one')
    ldc('ONE')
    u(SEQ='JUMP', TGT='finish')
    L('t_mone')
    ldc('ONE')
    u(SGN='NEG', SEQ='JUMP', TGT='finish')
    L('t_pzero')
    u(ASRC='ZERO', SEQ='JUMP', TGT='finish')
    # 2^(+-65534): the certain over/underflow (the model's 2^(+-2^20)).
    L('t_hugep')
    ldc('ONE')
    u(EOP='LDI', IMM=0x7FFF)
    u(EOP='ADDI', IMM=0x7FFF, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('t_hugen')
    ldc('ONE')
    u(EOP='LDI', IMM=-0x7FFF & 0xFFFF)
    u(EOP='ADDI', IMM=-0x7FFF & 0xFFFF, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')

    # ---- FETOX, FTWOTOX, FTENTOX ------------------------------------------------
    for op, kind in (('op_fetox', 'e'), ('op_ftwotox', '2'), ('op_ftentox', '10')):
        L(op)
        u(SEQ='BR', COND='A_NAN', TGT='nan1')
        u(SEQ='BR', COND='A_ZERO', TGT='t_one')
        u(SEQ='BR', NEG=1, COND='A_INF', TGT=f'{op}_1')
        u(SEQ='BR', COND='A_SIGN', TGT='t_pzero')
        u(SEQ='JUMP', TGT='finish')
        L(f'{op}_1')
        u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=17, TGT=f'{op}_2')
        u(SEQ='BR', COND='A_SIGN', TGT='t_hugen')
        u(SEQ='JUMP', TGT='t_hugep')
        L(f'{op}_2')
        if kind == '2':
            # 2^n, n an integer: exactly 2^n (the model's exact case).
            u(SEQ='BR', COND='AE_GE_IMM', IMM=15, TGT=f'{op}_3')
            st('X2')
            call('rintn')
            ldb('X2')
            u(SEQ='BR', NEG=1, COND='B_EQ_A', TGT=f'{op}_g')
            toexp()
            u(BSRC='A')
            ldc('ONE')
            u(EOP='LDB', FLAG='CLR_STK', SEQ='CALL', TGT='post')
            u(SEQ='JUMP', TGT='finish')
            L(f'{op}_g')
            ld('X2')
        if kind == '10':
            # 10^n, n an integer from 0 to 31: the constant ROM's, rounded
            # as any result (exact up to 27, FPU 4.3.2's example).
            u(SEQ='BR', COND='A_SIGN', TGT=f'{op}_3')
            u(SEQ='BR', COND='AE_GE_IMM', IMM=5, TGT=f'{op}_3')
            st('X2')
            call('rintn')
            ldb('X2')
            u(SEQ='BR', NEG=1, COND='B_EQ_A', TGT=f'{op}_g')
            toexp()
            u(RF='CROM', RFA='ELO', IMM=crom.CR_P10)
            u(ASRC='RFQ', FLAG='CLR_STK', SEQ='CALL', TGT='post')
            u(SEQ='JUMP', TGT='finish')
            L(f'{op}_g')
            ld('X2')
        L(f'{op}_3')
        call(f'exp_{kind}')
        u(SEQ='CALL', TGT='post')
        u(SEQ='JUMP', TGT='finish')

    # ---- FETOXM1 ---------------------------------------------------------------
    L('op_fetoxm1')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='em1_1')
    u(SEQ='BR', COND='A_SIGN', TGT='t_mone')
    u(SEQ='JUMP', TGT='finish')
    L('em1_1')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=17, TGT='em1_2')
    u(SEQ='BR', COND='A_SIGN', TGT='t_mone')
    u(SEQ='JUMP', TGT='t_hugep')
    L('em1_2')
    call('expm1')
    result()

    # ---- FSINH, FCOSH, FTANH ------------------------------------------------------
    # sinh |x| = (E + E / (E + 1)) / 2, E = e^|x| - 1; from |x| = 64, e^|x| / 2.
    L('op_fsinh')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', COND='A_INF', TGT='finish')
    st('X2')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=17, TGT='sinh_1')
    u(SEQ='BR', COND='A_SIGN', TGT='sinh_hn')
    u(SEQ='JUMP', TGT='t_hugep')
    L('sinh_hn')
    ldc('ONE')
    u(SGN='NEG', EOP='LDI', IMM=0x7FFF)
    u(EOP='ADDI', IMM=0x7FFF, SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('sinh_1')
    u(SGN='ABS')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=6, TGT='sinh_2')
    call('exp_e')
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    result('X2')
    L('sinh_2')
    call('expm1')
    st('S')
    ldcb('ONE')
    add()
    st('T1')
    ld('S')
    ldb('T1')
    div()
    ldb('S')
    add()
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    result('X2')

    # cosh |x| = (E + 1 / E) / 2, E = e^|x|; from |x| = 64, E / 2.
    L('op_fcosh')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='t_one')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='cosh_1')
    u(SGN='ABS', SEQ='JUMP', TGT='finish')
    L('cosh_1')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=17, TGT='t_hugep')
    u(SGN='ABS')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=6, TGT='cosh_2')
    call('exp_e')
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    result()
    L('cosh_2')
    call('exp_e')
    st('S')
    ldc('ONE')
    ldb('S')
    div()
    ldb('S')
    add()
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    result()

    # tanh |x| = E / (E + 2), E = e^(2|x|) - 1; from |x| = 64, 1.
    L('op_ftanh')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='tanh_1')
    u(SEQ='BR', COND='A_SIGN', TGT='t_mone')
    u(SEQ='JUMP', TGT='t_one')
    L('tanh_1')
    st('X2')
    u(SGN='ABS')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=6, TGT='tanh_2')
    ldc('ONE')
    result('X2')
    L('tanh_2')
    u(EOP='ADDI', IMM=1)
    call('expm1')
    st('S')
    ldcb('TWO')
    add()
    st('T1')
    ld('S')
    ldb('T1')
    div()
    result('X2')

    # =========================================================================
    # Logarithms (Tang): x = 2^k m, F = 1 + j/64 near m, u = (m - F) / F,
    # ln x = k ln 2 + ln F + ln(1 + u). lncore leaves k in T.K and
    # ln F + ln(1 + u) in T.FR (for log2 and log10 to use as they need).
    # =========================================================================
    L('lncore')
    st('X')
    u(MOP='EXPF')
    u(MOP='NORM')
    st('K')
    ld('X')
    u(EOP='LDI', IMM=0)
    st('M')                                             # m in [1, 2)
    ldcb('ONE')
    sub()
    u(EOP='ADDI', IMM=6)
    call('rintn')                                       # j = round(64 (m - 1))
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=6, TGT='ln_1')
    # j = 64: m / 2 and k + 1 instead, so that no ln 2 cancels near 1.
    ld('M')
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    st('M')
    ld('K')
    ldcb('ONE')
    add()
    st('K')
    u(ASRC='ZERO')
    L('ln_1')
    st('J')
    u(EOP='ADDI', IMM=-6 & 0xFFFF)
    ldcb('ONE')
    add()
    st('F')                                             # F = 1 + j/64
    ld('M')
    ldb('F')
    sub()
    ldb('F')
    div()
    st('U')                                             # u
    horner([f'LNC{k}' for k in range(1, 11)], 'U')
    ldb('U')
    mul()                                               # ln(1 + u)
    st('FR')
    ld('J')
    toexp()
    u(RF='CROM', RFA='EXP', IMM=C('LNF0'))
    u(BSRC='RFQ')
    ld('FR')
    add()
    st('FR')                                            # ln F + ln(1 + u)
    u(SEQ='RET')

    # ln x itself: k ln2 (two parts, the first product exact) + FR.
    L('lnfull')
    call('lncore')
    ld('K')
    ldcb('LN2K_2')
    mul()
    ldb('FR')
    add()
    st('FR')
    ld('K')
    ldcb('LN2K_1')
    mul()
    ldb('FR')
    add()
    u(SEQ='RET')

    # ln(1 + z), z > -1: below 1/16 by 2 atanh(z / (2 + z)), else ln(1 + z).
    L('lnp1')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=-4 & 0xFFFF, TGT='lnp1_b')
    st('X')
    ldcb('TWO')
    add()
    st('T1')
    ld('X')
    ldb('T1')
    div()
    st('S')                                             # s
    ldb('S')
    mul()
    st('U2')                                            # s^2
    horner([f'ODD{k}' for k in range(8)], 'U2')
    ldb('S')
    mul()
    u(EOP='ADDI', IMM=1, SEQ='RET')
    L('lnp1_b')
    ldcb('ONE')
    add()
    u(SEQ='JUMP', TGT='lnfull')

    def log_prologue(op, pos_inf_ok=True):
        L(op)
        u(SEQ='BR', COND='A_NAN', TGT='nan1')
        u(SEQ='BR', COND='A_ZERO', TGT='t_dzminf')
        u(SEQ='BR', COND='A_SIGN', TGT='operr')
        u(SEQ='BR', COND='A_INF', TGT='finish')

    # DZ: minus infinity (FPU 6.1.6), or the source's infinity.
    L('t_dzminf')
    u(EXCSET=DZ, SEQ='CALL', TGT='etemp_src')
    u(MOP='INFA', SGN='ABS')
    u(SGN='NEG', SEQ='JUMP', TGT='finish')
    L('t_dzsinf')
    st('X2')
    u(EXCSET=DZ, SEQ='CALL', TGT='etemp_src')
    ld('X2')
    u(MOP='INFA', SEQ='JUMP', TGT='finish')

    log_prologue('op_flogn')
    call('lnfull')
    L('log_out')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='log_out2')
    u(SGN='ABS', SEQ='JUMP', TGT='finish', comment='ln 1 = +0, exactly')
    L('log_out2')
    result()

    # log2 x = k + FR log2 e; a power of two is exactly k.
    log_prologue('op_flog2')
    u(SEQ='BR', NEG=1, COND='A_POW2', TGT='log2_1')
    u(MOP='EXPF')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(MOP='NORM')
    u(SEQ='CALL', TGT='post', comment='an integer: exact')
    u(SEQ='JUMP', TGT='finish')
    L('log2_1')
    call('lncore')
    ld('FR')
    ldcb('LOG2E')
    mul()
    ldb('K')
    add()
    u(SEQ='JUMP', TGT='log_out')

    # log10 x = k log10 2 (two parts) + FR log10 e.
    log_prologue('op_flog10')
    call('lncore')
    ld('FR')
    ldcb('LOG10E')
    mul()
    st('FR')
    ld('K')
    ldcb('LOG10_2K_2')
    mul()
    ldb('FR')
    add()
    st('FR')
    ld('K')
    ldcb('LOG10_2K_1')
    mul()
    ldb('FR')
    add()
    # log10 of 10^n, 0 <= n <= 27, is exactly n (the model's exact case).
    u(SEQ='BR', COND='A_SIGN', TGT='log_out')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=5, TGT='log_out')
    st('S')
    call('rintn')
    st('T1')
    toexp()
    u(RF='CROM', RFA='ELO', IMM=crom.CR_P10)
    u(BSRC='RFQ')
    ld('X')
    u(SEQ='BR', NEG=1, COND='B_EQ_A', TGT='log10_n')
    ld('T1')
    u(FLAG='CLR_STK', SEQ='BR', COND='A_ZERO', TGT='log_out')
    u(SEQ='CALL', TGT='post')
    u(SEQ='JUMP', TGT='finish')
    L('log10_n')
    ld('S')
    u(SEQ='JUMP', TGT='log_out')

    # FLOGNP1 (its table, and the DZ of FPU 6.1.6 at -1).
    L('op_flognp1')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='lp1_1')
    u(SEQ='BR', COND='A_SIGN', TGT='operr')
    u(SEQ='JUMP', TGT='finish')
    L('lp1_1')
    u(SEQ='BR', NEG=1, COND='A_SIGN', TGT='lp1_2')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=1, TGT='operr')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=0, TGT='lp1_2')
    u(SEQ='BR', COND='A_POW2', TGT='t_dzminf', comment='-1')
    u(SEQ='JUMP', TGT='operr')
    L('lp1_2')
    call('lnp1')
    u(SEQ='JUMP', TGT='log_out')

    # FATANH x = ln(1 + 2x / (1 - x)) / 2.
    L('op_fatanh')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    u(SEQ='BR', COND='A_INF', TGT='operr')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=1, TGT='operr')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=0, TGT='atanh_1')
    u(SEQ='BR', COND='A_POW2', TGT='t_dzsinf', comment='+-1')
    u(SEQ='JUMP', TGT='operr')
    L('atanh_1')
    st('X2')
    ldc('ONE')
    ldb('X2')
    sub()
    st('T1')                                            # 1 - x
    ld('X2')
    u(EOP='ADDI', IMM=1)
    ldb('T1')
    div()
    call('lnp1')
    u(EOP='ADDI', IMM=-1 & 0xFFFF)
    u(SEQ='JUMP', TGT='log_out')

    # =========================================================================
    # Arctangents. atan01 for 0 <= y <= 1: c = round(16 y) / 16,
    # atan y = atan c + atan((y - c) / (1 + y c)); atanall for any y >= 0.
    # =========================================================================
    L('atanall')
    u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=0, TGT='atan01')
    st('A1')
    ldc('ONE')
    ldb('A1')
    div()
    call('atan01')
    u(SGN='NEG')
    ldcb('PI_2')
    add()
    u(SEQ='RET')
    L('atan01')
    st('A2')
    u(EOP='ADDI', IMM=4)
    call('rintn')
    st('J')
    u(EOP='ADDI', IMM=-4 & 0xFFFF)
    st('F')                                              # c
    ld('A2')
    ldb('F')
    sub()
    st('S')                                              # y - c
    ld('A2')
    ldb('F')
    mul()
    ldcb('ONE')
    add()
    st('T1')                                             # 1 + y c
    ld('S')
    ldb('T1')
    div()
    st('U')
    ldb('U')
    mul()
    st('U2')
    horner([f'ATANC{k}' for k in range(9)], 'U2')
    ldb('U')
    mul()
    st('S')
    ld('J')
    toexp()
    u(RF='CROM', RFA='EXP', IMM=C('ATANT0'))
    u(BSRC='RFQ')
    ld('S')
    add()
    u(SEQ='RET')

    L('op_fatan')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    st('X2')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='atan_1')
    ldc('PI_2')
    result('X2')
    L('atan_1')
    u(SGN='ABS')
    call('atanall')
    result('X2')

    # |x| above one, or one: the FASIN/FACOS cases.
    def mag_cases(gt1, eq1, rest):
        u(SEQ='BR', COND='A_INF', TGT=gt1)
        u(SEQ='BR', COND='AE_GE_IMM', IMM=1, TGT=gt1)
        u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=0, TGT=rest)
        u(SEQ='BR', NEG=1, COND='A_POW2', TGT=gt1)
        u(SEQ='JUMP', TGT=eq1)

    # asin x = atan(x / sqrt((1 - x)(1 + x))).
    L('op_fasin')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    st('X2')
    mag_cases('operr', 'asin_one', 'asin_1')
    L('asin_one')
    ldc('PI_2')
    result('X2')
    L('asin_1')
    u(SGN='ABS')
    st('A1')
    ldc('ONE')
    ldb('A1')
    sub()
    st('T1')
    ldc('ONE')
    ldb('A1')
    add()
    ldb('T1')
    mul()
    call('sqrtz')
    st('T1')
    ld('A1')
    ldb('T1')
    div()
    call('atanall')
    result('X2')

    # acos x = 2 atan(sqrt((1 - x) / (1 + x))).
    L('op_facos')
    u(SEQ='BR', COND='A_NAN', TGT='nan1')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='acos_0')
    ldc('PI_2')
    result()
    L('acos_0')
    st('X2')
    mag_cases('operr', 'acos_one', 'acos_1')
    L('acos_one')
    u(SEQ='BR', COND='A_SIGN', TGT='acos_m1')
    u(ASRC='ZERO', SEQ='JUMP', TGT='finish', comment='acos 1 = +0, exactly')
    L('acos_m1')
    ldc('PI')
    result()
    L('acos_1')
    ldc('ONE')
    ldb('X2')
    sub()
    st('T1')
    ldc('ONE')
    ldb('X2')
    add()
    st('T2')
    ld('T1')
    ldb('T2')
    div()
    call('sqrtz')
    call('atanall')
    u(EOP='ADDI', IMM=1)
    result()

    # =========================================================================
    # Sine, cosine, tangent: reduce |x| to r in [-pi/4, pi/4] and a quadrant
    # q (T.Q's exponent), then the Taylor series of sin and cos at r.
    # =========================================================================
    L('reduce')
    u(SEQ='BR', COND='AE_GE_IMM', IMM=20, TGT='red_ph')
    # Cody and Waite: N = round(x 2/pi) < 2^20, r = x - N (P1 + P2 + P3).
    st('X')
    ldcb('C2_PI')
    mul()
    call('rintn')
    st('N')
    ldcb('PIO2_1')
    mul()
    st('R')
    ld('X')
    ldb('R')
    sub()
    st('R')
    ld('N')
    ldcb('PIO2_2')
    mul()
    st('P')
    ld('R')
    ldb('P')
    sub()
    st('R')
    ld('N')
    ldcb('PIO2_3')
    mul()
    st('P')
    ld('R')
    ldb('P')
    sub()
    st('R')
    ld('N')
    u(SEQ='JUMP', TGT='red_q')

    # Payne and Hanek: x = M 2^E, M a 64-bit integer. Of x 2/pi mod 4 only
    # four 64-bit chunks of 2/pi matter, from chunk idx = (E + 62) >> 6;
    # t = ((E + 62) & 63) - 62 places them. Each product M G is exact,
    # its high half from C (MULHI), its low from B (MULSTEP shifts it there).
    L('red_ph')
    st('X')
    u(EOP='SA_IMM', IMM=8)
    u(MOP='SHRA')
    st('MI')                                             # M, in mantissa bits 63-0
    ld('X')
    u(EOP='ADDI', IMM=-1 & 0xFFFF)                       # E + 62
    st('TT')
    for _ in range(6):
        u(EOP='HALF')
    st('IDX')
    ld('TT')
    u(EOP='LO6')
    u(EOP='ADDI', IMM=-62 & 0xFFFF)
    st('TT')                                             # t
    for k in range(4):
        ld('IDX')
        if k:
            u(EOP='ADDI', IMM=k)
        u(RF='CROM', RFA='EXP', IMM=crom.CR_2OPI)
        u(BSRC='RFQ')
        ld('MI')
        u(MOP='CLRQ')
        for _ in range(5):
            u(MOP='MULSTEP')
        u(MOP='MULHI')
        st(f'H{k}')
        u(ASRC='B')
        st(f'L{k}')
    # V_i = (L_i + H_(i+1)) 2^(t - 64 i), each sum exact.
    for i in range(3):
        ld(f'L{i}')
        u(EOP='LDI', IMM=63)
        u(MOP='NORM')
        st('T1')
        ld(f'H{i + 1}')
        u(EOP='LDI', IMM=63)
        u(MOP='NORM')
        ldb('T1')
        add()
        ldb('TT')
        u(EOP='ADDB')
        if i:
            u(EOP='ADDI', IMM=(-64 * i) & 0xFFFF)
        st(f'V{i}')
    # V0 = K + F0, K an integer, F0 in [0, 1): K mod 4, then the quadrant
    # n = round(K mod 4 + F0 + V1), and r = ((K mod 4 - n) + F0 + V1 + V2) pi/2,
    # the small terms added last so that their error is relative to r.
    ld('V0')
    call('rintm')
    st('KM')
    u(SGN='NEG')
    ldb('V0')
    add()
    st('S')                                              # F0
    ld('KM')
    u(EOP='ADDI', IMM=-2 & 0xFFFF)
    call('rintm')
    u(EOP='ADDI', IMM=2)
    u(SGN='NEG')
    ldb('KM')
    add()
    st('KM')                                             # K mod 4
    ldb('S')
    add()
    ldb('V1')
    add()
    call('rintn')
    st('N')
    u(SGN='NEG')
    ldb('KM')
    add()
    ldb('S')
    add()
    ldb('V1')
    add()
    ldb('V2')
    add()
    ldcb('PI_2')
    mul()
    st('R')
    ld('N')
    # q = n mod 4, into T.Q's exponent.
    L('red_q')
    st('N')
    u(EOP='ADDI', IMM=-2 & 0xFFFF)
    call('rintm')
    u(EOP='ADDI', IMM=2)
    u(SGN='NEG')
    ldb('N')
    add()
    toexp()
    st('Q')
    u(SEQ='RET')

    L('sincore')                                         # A = sin(T.R)
    ld('R')
    ldb('R')
    mul()
    st('U2')
    horner([f'SINC{k}' for k in range(10)], 'U2')
    ldb('R')
    u(SEQ='JUMP', TGT='mulz')
    L('coscore')                                         # A = cos(T.R)
    ld('R')
    ldb('R')
    mul()
    st('U2')
    horner([f'COSC{k}' for k in range(11)], 'U2')
    u(SEQ='RET')

    # The quadrant's choice: sin x = (sin r, cos r, -sin r, -cos r)[q],
    # cos x = (cos r, -sin r, -cos r, sin r)[q]; tan x = sin x / cos x.
    # sinq/cosq leave the value for |x| with its sign (sin, tan: times x's).
    for name, odd_first in (('sinq', False), ('cosq', True)):
        L(name)
        ld('Q')
        if not odd_first:
            u(SEQ='BR', COND='AE_ODD', TGT=f'{name}_c')
            call('sincore')
            u(SEQ='JUMP', TGT=f'{name}_s')
            L(f'{name}_c')
            call('coscore')
        else:
            u(SEQ='BR', COND='AE_ODD', TGT=f'{name}_c')
            call('coscore')
            u(SEQ='JUMP', TGT=f'{name}_s')
            L(f'{name}_c')
            call('sincore')
        L(f'{name}_s')
        st('S')
        ld('Q')
        if odd_first:
            # cos: negative for q = 1, 2
            u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=1, TGT=f'{name}_p')
            u(SEQ='BR', COND='AE_GE_IMM', IMM=3, TGT=f'{name}_p')
        else:
            u(SEQ='BR', NEG=1, COND='AE_GE_IMM', IMM=2, TGT=f'{name}_p')
        ld('S')
        u(SGN='NEG', SEQ='RET')
        L(f'{name}_p')
        ld('S')
        u(SEQ='RET')

    def trig_prologue(op):
        L(op)
        u(SEQ='BR', COND='A_NAN', TGT='nan1')
        u(SEQ='BR', COND='A_INF', TGT='operr')

    trig_prologue('op_fsin')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    st('X2')
    u(SGN='ABS')
    call('reduce')
    call('sinq')
    result('X2')

    trig_prologue('op_fcos')
    u(SEQ='BR', COND='A_ZERO', TGT='t_one')
    u(SGN='ABS')
    call('reduce')
    call('cosq')
    result()

    trig_prologue('op_ftan')
    u(SEQ='BR', COND='A_ZERO', TGT='finish')
    st('X2')
    u(SGN='ABS')
    call('reduce')
    call('cosq')
    st('CC')
    call('sinq')
    ldb('CC')
    div()
    result('X2')

    # FSINCOS: the cosine to FPc (command word bits 2-0) first, then the
    # sine to FPn, so that one register for both ends with the sine (FPU 4.6).
    L('op_fsincos')
    u(SEQ='BR', NEG=1, COND='A_NAN', TGT='sc_1')
    u(SEQ='BR', NEG=1, COND='A_SNAN', TGT='sc_q')
    u(EXCSET=0x40, SEQ='CALL', TGT='etemp_src')
    L('sc_q')
    u(MOP='QUIET')
    u(SEQ='BR', COND='SUPPRESS', TGT='fin_x')
    u(RF='WRITE', RFA='FPC', SEQ='JUMP', TGT='finish')
    L('sc_1')
    u(SEQ='BR', NEG=1, COND='A_INF', TGT='sc_2')
    u(EXCSET=OPERR, SEQ='CALL', TGT='etemp_src')
    u(ASRC='NAN')
    u(SEQ='BR', COND='SUPPRESS', TGT='fin_x')
    u(RF='WRITE', RFA='FPC', SEQ='JUMP', TGT='finish')
    L('sc_2')
    u(SEQ='BR', NEG=1, COND='A_ZERO', TGT='sc_3')
    st('X2')
    ldc('ONE')
    u(RF='WRITE', RFA='FPC')
    ld('X2')
    u(SEQ='JUMP', TGT='finish')
    L('sc_3')
    st('X2')
    u(SGN='ABS')
    call('reduce')
    call('cosq')
    u(FLAG='SET_STK', SEQ='CALL', TGT='post')
    u(RF='WRITE', RFA='FPC')
    call('sinq')
    result('X2')

    # sqrtz: A = sqrt(A), A >= 0, as FSQRT's core.
    L('sqrtz')
    u(SEQ='BR', COND='A_ZERO', TGT='mulz_r')
    u(BSRC='A', SEQ='BR', COND='AE_ODD', TGT='sqz_odd')
    u(EOP='SA_IMM', IMM=1)
    u(MOP='SHRB', SEQ='JUMP', TGT='sqz_go')
    L('sqz_odd')
    u(EOP='ADDI', IMM=0xFFFF)
    L('sqz_go')
    u(EOP='HALF', MOP='CLRQ', CTR='LOAD', IMM=71)
    u(MOP='CLRAM')
    L('sqz_loop')
    u(SEQ='LOOP', TGT='sqz_loop', MOP='SQSTEP')
    u(MOP='SQFIN', SEQ='RET')
