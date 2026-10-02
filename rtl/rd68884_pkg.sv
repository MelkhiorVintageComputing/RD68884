// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 - SystemVerilog MC68881 floating-point coprocessor
//
// Architectural constants, transcribed once from the manual with their citation.
// FPU = MC68881UM_split. Refer to members with their full scope: yosys accepts no
// form of `import` (doc/coding-standard.md).

package rd68884_pkg;

  // -------------------------------------------------------------------------
  // Coprocessor interface registers, FPU table 7-2 / table 9-1. The value is the
  // CIR's byte offset, A4-A0 with the low bits the register's width ignores
  // cleared. Offset $08 is the operation-word CIR in table 7-2 and "reserved"
  // in table 9-1; the MC68881 implements neither (FPU 7.2.5).
  // -------------------------------------------------------------------------
  localparam logic [4:0] CIR_RESPONSE  = 5'h00;  // 16 R
  localparam logic [4:0] CIR_CONTROL   = 5'h02;  // 16 W
  localparam logic [4:0] CIR_SAVE      = 5'h04;  // 16 R
  localparam logic [4:0] CIR_RESTORE   = 5'h06;  // 16 R/W
  localparam logic [4:0] CIR_OPWORD    = 5'h08;  // 16, not implemented
  localparam logic [4:0] CIR_COMMAND   = 5'h0A;  // 16 W
  localparam logic [4:0] CIR_RSVD_0C   = 5'h0C;  // 16, reserved
  localparam logic [4:0] CIR_CONDITION = 5'h0E;  // 16 W
  localparam logic [4:0] CIR_OPERAND   = 5'h10;  // 32 R/W
  localparam logic [4:0] CIR_REGSEL    = 5'h14;  // 16 R
  localparam logic [4:0] CIR_RSVD_16   = 5'h16;  // 16, reserved
  localparam logic [4:0] CIR_INSTADDR  = 5'h18;  // 32 W
  localparam logic [4:0] CIR_OPADDR    = 5'h1C;  // 32, not implemented

  // -------------------------------------------------------------------------
  // Response primitives the MC68881 issues, FPU table 7-7 (checked against the
  // page image: the table's own $0801 row is mislabelled TF=0, and its
  // $3104/$3208/$320C rows are 68882-only and not used here).
  // -------------------------------------------------------------------------
  localparam logic [15:0] PRIM_NULL_FALSE   = 16'h0800;  // condition false
  localparam logic [15:0] PRIM_NULL_TRUE    = 16'h0801;  // condition true
  localparam logic [15:0] PRIM_NULL_IDLE    = 16'h0802;  // done, PF=1
  localparam logic [15:0] PRIM_NULL_REL     = 16'h0900;  // released, IA=1
  localparam logic [15:0] PRIM_NULL_REL_PC  = 16'h4900;  // released, pass PC
  localparam logic [15:0] PRIM_NULL_WAIT    = 16'h8900;  // come again, IA=1
  localparam logic [15:0] PRIM_NULL_WAIT_PC = 16'hC900;  // come again, pass PC
  localparam logic [15:0] PRIM_FLINE        = 16'h1C0B;  // pre-instruction, vector 11
  localparam logic [15:0] PRIM_BSUN         = 16'h5C30;  // pre-instruction, PC, vector 48
  localparam logic [15:0] PRIM_PROTOCOL     = 16'h1D0D;  // mid-instruction, vector 13

  // Save-CIR format words, FPU 6.4.2 and table 6-6. Version $1F is the
  // MC68881's.
  localparam logic [7:0]  FRAME_VERSION     = 8'h1F;
  localparam logic [15:0] FRAME_NULL        = 16'h0000;
  localparam logic [15:0] FRAME_COME_AGAIN  = 16'h0100;
  localparam logic [15:0] FRAME_INVALID     = 16'h0200;
  localparam logic [15:0] FRAME_IDLE        = 16'h1F18;  // 24 bytes follow
  localparam logic [15:0] FRAME_BUSY        = 16'h1FB4;  // 180 bytes follow

  // -------------------------------------------------------------------------
  // Exception vectors, FPU table 7-6.
  // -------------------------------------------------------------------------
  localparam logic [7:0] VEC_FLINE    = 8'd11;
  localparam logic [7:0] VEC_PROTOCOL = 8'd13;
  localparam logic [7:0] VEC_BSUN     = 8'd48;
  localparam logic [7:0] VEC_INEX     = 8'd49;
  localparam logic [7:0] VEC_DZ       = 8'd50;
  localparam logic [7:0] VEC_UNFL     = 8'd51;
  localparam logic [7:0] VEC_OPERR    = 8'd52;
  localparam logic [7:0] VEC_OVFL     = 8'd53;
  localparam logic [7:0] VEC_SNAN     = 8'd54;

endpackage
