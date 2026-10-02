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
//     any frequency works, a slow one only adding wait states (doc/bus-timing.md);
//     50 MHz from a board PLL is the target, set by the datapath;
//   - rst_n is the hardware initialisation input every register resets from. The
//     MC68881's RESET pin is reset_n_i, the architectural reset (FPU 9.9).
//   - SENSE is a wire to ground on the board (FPU 9.11), not a core port.
//
// M3: the bus interface unit, with its sequencer side tied off until the
// sequencer exists (M4). A command is accepted and answered with null (CA=1)
// for ever; everything else on the bus already behaves.

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

    // FPU 9.8: DSACK1/DSACK0, bit 1 = DSACK1, driven together while an access
    // is acknowledged and floated the moment it ends (doc/divergences.md).
    output logic [1:0]  dsack_n_o,
    output logic        dsack_oe
);

  logic        arch_reset;
  logic        cmd_pend, cmd_cond, opw_valid, opr_valid, save_req, restore_req;
  logic [15:0] cmd_word, restore_word;
  logic [31:0] opw_data, fpiar;
  logic        resp_read, rsel_read, save_read, abort, pv;

  rd68884_biu u_biu (
      .clk           (clk),
      .rst_n         (rst_n),
      .reset_n_i     (reset_n_i),
      .cs_n_i        (cs_n_i),
      .as_n_i        (as_n_i),
      .ds_n_i        (ds_n_i),
      .rw_i          (rw_i),
      .size_n_i      (size_n_i),
      .a_i           (a_i),
      .d_i           (d_i),
      .d_o           (d_o),
      .d_oe          (d_oe),
      .dsack_n_o     (dsack_n_o),
      .dsack_oe      (dsack_oe),
      .arch_reset_o  (arch_reset),
      .resp_we_i     (1'b0),
      .resp_i        (16'h0000),
      .resp_oneshot_i(1'b0),
      .expect_i      (3'd0),
      .cmd_pend_o    (cmd_pend),
      .cmd_cond_o    (cmd_cond),
      .cmd_word_o    (cmd_word),
      .cmd_ack_i     (1'b0),
      .opw_valid_o   (opw_valid),
      .opw_data_o    (opw_data),
      .opw_ack_i     (1'b0),
      .opr_we_i      (1'b0),
      .opr_i         (32'h0000_0000),
      .opr_valid_o   (opr_valid),
      .rsel_we_i     (1'b0),
      .rsel_i        (8'h00),
      .save_we_i     (1'b0),
      .save_i        (16'h0000),
      .save_req_o    (save_req),
      .restore_req_o (restore_req),
      .restore_word_o(restore_word),
      .restore_we_i  (1'b0),
      .restore_i     (16'h0000),
      .fpiar_o       (fpiar),
      .fpiar_we_i    (1'b0),
      .fpiar_i       (32'h0000_0000),
      .resp_read_o   (resp_read),
      .rsel_read_o   (rsel_read),
      .save_read_o   (save_read),
      .abort_o       (abort),
      .pv_o          (pv)
  );

  logic unused_x;
  assign unused_x = &{1'b1, arch_reset, cmd_pend, cmd_cond, cmd_word, opw_valid,
                      opw_data, opr_valid, save_req, restore_req, restore_word,
                      fpiar, resp_read, rsel_read, save_read, abort, pv};

endmodule
