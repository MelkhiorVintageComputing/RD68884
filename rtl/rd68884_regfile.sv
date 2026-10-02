// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 - SystemVerilog MC68881 floating-point coprocessor
//
// The register file: FP0-FP7, the exceptional operand (entry 8) and the
// microcode's temporaries, 128 entries of {sign, exponent[17:0],
// mantissa[71:0]} (doc/microcode.md). One write port, one read port with a
// registered output: a block RAM on every target.
//
// Neither the array nor the read register is reset. The array is not a
// register (tools/reset_audit.py does not count memories), and the reset
// microcode writes every entry the programmer can see before anything reads
// it (FPU 9.9). The read register is the second named exemption in
// tools/reset_audit.py: a block RAM keeps it inside the primitive, and nothing
// reads it before the microcode has issued a read.

module rd68884_regfile (
    input  logic        clk,
    input  logic        we,
    input  logic [6:0]  wa,
    input  logic [90:0] wd,
    input  logic        re,
    input  logic [6:0]  ra,
    output logic [90:0] q
);

  (* ram_style = "block" *)
  logic [90:0] mem [0:127];

  always_ff @(posedge clk) begin
    if (we) begin
      mem[wa] <= wd;
    end
    if (re) begin
      q <= mem[ra];
    end
  end

endmodule
