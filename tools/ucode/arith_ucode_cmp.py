# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

"""A magnitude comparison, emitted inline wherever the microcode needs one
(FCMP, FREM's rounding, packed out, the transcendentals' exact cases)."""


def mag_cmp(p, gt, lt, eq=None):
    """Branch on |A| against |B| (finite non-zero or infinite, normalised):
    the exponents, then the mantissas through the adder (MOP CMPM). Falls
    through on equality when eq is None."""
    u = p.u
    u(SEQ='BR', COND='AE_LT_B', TGT=lt)
    u(SEQ='BR', NEG=1, COND='AE_EQ_B', TGT=gt)
    u(MOP='CMPM')
    u(SEQ='BR', COND='RX0', TGT=lt)
    u(SEQ='BR', NEG=1, COND='RX1', TGT=gt)
    if eq:
        u(SEQ='JUMP', TGT=eq)
