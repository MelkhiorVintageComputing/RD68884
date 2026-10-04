// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 -- simulation stand-ins for the two Xilinx primitives the IIsiA7
// Mini's board top uses (boards/iisia7_mini/rd68884_iisia7_top.sv), so that
// sys_tb can run the whole board: not models of their timing, only of what the
// design depends on. Simulation only; never synthesised.
//
//   MMCME2_BASE  CLKOUT0 at CLKIN1_PERIOD * DIVCLK_DIVIDE * CLKOUT0_DIVIDE_F /
//                CLKFBOUT_MULT_F, from the first rising edge of CLKIN1; LOCKED
//                LOCK_NS later; CLKFBOUT follows CLKIN1
//   BUFG         a wire

`timescale 1ns / 1ps

module MMCME2_BASE #(
    parameter real CLKIN1_PERIOD    = 10.0,
    parameter int  DIVCLK_DIVIDE    = 1,
    parameter real CLKFBOUT_MULT_F  = 10.0,
    parameter real CLKOUT0_DIVIDE_F = 20.0
) (
    input  wire CLKIN1,
    input  wire CLKFBIN,
    output reg  CLKFBOUT,
    output reg  CLKOUT0,
    output reg  LOCKED,
    input  wire PWRDWN,
    input  wire RST
);
  localparam real PERIOD  = CLKIN1_PERIOD * DIVCLK_DIVIDE * CLKOUT0_DIVIDE_F / CLKFBOUT_MULT_F;
  localparam real LOCK_NS = 300.0;

  always @(CLKIN1) CLKFBOUT = CLKIN1;

  initial begin
    CLKOUT0 = 1'b0;
    LOCKED  = 1'b0;
    @(posedge CLKIN1);
    fork
      forever #(PERIOD / 2.0) CLKOUT0 = ~CLKOUT0;
      #(LOCK_NS) LOCKED = 1'b1;
    join
  end

  wire unused = &{1'b0, CLKFBIN, PWRDWN, RST};
endmodule

module BUFG (
    input  wire I,
    output wire O
);
  assign O = I;
endmodule
