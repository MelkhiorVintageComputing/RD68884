// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68885 - SystemVerilog MC68882 floating-point coprocessor
//
// RD68884 built as the MC68882 (MODEL = 68882, doc/rd68885.md): the same
// pins and the same parameters as rd68884_top, whose header describes them.

module rd68885_top #(
    parameter int BUS_SYNC      = 0,
    parameter int BUS_SYNC_WAIT = 0,
    parameter int RESP_HOLD     = 20,
    parameter int DSACK_NEGATE  = 0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        reset_n_i,
    input  logic        cs_n_i,
    input  logic        as_n_i,
    input  logic        ds_n_i,
    input  logic        rw_i,
    input  logic        size_n_i,
    input  logic [4:0]  a_i,
    input  logic [31:0] d_i,
    output logic [31:0] d_o,
    output logic [3:0]  d_oe,
    output logic [1:0]  dsack_n_o,
    output logic        dsack_oe
);

  rd68884_top #(
      .BUS_SYNC     (BUS_SYNC),
      .BUS_SYNC_WAIT(BUS_SYNC_WAIT),
      .RESP_HOLD    (RESP_HOLD),
      .DSACK_NEGATE (DSACK_NEGATE),
      .MODEL        (68882)
  ) u_fpu (
      .clk      (clk),
      .rst_n    (rst_n),
      .reset_n_i(reset_n_i),
      .cs_n_i   (cs_n_i),
      .as_n_i   (as_n_i),
      .ds_n_i   (ds_n_i),
      .rw_i     (rw_i),
      .size_n_i (size_n_i),
      .a_i      (a_i),
      .d_i      (d_i),
      .d_o      (d_o),
      .d_oe     (d_oe),
      .dsack_n_o(dsack_n_o),
      .dsack_oe (dsack_oe)
  );

endmodule
