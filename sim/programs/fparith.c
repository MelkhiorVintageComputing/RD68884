/*
 * SPDX-License-Identifier: CERN-OHL-S-2.0
 * Copyright 2026 Romain Dolbeau
 * Source location: https://github.com/MelkhiorVintageComputing/RD68884
 */

/*
 * RD68884 -- compiled floating-point code, on RD68021 with RD68884 as its
 * coprocessor (sim/tb/sys_tb.sv). Copied from RD68021's sim/programs/fparith.c
 * and adapted: FPARITH_NO_TRANS can leave out the transcendental functions
 * (RD68884 had none before M7).
 *
 * The same source is the oracle: built for the host, it prints the buffer the
 * FPU should produce. It is built twice, and is C11 both times, so that every
 * assignment and cast rounds to its type, whatever the registers hold
 * (FLT_EVAL_METHOD 2, on the MC68881 and the x87 alike):
 *
 *   fparith      extended rounding precision, against the host's x87, whose
 *                significand is the same 64 bits. Every operation rounds once
 *                to 64 bits and again on assignment, on both sides.
 *   fparith-dbl  FPU_PREC_DOUBLE: rounding precision double (MC68881 UM
 *                2.2.2), against the host's SSE doubles. Every operation
 *                rounds once, to 53 bits, as an IEEE double does.
 *
 * Neither leaves the range where the FPU's wider exponent could make a
 * difference: no overflow, no underflow, no denormals. GCC's instruction mix
 * decides what reaches the interface: the moves, the formats, the addressing
 * modes, the compares.
 *
 * The first part is exact and compared bit for bit. The second is the
 * transcendental functions, which are only as good as the FPU's algorithms
 * and the host's libm; they are reported in ulps, not compared.
 *
 * The results block is the one sim/tb/core_mh882_tb.sv documents; RES+$18
 * says how many of the buffer's long words are the exact part.
 */

#ifdef __m68k__
#define RES ((volatile unsigned long *)0xC000)
#define BUF ((volatile unsigned long *)0xC100)
#else
#include <math.h>
#include <stdio.h>
static unsigned long res_[16];
static unsigned long buf_[4096];
#define RES res_
#define BUF buf_
#endif

static unsigned int nbuf;

static void put(double d)
{
    union { double d; unsigned long long u; } x;
    x.d = d;
    BUF[nbuf++] = (unsigned long)(x.u >> 32) & 0xFFFFFFFFul;
    BUF[nbuf++] = (unsigned long)x.u & 0xFFFFFFFFul;
}

static void puti(long v)
{
    BUF[nbuf++] = (unsigned long)v & 0xFFFFFFFFul;
}

static unsigned long seed = 12345;

static long rnd(void)
{
    seed = (seed * 1103515245ul + 12345ul) & 0x7FFFFFFFul;
    return (long)(seed >> 8);
}

/* Something in [-1000, 1000), never zero, with a few bits of fraction. */
static double val(void)
{
    double d = (double)(rnd() % 2000000 - 1000000) / 1000.0;
    return d == 0.0 ? 0.5 : d;
}

#ifdef FPARITH_NO_TRANS
#elif defined(__m68k__)
#define TRANS(name, insn)                                               \
    static double name(double x)                                        \
    {                                                                   \
        double r;                                                       \
        __asm__ volatile(insn " %1,%0" : "=f"(r) : "f"(x));             \
        return r;                                                       \
    }
TRANS(fp_sin, "fsin.x")
TRANS(fp_cos, "fcos.x")
TRANS(fp_tan, "ftan.x")
TRANS(fp_atan, "fatan.x")
TRANS(fp_etox, "fetox.x")
TRANS(fp_logn, "flogn.x")
TRANS(fp_log10, "flog10.x")
TRANS(fp_twotox, "ftwotox.x")
#else
#define fp_sin sin
#define fp_cos cos
#define fp_tan tan
#define fp_atan atan
#define fp_etox exp
#define fp_logn log
#define fp_log10 log10
#define fp_twotox exp2
#endif

int main(void)
{
    int i, j;
    double a, b, s, p, x;

#if defined(__m68k__) && defined(FPU_PREC_DOUBLE)
    /* Rounding precision double, to nearest (MC68881 UM 2.2.2). */
    __asm__ volatile("fmove.l %0,%%fpcr" : : "d"(0x80));
#endif
    RES[4] = 0;
    RES[1] = 0;
    RES[2] = 0;
    nbuf = 0;

    /* The four operations, square root, conversions and compares. */
    for (i = 0; i < 24; i++) {
        a = val();
        b = val();
        put(a + b);
        put(a - b);
        put(a * b);
        put(a / b);
        put(__builtin_sqrt(a < 0 ? -a : a));
        puti((long)(a * b));
        puti((a < b) | (a == b) << 1 | (a >= b) << 2);
        put((double)(float)(a / b));
        put((double)rnd() * 0.0009765625);
    }

    /* Chained work: Horner's rule, a Newton iteration, a series. */
    for (i = 0; i < 6; i++) {
        x = val() / 1000.0;
        p = 0.0;
        for (j = 7; j >= 0; j--)
            p = p * x + (double)(j + 1) / 3.0;
        put(p);
        a = val();
        b = a > 0 ? a : -a;
        s = b / 2.0;
        for (j = 0; j < 6; j++)
            s = 0.5 * (s + b / s);
        put(s);
    }
    s = 0.0;
    for (j = 1; j <= 40; j++)
        s += 1.0 / (double)j;
    put(s);

    RES[6] = nbuf;

#ifndef FPARITH_NO_TRANS
    /* Transcendentals, reported only. */
    for (i = 0; i < 4; i++) {
        x = val() / 400.0;
        put(fp_sin(x));
        put(fp_cos(x));
        put(fp_tan(x));
        put(fp_atan(x));
        put(fp_etox(x));
        put(fp_logn(x < 0 ? -x : x));
        put(fp_log10(x < 0 ? -x : x));
        put(fp_twotox(x));
    }
#endif

    RES[5] = nbuf;
#ifdef __m68k__
    RES[0] = 0x444F4E45;
#else
    for (i = 0; i < (int)nbuf; i++)
        printf("%08lx\n", BUF[i]);
    fprintf(stderr, "exact %lu of %u\n", RES[6], nbuf);
#endif
    return 0;
}
