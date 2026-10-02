// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 - SystemVerilog MC68881 floating-point coprocessor
//
// Top level. The port list is the MC68881's pin list (FPU section 9, table 9-5),
// with the project's conventions (doc/pinout.md):
//
//   - three-state pins are split into _i / _o / _oe, _oe high = this chip drives;
//   - _n means active low at the pin;
//   - clk is the core clock, NOT the MC68881's CLK pin. The bus is asynchronous
//     (FPU 10.4), so the core's clock needs no relation to the main processor's;
//     it does need to be fast enough to see the shortest strobe-negated gap
//     (specification 13), which in practice means a board PLL at two to three
//     times the bus clock (doc/architecture.md, "Clocking");
//   - rst_n is the hardware initialisation input every register resets from. The
//     MC68881's RESET pin is reset_n_i, the architectural reset (FPU 9.9).
//   - SENSE is a wire to ground on the board (FPU 9.11), not a core port.
//
// M1 skeleton: the strobes are synchronised and nothing else exists. The chip
// never drives the data bus or DSACK, which to the main processor looks like an
// empty socket -- a bus error on the first CIR access, and an F-line exception.

module rd68884_top (
    input  logic        clk,
    input  logic        rst_n,

    // FPU 9.9: RESET.
    input  logic        reset_n_i,

    // FPU 9.1-9.7: the bus inputs. a_i[0] doubles as a byte address on an 8-bit
    // port; on 16- and 32-bit ports it and size_n_i are strapped (table 9-2).
    input  logic        cs_n_i,
    input  logic        as_n_i,
    input  logic        ds_n_i,
    input  logic        rw_i,
    input  logic        size_n_i,
    input  logic [4:0]  a_i,

    // FPU 9.2: D31-D0. One enable per byte lane: on 8- and 16-bit ports the
    // board ties lanes together, so only the lane in use may be driven.
    input  logic [31:0] d_i,
    output logic [31:0] d_o,
    output logic [3:0]  d_oe,

    // FPU 9.8: DSACK1/DSACK0, bit 1 = DSACK1. Both are driven together: actively
    // negated after the strobes rise, then floated (dsack_oe low).
    output logic [1:0]  dsack_n_o,
    output logic        dsack_oe
);

  // Synchronised strobes and controls, in the order {reset, cs, as, ds, rw, size}.
  logic [5:0] strobes_q;

  rd68884_sync #(
      .WIDTH    (6),
      .RESET_VAL(6'b111111)
  ) u_sync_strobes (
      .clk  (clk),
      .rst_n(rst_n),
      .d    ({reset_n_i, cs_n_i, as_n_i, ds_n_i, rw_i, size_n_i}),
      .q    (strobes_q)
  );

  assign d_o       = 32'h0000_0000;
  assign d_oe      = 4'b0000;
  assign dsack_n_o = 2'b11;
  assign dsack_oe  = 1'b0;

  logic unused_x;
  assign unused_x = &{1'b1, strobes_q, a_i, d_i};

endmodule
