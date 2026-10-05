// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 -- the system: RD68021 (../RD68021 at the Makefile's RD68021_REV,
// exported to build/; used as it stands, never edited)
// running a program from memory, with RD68884 as its coprocessor at CpID 1.
//
//   memory     RD68021's own slave model, a 32-bit port at $0, 64 KB
//   the FPU    on a clock of its own (+fpu_ns, default 20 ns against the
//              CPU's 60) -- or, built with SYS_BUS_SYNC, the same-clock BIU
//              (BUS_SYNC = 1, BUS_SYNC_WAIT = SYS_BUS_SYNC_WAIT) on the CPU's
//              own clock -- behind an early chip select decoded from FC = 7, A19-A16 =
//              2, A15-A13 = 1 (FPU 10.3, figure 10-4); SIZE and A0 strapped for
//              the port width of the build (SYS_PORT 32, 16 or 8), the data
//              lanes of a narrow port tied as the board would (FPU section 11)
//
// The program is the check (sim/programs/fpu_m4.S): it fills a results block
// and sets 'DONE'; this waits for that and reports it.
//
// Two testbench registers let a program provoke the exceptions that make
// an FSAVE land in the middle of a coprocessor dialog (M8):
//   RES+$80  write N: N CPU clocks later, an interrupt at level 2,
//            autovectored, held until acknowledged
//   RES+$84  write an address: the next data access to it gets a bus error
//            (once); the processor's RTE runs it again
//
// and one lets it time itself (make cycles, doc/timing-divergences.md):
//   RES+$88  read: CPU clocks since reset, rewritten on every rising edge
//
// +lockstep=FILE writes, every FPU clock, what the BIU drove into the
// sequencer and what the sequencer drove back, for tools/iss/lockstep.py to
// replay on the ISS (doc/microcode.md).

`timescale 1ns / 1ps

`ifndef SYS_PORT
`define SYS_PORT 32
`endif

// SYS_MODEL: 68881 (RD68884, the default) or 68882 (RD68885, doc/rd68885.md).
`ifndef SYS_MODEL
`define SYS_MODEL 68881
`endif

// SYS_BOARD_IISIA7: the whole IIsiA7 Mini board top on the bus instead of
// rd68884_top (doc/boards.md): its own clock from a 100 MHz oscillator through
// the MMCM stand-in, its chip select from the full address and FC, DSACK and
// HALT as wires with the motherboard's pull-ups, HALT to the CPU. 32-bit port.
`ifdef SYS_BOARD_IISIA7
`define FPU board.u_fpu
`define FCLK board.clk
`else
`define FPU fpu
`endif

`ifdef SYS_BUS_SYNC
`ifndef SYS_BUS_SYNC_WAIT
`define SYS_BUS_SYNC_WAIT 0
`endif
`define FCLK clk
`elsif SYS_BOARD_IISIA7
`else
`define FCLK clk_fpu
`endif

