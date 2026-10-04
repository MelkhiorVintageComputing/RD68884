// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 on the IIsiA7 Mini: an MC68881 for the Macintosh IIsi's MC68030, on
// its Processor Direct Slot (doc/boards.md).
//
// The board (Inputs/IIsiFPGA): an xc7a50tftg256-1 whose PDS pins reach the
// MC68030 bus through 74CB3T16211 bus switches that are always on (their
// output enables are tied to ground), so the FPGA drives D, DSACK and HALT
// with its own three-state buffers. The pin names are the LiteX platform's,
// Inputs/IIsiFPGA/IIsi-to-ztex-gateware/IIsiA7_Mini_pds.py; tools/board_pins.py
// writes the pin constraints from it.
//
// Xilinx-specific (MMCME2_BASE, BUFG), so outside rtl/ and its six-tool rule;
// sim/models/mmcme2_base.sv stands in for the two primitives in simulation.
// Every register still takes its value from a reset branch.
//
//   clock    clk100 -> MMCM (x10, 1000 MHz VCO) -> / CLKOUT0_DIVIDE -> core;
//            20 gives the 50 MHz the datapath is constrained for
//   reset    the core's rst_n is the MMCM's LOCKED, synchronised; the PDS RESET
//            pin is the architectural reset (FPU 9.9)
//   select   early chip select, FC = 7, A19-A16 = 2, A15-A13 = 1: coprocessor
//            ID 1 (FPU 10.3); a 32-bit port, SIZE and A0 strapped high
//            (FPU table 9-2)
//   HALT     held low until the FPU is out of reset, then three-stated for
//            good, so the MC68030 never asks an FPU that cannot answer
//   DSACK    actively negated before it is released (DSACK_NEGATE, FPU 9.8)
//   LEDs     0: the FPU ready; 1: a coprocessor-interface access, stretched;
//            2: RESET asserted; 3: HALT held
//
// Every other pin of the board is left out of the design; the bitstream leaves
// unused pins three-stated with no pull (boards/iisia7_mini/build.tcl).

module rd68884_iisia7_top #(
    parameter int CLKOUT0_DIVIDE = 20       // 1000 MHz / this: the core clock
) (
    input  logic        clk100,

    input  logic [31:0] A_3v3,
    input  logic [2:0]  fc_3v3,
    input  logic        as_3v3_n,
    input  logic        ds_3v3_n,
    input  logic        rw_3v3_n,           // the PDS's R/W: 1 = read
    input  logic        reset_3v3_n,
    inout  wire  [31:0] D_3v3,
    output wire  [1:0]  dsack_3v3_n,        // bit 0 = DSACK0; three-state
    output wire         halt_3v3_n,         // three-state, only ever driven low

    output logic [3:0]  user_leds
);

  // ---- the clock ------------------------------------------------------------------------
  logic clk_fb, clk_mmcm, clk, locked;

  MMCME2_BASE #(
      .CLKIN1_PERIOD   (10.0),
      .DIVCLK_DIVIDE   (1),
      .CLKFBOUT_MULT_F (10.0),
      .CLKOUT0_DIVIDE_F(CLKOUT0_DIVIDE)
  ) u_mmcm (
      .CLKIN1  (clk100),
      .CLKFBIN (clk_fb),
      .CLKFBOUT(clk_fb),
      .CLKOUT0 (clk_mmcm),
      .LOCKED  (locked),
      .PWRDWN  (1'b0),
      .RST     (1'b0)
  );

  BUFG u_bufg (
      .I(clk_mmcm),
      .O(clk)
  );

  // ---- reset ------------------------------------------------------------------------------
  // rst_n leaves reset two clocks after LOCKED; fpu_ready sixteen after that.
  logic [1:0] lock_q;
  logic       rst_n;
  always_ff @(posedge clk or negedge locked) begin
    if (!locked) begin
      lock_q <= 2'b00;
    end else begin
      lock_q <= {lock_q[0], 1'b1};
    end
  end
  assign rst_n = lock_q[1];

  logic [4:0] ready_q;
  logic       fpu_ready;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ready_q <= 5'd0;
    end else if (!ready_q[4]) begin
      ready_q <= ready_q + 5'd1;
    end
  end
  assign fpu_ready = ready_q[4];

  // ---- the FPU ----------------------------------------------------------------------------
  // Early chip select: no AS in the decode (FPU 10.3).
  logic        cs_n;
  assign cs_n = !(fc_3v3 == 3'd7 && A_3v3[19:16] == 4'h2 && A_3v3[15:13] == 3'd1);

  logic [31:0] d_o;
  logic [3:0]  d_oe;
  logic [1:0]  dsack_n_o;
  logic        dsack_oe;

  rd68884_top #(
      .DSACK_NEGATE(1)
  ) u_fpu (
      .clk      (clk),
      .rst_n    (rst_n),
      .reset_n_i(reset_3v3_n),
      .cs_n_i   (cs_n),
      .as_n_i   (as_3v3_n),
      .ds_n_i   (ds_3v3_n),
      .rw_i     (rw_3v3_n),
      .size_n_i (1'b1),
      .a_i      ({A_3v3[4:1], 1'b1}),
      .d_i      (D_3v3),
      .d_o      (d_o),
      .d_oe     (d_oe),
      .dsack_n_o(dsack_n_o),
      .dsack_oe (dsack_oe)
  );

  // ---- the pads -------------------------------------------------------------------------
  // rd68884_top's dsack_n_o: bit 1 = DSACK1. The platform lists the PDS's
  // DSACK0 first.
  for (genvar l = 0; l < 4; l++) begin : g_lane
    assign D_3v3[8*l +: 8] = d_oe[l] ? d_o[8*l +: 8] : 8'bz;
  end
  assign dsack_3v3_n[0] = dsack_oe ? dsack_n_o[0] : 1'bz;
  assign dsack_3v3_n[1] = dsack_oe ? dsack_n_o[1] : 1'bz;
  assign halt_3v3_n     = fpu_ready ? 1'bz : 1'b0;

  // ---- the LEDs ---------------------------------------------------------------------------
  // A coprocessor-interface access lights LED 1 for 2^22 clocks (84 ms at 50
  // MHz). CS and AS are asynchronous: synchronised first.
  logic access_s;
  rd68884_sync #(
      .WIDTH    (1),
      .RESET_VAL(1'b0)
  ) u_sync_access (
      .clk  (clk),
      .rst_n(rst_n),
      .d    (!cs_n && !as_3v3_n),
      .q    (access_s)
  );

  logic [21:0] blink_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      blink_q <= 22'd0;
    end else if (access_s) begin
      blink_q <= 22'h3F_FFFF;
    end else if (blink_q != 22'd0) begin
      blink_q <= blink_q - 22'd1;
    end
  end

  assign user_leds = {!fpu_ready, !reset_3v3_n, blink_q != 22'd0, fpu_ready};

endmodule
