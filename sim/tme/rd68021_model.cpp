// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 -- copied from RD68021's sim/tme/rd68021_model.cpp and adapted: the
// model is sim/tme/rd68021_tme_rd68884.sv, RD68021 with RD68884 as its
// coprocessor, behind the C interface RD68021's TME element (sim/tme/rd68021.c,
// used as it stands) expects. The element's `fpu mh882` mode is its "the RTL
// in the model answers the interface registers" mode, and this model is built
// for it: rdm_mh882() says so, and the element's reports say MH882 for it.
//
// Original notes, for the RD68021 element:
//
// RD68021 -- the Verilator model of the core, behind a C interface, for the TME
// element in sim/tme/rd68021.c.
//
// TME is C and runs its elements as cooperative threads that leave by longjmp.
// Nothing may longjmp across a C++ frame, so everything here is a short call
// that returns: a half clock, or a pin read or written. The bus protocol --
// which edge answers what -- is the element's business, not this file's.
//
// Built with RD_MH882 the model is sim/tme/rd68021_tme_mh882.sv, the core with
// the mh882 MC68882 beside it (doc/mh882.md), under the same class name. The
// FPU runs from a clock of its own: RD68021_MH882_TOGGLES edges of it in every
// half clock of the core, 6 by default -- with the core at 16.67 MHz, an FPU
// CLK of 25 MHz, which mh882 runs at four times.
//
// Clocking mh882 makes the model about twenty times slower, and a SunOS run
// is billions of clocks. So by default its clock runs only while an interface
// register cycle is on the bus and for RD68021_MH882_TAIL half clocks of the
// core after it (64 by default): between dialogues the FPU is stopped. The
// FPU's state changes only while it computes or answers, and a computation the
// core is waiting for is advanced by the response-CIR reads it polls with --
// so this is an FPU that is slower between conversations, which the protocol
// allows for, not a different one. RD68021_MH882_TAIL=0 clocks it always.

#include <stdlib.h>

#include "Vrd68021_top.h"
#include "verilated.h"
#include "Vrd68021_top___024root.h"
#include "rd68021_model.h"

// RD68884: always the core with the FPU beside it.
#define RD_MH882 1
#define H(x) rd68021_tme_rd68884__DOT__core__DOT__##x

struct rdm {
  VerilatedContext *ctx;
  Vrd68021_top     *top;
  unsigned long long clocks;
  int fpu_toggles;
  int fpu_tail, fpu_left;
};

// Built with RD_FPU_SAMECLK, the FPU is the same-clock one and runs on the
// core's own edges (doc/bus-timing.md): no clock of its own to advance.
static void fpu_run(struct rdm *m) {
#if defined(RD_MH882) && !defined(RD_FPU_SAMECLK)
  if (m->fpu_tail) {
    if (m->top->fpu_cs || m->top->reset_n_oe || !m->top->rst_n) m->fpu_left = m->fpu_tail;
    else if (m->fpu_left == 0) return;
    else m->fpu_left--;
  }
  for (int k = 0; k < m->fpu_toggles; k++) {
    m->top->clk_fpu = !m->top->clk_fpu;
    m->top->eval();
  }
#else
  (void) m;
#endif
}

extern "C" {

struct rdm *rdm_new(void) {
  struct rdm *m = new rdm;
  m->ctx = new VerilatedContext;
  m->top = new Vrd68021_top{m->ctx};
  m->clocks = 0;
  // RD68884's core clock is 20 ns against the CPU's 60: three edges of it in
  // each half clock of the CPU (doc/system.md).
  m->fpu_toggles = getenv("RD68021_MH882_TOGGLES") ? atoi(getenv("RD68021_MH882_TOGGLES")) : 3;
  m->fpu_tail = getenv("RD68021_MH882_TAIL") ? atoi(getenv("RD68021_MH882_TAIL")) : 64;
  m->fpu_left = m->fpu_tail;
  Vrd68021_top *t = m->top;
#ifdef RD_MH882
  t->clk_fpu = 0;
#endif
  // Everything idle: no bus master wants the bus, no interrupt, no halt, no
  // reset from outside, the cache allowed.
  t->clk = 0;
  t->rst_n = 0;
  t->d_i = 0;
  t->dsack_n_i = 3;
  t->ipl_n_i = 7;
  t->avec_n_i = 1;
  t->br_n_i = 1;
  t->bgack_n_i = 1;
  t->berr_n_i = 1;
  t->reset_n_i = 1;
  t->halt_n_i = 1;
  t->cdis_n_i = 1;
  t->eval();
  return m;
}

void rdm_rising(struct rdm *m)  { m->top->clk = 1; m->top->eval(); m->clocks++; fpu_run(m); }
void rdm_falling(struct rdm *m) { m->top->clk = 0; m->top->eval(); fpu_run(m); }
int rdm_mh882(const struct rdm *m) {
#ifdef RD_MH882
  (void) m;
  return 1;
#else
  (void) m;
  return 0;
#endif
}
unsigned long long rdm_clocks(const struct rdm *m) { return m->clocks; }

void rdm_set_rst_n(struct rdm *m, int v)       { m->top->rst_n = v; m->top->eval(); }
void rdm_set_d(struct rdm *m, unsigned v)      { m->top->d_i = v; }
void rdm_set_dsack_n(struct rdm *m, unsigned v){ m->top->dsack_n_i = v; }
void rdm_set_berr_n(struct rdm *m, int v)      { m->top->berr_n_i = v; }
void rdm_set_avec_n(struct rdm *m, int v)      { m->top->avec_n_i = v; }
void rdm_set_ipl_n(struct rdm *m, unsigned v)  { m->top->ipl_n_i = v; }
void rdm_set_halt_n(struct rdm *m, int v)      { m->top->halt_n_i = v; }

int      rdm_as_n(const struct rdm *m)   { return m->top->as_n_o; }
int      rdm_ds_n(const struct rdm *m)   { return m->top->ds_n_o; }
int      rdm_rw(const struct rdm *m)     { return m->top->rw_o; }
int      rdm_rmc_n(const struct rdm *m)  { return m->top->rmc_n_o; }
unsigned rdm_fc(const struct rdm *m)     { return m->top->fc_o; }
unsigned rdm_addr(const struct rdm *m)   { return m->top->a_o; }
unsigned rdm_siz(const struct rdm *m)    { return m->top->siz_o; }
unsigned rdm_dout(const struct rdm *m)   { return m->top->d_o; }
int      rdm_reset_out(const struct rdm *m) { return m->top->reset_n_oe; }
int      rdm_halt_out(const struct rdm *m)  { return m->top->halt_n_oe; }
unsigned rdm_d0(const struct rdm *m) {
  return m->top->rootp->H(u_seq__DOT__dreg)[0];
}
unsigned rdm_sr(const struct rdm *m) {
  return m->top->rootp->H(u_seq__DOT__sr_q);
}
unsigned rdm_d1(const struct rdm *m) {
  return m->top->rootp->H(u_seq__DOT__dreg)[1];
}
unsigned rdm_usp(const struct rdm *m) {
  return m->top->rootp->H(u_seq__DOT__usp_q);
}
unsigned rdm_vbr(const struct rdm *m) {
  return m->top->rootp->H(u_seq__DOT__vbr_q);
}
unsigned rdm_pc(const struct rdm *m) {
  return m->top->rootp->H(u_ifu__DOT__pc_d_q);
}

}