module sys_tb;

  localparam real CPU_NS = 60.0;
  localparam int unsigned RES  = 32'h0000_C000;
  localparam int unsigned DONE = 32'h444F_4E45;

  real  fpu_ns;
  logic clk, clk_fpu, rst_n;

  initial begin
    if (!$value$plusargs("fpu_ns=%f", fpu_ns)) fpu_ns = 20.0;
    clk = 1'b0;
    forever #(CPU_NS / 2.0) clk = ~clk;
  end
  initial begin
    clk_fpu = 1'b0;
    #1;
    forever #(fpu_ns / 2.0) clk_fpu = ~clk_fpu;
  end

  // ---- the processor ----------------------------------------------------------
  logic  [2:0] fc_o;
  logic        fc_oe;
  logic [31:0] a_o;
  logic        a_oe;
  logic [31:0] d_o;
  logic        d_oe;
  logic  [1:0] siz_o;
  logic        siz_oe, ecs_n_o, ocs_n_o, rw_o, rw_oe, rmc_n_o, rmc_oe;
  logic        as_n_o, as_oe, ds_n_o, ds_oe, dben_o, dben_oe;
  logic  [1:0] dsack_n_i;
  logic        ipend_n_o, bg_n_o, reset_n_o, reset_n_oe, halt_n_o, halt_n_oe;

  wire [31:0] dbus;
  assign dbus = d_oe ? d_o : 32'bz;

  logic  [2:0] ipl_n;
  logic        avec_n, berr_n;

  rd68021_top #(.ICACHE_ENTRIES (64), .COPROCESSOR (1'b1)) cpu (
      .clk (clk), .rst_n (rst_n),
      .fc_o (fc_o), .fc_oe (fc_oe), .a_o (a_o), .a_oe (a_oe),
      .d_i (dbus), .d_o (d_o), .d_oe (d_oe),
      .siz_o (siz_o), .siz_oe (siz_oe), .ecs_n_o (ecs_n_o), .ocs_n_o (ocs_n_o),
      .rw_o (rw_o), .rw_oe (rw_oe), .rmc_n_o (rmc_n_o), .rmc_oe (rmc_oe),
      .as_n_o (as_n_o), .as_oe (as_oe), .ds_n_o (ds_n_o), .ds_oe (ds_oe),
      .dben_o (dben_o), .dben_oe (dben_oe), .dsack_n_i (dsack_n_i),
      .ipl_n_i (ipl_n), .ipend_n_o (ipend_n_o), .avec_n_i (avec_n),
      .br_n_i (1'b1), .bg_n_o (bg_n_o), .bgack_n_i (1'b1), .berr_n_i (berr_n),
      .reset_n_i (1'b1), .reset_n_o (reset_n_o), .reset_n_oe (reset_n_oe),
`ifdef SYS_BOARD_IISIA7
      .halt_n_i (board_halt), .halt_n_o (halt_n_o), .halt_n_oe (halt_n_oe),
`else
      .halt_n_i (1'b1), .halt_n_o (halt_n_o), .halt_n_oe (halt_n_oe),
`endif
      .cdis_n_i (1'b1));

  // ---- memory -------------------------------------------------------------------
  logic  [1:0] dsack32;
  wire  [31:0] d32;
  logic        oe32;
  assign dbus = oe32 ? d32 : 32'bz;

  rd68021_slave #(.PORT_BYTES (4), .WAITS (0), .BASE (32'h0000_0000),
                  .MASK (32'hF000_0000), .ABITS (16)) s32 (
      .clk (clk), .rst_n (rst_n), .a_i (a_o), .siz_i (siz_o), .fc_i (fc_o),
      .as_n_i (as_n_o), .ds_n_i (ds_n_o), .rw_i (rw_o), .d_i (dbus),
      .wr_inhibit_i (1'b0), .d_o (d32), .d_oe (oe32), .dsack_n_o (dsack32));

  // ---- the FPU ------------------------------------------------------------------
  localparam int PORT = `SYS_PORT;
  logic        cs_n;
  logic [31:0] fpu_d_i, fpu_d_o;
  logic  [3:0] fpu_d_oe;
  logic  [1:0] fpu_dsack_n;
  logic        fpu_dsack_oe;

  // Early chip select: no AS in the decode (FPU 10.3).
  assign cs_n = !(fc_o == 3'd7 && a_o[19:16] == 4'h2 && a_o[15:13] == 3'd1);

`ifdef SYS_BOARD_IISIA7
  // ---- the IIsiA7 Mini ---------------------------------------------------------
  logic clk100;
  initial begin
    clk100 = 1'b0;
    #3.3;
    forever #5.0 clk100 = ~clk100;
  end
  wire [1:0] board_dsack;
  wire       board_halt;
  wire [3:0] board_leds;
  assign (weak0, weak1) board_dsack = 2'b11;     // the motherboard's pull-ups
  assign (weak0, weak1) board_halt  = 1'b1;
  assign fpu_dsack_n  = board_dsack;
  assign fpu_dsack_oe = 1'b1;
  wire unused_board = &{1'b0, cs_n, fpu_d_i, fpu_d_o, fpu_d_oe, board_leds};
  assign fpu_d_i = dbus;
  assign fpu_d_o = 32'd0;
  assign fpu_d_oe = 4'd0;

  rd68884_iisia7_top board (
      .clk100      (clk100),
      .A_3v3       (a_o),
      .fc_3v3      (fc_o),
      .as_3v3_n    (as_n_o),
      .ds_3v3_n    (ds_n_o),
      .rw_3v3_n    (rw_o),
      .reset_3v3_n (1'b1),
      .D_3v3       (dbus),
      .dsack_3v3_n (board_dsack),
      .halt_3v3_n  (board_halt),
      .user_leds   (board_leds));

  // HALT: held while the FPU is not ready, released for good after.
  logic halt_seen, halt_released;
  initial begin halt_seen = 0; halt_released = 0; end
  always @(board_halt) begin
    if (board_halt === 1'b0) halt_seen = 1;
    if (board_halt === 1'b1 && halt_seen) halt_released = 1;
  end
`else
  // The lanes: on a 16-bit port D15-D0 of the FPU are tied to D31-D16, on an
  // 8-bit port all four bytes to D31-D24.
  generate
    if (PORT == 32) begin : g32
      for (genvar l = 0; l < 4; l++) begin : g
        assign dbus[8*l +: 8] = fpu_d_oe[l] ? fpu_d_o[8*l +: 8] : 8'bz;
      end
      assign fpu_d_i = dbus;
    end else if (PORT == 16) begin : g16
      for (genvar l = 0; l < 4; l++) begin : g
        assign dbus[8*(l | 2) +: 8] = fpu_d_oe[l] ? fpu_d_o[8*l +: 8] : 8'bz;
      end
      assign fpu_d_i = {dbus[31:16], dbus[31:16]};
    end else begin : g8
      for (genvar l = 0; l < 4; l++) begin : g
        assign dbus[31:24] = fpu_d_oe[l] ? fpu_d_o[8*l +: 8] : 8'bz;
      end
      assign fpu_d_i = {4{dbus[31:24]}};
    end
  endgenerate

`ifdef SYS_BUS_SYNC
  rd68884_top #(.BUS_SYNC(1), .BUS_SYNC_WAIT(`SYS_BUS_SYNC_WAIT), .MODEL(`SYS_MODEL)) fpu (
`else
  rd68884_top #(.MODEL(`SYS_MODEL)) fpu (
`endif
      .clk (`FCLK), .rst_n (rst_n), .reset_n_i (1'b1),
      .cs_n_i (cs_n), .as_n_i (as_n_o), .ds_n_i (ds_n_o), .rw_i (rw_o),
      .size_n_i (PORT != 8),
      .a_i ({a_o[4:1], (PORT == 32) ? 1'b1 : (PORT == 16) ? 1'b0 : a_o[0]}),
      .d_i (fpu_d_i), .d_o (fpu_d_o), .d_oe (fpu_d_oe),
      .dsack_n_o (fpu_dsack_n), .dsack_oe (fpu_dsack_oe));
`endif

  function automatic logic [31:0] peek(input int unsigned a);
    peek = {s32.mem[a], s32.mem[a+1], s32.mem[a+2], s32.mem[a+3]};
  endfunction

  // ---- the exception injectors (RES+$80, RES+$84) -------------------------------------
  int unsigned irq_count;
  logic        irq_on, irq_ack, fault_on, fault_now, fault_hit;
  logic [31:0] fault_addr;
  // An interrupt acknowledge: FC = 7, A19-A16 = $F (UM 5.4.1).
  logic        iack;
  assign iack = !as_n_o && fc_o == 3'd7 && a_o[19:16] == 4'hF;
  assign ipl_n  = irq_on ? 3'b101 : 3'b111;
  assign avec_n = !(iack && irq_on);
  // The faulted access: a data access to the address, while AS is asserted.
  assign fault_now = fault_on && !as_n_o && (fc_o == 3'd1 || fc_o == 3'd5) && a_o == fault_addr;
  assign berr_n = !fault_now;

  always @(posedge clk) begin
    if (!rst_n) begin
      irq_count <= 0; irq_on <= 1'b0; irq_ack <= 1'b0; fault_on <= 1'b0; fault_hit <= 1'b0; fault_addr <= 32'd0;
    end else begin
      if (peek(RES + 32'h80) != 0) begin
        irq_count <= peek(RES + 32'h80);
        {s32.mem[RES + 32'h80], s32.mem[RES + 32'h81], s32.mem[RES + 32'h82], s32.mem[RES + 32'h83]} <= 32'd0;
      end else if (irq_count != 0) begin
        irq_count <= irq_count - 1;
        if (irq_count == 1) irq_on <= 1'b1;
      end
      // Withdrawn once the acknowledge cycle it answered is over.
      if (iack && irq_on) irq_ack <= 1'b1;
      if (irq_ack && as_n_o) begin
        irq_on  <= 1'b0;
        irq_ack <= 1'b0;
      end
      if (peek(RES + 32'h84) != 0) begin
        fault_addr <= peek(RES + 32'h84);
        fault_on   <= 1'b1;
        {s32.mem[RES + 32'h84], s32.mem[RES + 32'h85], s32.mem[RES + 32'h86], s32.mem[RES + 32'h87]} <= 32'd0;
      end else if (fault_now) begin
        fault_hit <= 1'b1;
      end else if (fault_hit && as_n_o) begin
        fault_on  <= 1'b0;                     // once, when that cycle is over
        fault_hit <= 1'b0;
      end
    end
  end

  // RES+$88: the clock count. The memory model holds it, so a read of it is an
  // ordinary memory cycle that returns the count as it was on that cycle.
  int unsigned clocks;
  always @(posedge clk) begin
    if (!rst_n) clocks <= 0;
    else begin
      clocks <= clocks + 1;
      {s32.mem[RES + 32'h88], s32.mem[RES + 32'h89], s32.mem[RES + 32'h8A], s32.mem[RES + 32'h8B]} <= clocks;
    end
  end

  // DSACK with the board's pull-ups; none for the faulted access.
  assign dsack_n_i = fault_now ? 2'b11 : (dsack32 & (fpu_dsack_oe ? fpu_dsack_n : 2'b11));

  // ---- +trace: every bus cycle the processor makes, as it ends ------------------
  // The clock counts at AS asserted and negated, FC, the address, R or W, SIZ
  // and the data on the bus when DSACK (or BERR) ended it -- for reading a
  // row of make cycles.
  logic trace, as_q, ended;
  logic [31:0] tr_d;
  int unsigned tr_start;
  initial trace = $test$plusargs("trace");
  always @(posedge clk) begin
    if (!rst_n) begin
      as_q <= 1'b1; ended <= 1'b0; tr_d <= 32'd0; tr_start <= 0;
    end else begin
      as_q <= as_n_o;
      if (!as_n_o && as_q) tr_start <= clocks;
      if (!as_n_o && (dsack_n_i != 2'b11 || !berr_n || !avec_n)) begin
        ended <= 1'b1;
        tr_d  <= dbus;
      end
      if (as_n_o && !as_q) begin
        ended <= 1'b0;
        if (trace)
          $display("trace %0d-%0d fc %0d a %08h %s siz %0d d %08h%s", tr_start, clocks, fc_o, a_o,
                   rw_o ? "R" : "W", siz_o, tr_d, ended ? "" : " (no end)");
      end
    end
  end

  // ---- lockstep recording ----------------------------------------------------------
  integer ls;
  string  ls_file;
  initial begin
    ls = 0;
    if ($value$plusargs("lockstep=%s", ls_file)) ls = $fopen(ls_file, "w");
  end
  // BIU outputs, then the sequencer's outputs, then its micro-address, in the
  // order tools/iss/lockstep.py reads them. Sampled just before the edge.
  always @(posedge `FCLK) if (ls != 0 && `FPU.rst_n) begin
    $fwrite(ls, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h ",
      `FPU.arch_reset, `FPU.cmd_pend, `FPU.cmd_cond, `FPU.cmd_word, `FPU.opw_valid,
      `FPU.opw_data, `FPU.opr_valid, `FPU.save_req, `FPU.restore_req,
      `FPU.restore_word, `FPU.fpiar, `FPU.pv, `FPU.resp_read, `FPU.rsel_read,
      `FPU.save_read, `FPU.abort);
    // RD68885's conversion unit (constant in the MC68881 build).
    $fwrite(ls, "%h %h %h %h %h %h %h %h %h %h | ",
      `FPU.abort_ab, `FPU.cu_ready, `FPU.cu_valid, `FPU.cu_mid, `FPU.cu_word,
      `FPU.cu_d0, `FPU.cu_d1, `FPU.cu_d2, `FPU.cu_w0, `FPU.cu_w1);
    $fwrite(ls, "%h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h %h ",
      `FPU.resp_we, `FPU.resp, `FPU.resp_oneshot, `FPU.expect_v, `FPU.resp_cond,
      `FPU.cmd_ack, `FPU.opw_ack, `FPU.opr_we, `FPU.opr, `FPU.rsel_we, `FPU.rsel,
      `FPU.rsel_dir, `FPU.save_we, `FPU.save_v, `FPU.save_xfer, `FPU.restore_we,
      `FPU.restore_v, `FPU.restore_xfer, `FPU.fpiar_we, `FPU.fpiar_v, `FPU.clear);
    $fwrite(ls, "%h %h %h %h %h %h %h %h %h %h %h | %h\n",
      `FPU.apu_run, `FPU.pcen, `FPU.resp_xfer, `FPU.cu_take, `FPU.cu_load, `FPU.cu_idx,
      `FPU.cu_data, `FPU.cu_resume, `FPU.relatch, `FPU.relatch_word, `FPU.relatch_cond,
      `FPU.u_seq.upc);
  end

  // ---- the run ----------------------------------------------------------------------

  string       image, dump;
  int unsigned n, limit, k;
  int          fd;

  initial begin
    if (!$value$plusargs("image=%s", image)) image = "build/programs/fpu_m4.hex";
    if (!$value$plusargs("limit=%d", limit)) limit = 400000;
    if (!$value$plusargs("dump=%s", dump)) dump = "";
    // Memory the image leaves out reads as zero, as on RD68021's harness:
    // a program may count on its results block starting clear.
    for (k = 0; k < 65536; k = k + 1) s32.mem[k] = 8'h00;
    $readmemh(image, s32.mem);
    rst_n = 1'b0;
    repeat (8) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    n = 0;
    while (peek(RES) !== DONE && n < limit) begin
      @(posedge clk);
      n = n + 1;
    end
    if (ls != 0) $fclose(ls);
    // A C program's data buffer (sim/programs/fparith.c): RES+$14 long words
    // from RES+$100, for tools/fparith_compare.py.
    if (dump != "") begin
      fd = $fopen(dump, "w");
      for (k = 0; k < peek(RES + 32'h14); k = k + 1)
        $fdisplay(fd, "%08h", peek(RES + 32'h100 + 4 * k));
      $fclose(fd);
    end
`ifdef SYS_BUS_SYNC
    $display("sys_tb: port %0d, FPU on the CPU's clock, BUS_SYNC_WAIT %0d, %0d CPU clocks",
             PORT, `SYS_BUS_SYNC_WAIT, n);
`else
    $display("sys_tb: port %0d, FPU clock %.1f ns, %0d CPU clocks", PORT, fpu_ns, n);
`endif
    if (peek(RES) !== DONE) begin
      $display("FAIL: sys_tb: the program did not finish (PC %08h)", cpu.u_ifu.pc_d);
    end else if (peek(RES + 32'h10) !== 0) begin
      $display("FAIL: sys_tb: unexpected exception, vector %0d", peek(RES + 32'h10));
    end else if (peek(RES + 8) !== 0) begin
      $display("FAIL: sys_tb: %0d of %0d checks failed, first #%0d: found %08h, wanted %08h",
               peek(RES + 8), peek(RES + 4), peek(RES + 12), peek(RES + 32'h18),
               peek(RES + 32'h1C));
`ifdef SYS_BOARD_IISIA7
    end else if (!halt_seen || !halt_released) begin
      $display("FAIL: sys_tb: the board did not hold HALT and then release it");
`endif
    end else begin
      $display("PASS: sys_tb, %0d checks", peek(RES + 4));
    end
    $finish;
  end

endmodule
