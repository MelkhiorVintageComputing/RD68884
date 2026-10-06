// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 -- RD68021 (../RD68021, unmodified) with RD68884 as its
// coprocessor, as one Verilator model for TME's tme/ic/rd68021 element
// (doc/system.md). Adapted from RD68021's sim/tme/rd68021_tme_mh882.sv: the
// core's own ports, so that the element drives it as it drives rd68021_top,
// plus the FPU's clock and the flag that an interface-register cycle is on
// the bus, which the element leaves to the FPU.
//
// The board side of the FPU is sim/tb/sys_tb.sv's: a 32-bit port (SIZE and
// A0 strapped), an early chip select for CpID 1 decoded from FC = 7, A19-A16
// = 2, A15-A13 = 1 (FPU 10.3), DSACK with pull-ups. RESET reaches the FPU
// from the board and from the core's RESET instruction.
//
// BUS_SYNC = 1 is the same-clock BIU (doc/bus-timing.md): the FPU then runs on
// the core's clock, and clk_fpu is not used. MODEL = 68882 is RD68885
// (doc/rd68885.md).

module rd68021_tme_rd68884 #(
    parameter int BUS_SYNC      = 0,
    parameter int BUS_SYNC_WAIT = 0,
    parameter int MODEL         = 68881
) (
    input  logic        clk,
    input  logic        clk_fpu,
    input  logic        rst_n,
    output logic  [2:0] fc_o,
    output logic        fc_oe,
    output logic [31:0] a_o,
    output logic        a_oe,
    input  logic [31:0] d_i,
    output logic [31:0] d_o,
    output logic        d_oe,
    output logic  [1:0] siz_o,
    output logic        siz_oe,
    output logic        ecs_n_o,
    output logic        ocs_n_o,
    output logic        rw_o,
    output logic        rw_oe,
    output logic        rmc_n_o,
    output logic        rmc_oe,
    output logic        as_n_o,
    output logic        as_oe,
    output logic        ds_n_o,
    output logic        ds_oe,
    output logic        dben_o,
    output logic        dben_oe,
    input  logic  [1:0] dsack_n_i,
    input  logic  [2:0] ipl_n_i,
    output logic        ipend_n_o,
    input  logic        avec_n_i,
    input  logic        br_n_i,
    output logic        bg_n_o,
    input  logic        bgack_n_i,
    input  logic        berr_n_i,
    input  logic        reset_n_i,
    output logic        reset_n_o,
    output logic        reset_n_oe,
    input  logic        halt_n_i,
    output logic        halt_n_o,
    output logic        halt_n_oe,
    input  logic        cdis_n_i,
    output logic        fpu_cs
);

  logic        cs_n;
  logic [31:0] fpu_d_o;
  logic  [3:0] fpu_d_oe;
  logic  [1:0] fpu_dsack_n;
  logic        fpu_dsack_oe;
  logic [31:0] core_d;

  assign cs_n   = !(fc_o == 3'd7 && a_o[19:16] == 4'h2 && a_o[15:13] == 3'd1);
  assign fpu_cs = !cs_n;
  // The core reads the FPU's lanes when it drives them, the board otherwise.
  always_comb begin
    for (int l = 0; l < 4; l++)
      core_d[8*l +: 8] = fpu_d_oe[l] ? fpu_d_o[8*l +: 8] : d_i[8*l +: 8];
  end

  rd68021_top #(.ICACHE_ENTRIES (64), .COPROCESSOR (1'b1)) core (
      .clk (clk), .rst_n (rst_n),
      .fc_o (fc_o), .fc_oe (fc_oe), .a_o (a_o), .a_oe (a_oe),
      .d_i (core_d), .d_o (d_o), .d_oe (d_oe),
      .siz_o (siz_o), .siz_oe (siz_oe),
      .ecs_n_o (ecs_n_o), .ocs_n_o (ocs_n_o),
      .rw_o (rw_o), .rw_oe (rw_oe), .rmc_n_o (rmc_n_o), .rmc_oe (rmc_oe),
      .as_n_o (as_n_o), .as_oe (as_oe), .ds_n_o (ds_n_o), .ds_oe (ds_oe),
      .dben_o (dben_o), .dben_oe (dben_oe),
      .dsack_n_i (dsack_n_i & (fpu_dsack_oe ? fpu_dsack_n : 2'b11)),
      .ipl_n_i (ipl_n_i), .ipend_n_o (ipend_n_o), .avec_n_i (avec_n_i),
      .br_n_i (br_n_i), .bg_n_o (bg_n_o), .bgack_n_i (bgack_n_i),
      .berr_n_i (berr_n_i),
      .reset_n_i (reset_n_i), .reset_n_o (reset_n_o), .reset_n_oe (reset_n_oe),
      .halt_n_i (halt_n_i), .halt_n_o (halt_n_o), .halt_n_oe (halt_n_oe),
      .cdis_n_i (cdis_n_i));

  logic fpu_clk;
  assign fpu_clk = (BUS_SYNC != 0) ? clk : clk_fpu;

  rd68884_top #(.BUS_SYNC (BUS_SYNC), .BUS_SYNC_WAIT (BUS_SYNC_WAIT), .MODEL (MODEL)) fpu (
      .clk (fpu_clk), .rst_n (rst_n),
      .reset_n_i (reset_n_i && !(reset_n_oe && !reset_n_o)),
      .cs_n_i (cs_n), .as_n_i (as_n_o), .ds_n_i (ds_n_o), .rw_i (rw_o),
      .size_n_i (1'b1), .a_i ({a_o[4:1], 1'b1}),
      .d_i (d_o), .d_o (fpu_d_o), .d_oe (fpu_d_oe),
      .dsack_n_o (fpu_dsack_n), .dsack_oe (fpu_dsack_oe));

endmodule
