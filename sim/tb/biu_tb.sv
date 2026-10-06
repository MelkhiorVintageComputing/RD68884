// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 -- the bus interface unit on its own.
//
// Three parts:
//
//   an MC68020 bus model  -- asynchronous bus cycles on its own clock, with the
//                            MC68020's dynamic bus sizing: it learns the port
//                            width from DSACK and drives and samples the data
//                            bus per UM tables 5-4 and 5-5, as the real chip;
//   a board               -- the FPU's straps (SIZE, A0) and lane ties for an
//                            8-, 16- or 32-bit port (FPU table 9-2, section
//                            11), resolving every data-bus group and failing on
//                            contention;
//   a scripted sequencer  -- the BIU's other side, driven by the test.
//
// Every test runs for each port width and for several bus/core clock pairs,
// with the core clock from three times the bus clock down to equal to it.
// The run ends with PASS or FAIL; a missing PASS is a failure (Makefile).
//
// Built with BIU_SYNC defined (and BIU_SYNC_WAIT 0 or 1), the BIU is the
// same-clock one (doc/bus-timing.md) and the bus model runs on the BIU's own
// clock with the MC68020's edges: AS (and DS, reading) a little after the
// falling edge entering S1, write data from the rising edge entering S2, DS
// (writing) after the falling edge entering S3, DSACK sampled on that falling
// edge and on each one entering a wait state, read data latched on the
// falling edge entering S5 -- RD68021's state ruler. Every cycle is then held
// to an exact count: its wait states are the rising edges, from the one
// entering S2, before the BIU took or acknowledged it, and on each of those the
// BIU must have been unable to (not ready, or START not yet seen). With one
// wait state the run ends with strobes too late for the edge entering S2,
// which must cost exactly one more.

`timescale 1ns / 1ps

// BIU_MODEL: 68881 (the default) or 68882 (RD68885, doc/rd68885.md).
`ifndef BIU_MODEL
`define BIU_MODEL 68881
`endif

`ifdef BIU_SYNC
`ifndef BIU_SYNC_WAIT
`define BIU_SYNC_WAIT 0
`endif
`define BCLK clk
`else
`define BCLK bclk
`endif

module biu_tb;

  // ==========================================================================
  // Clocks
  // ==========================================================================
  real  core_ns = 20.0;
  real  bus_ns  = 60.0;
  logic clk  = 1'b0;
  logic bclk = 1'b0;
  logic rst_n;

  always begin
    #(core_ns / 2.0) clk = ~clk;
  end
  always begin
    #(bus_ns / 2.0) bclk = ~bclk;
  end

  // ==========================================================================
  // The DUT
  // ==========================================================================
  logic        reset_n;
  logic        cs_n, as_n, ds_n, rw;
  logic [4:0]  mpu_a;
  integer      port;              // 8, 16 or 32: the board's wiring
  logic        size_n;
  logic [4:0]  fpu_a;
  logic [31:0] fpu_d_i, fpu_d_o;
  logic [3:0]  fpu_d_oe;
  logic [1:0]  dsack_n;
  logic        dsack_oe;

  logic        resp_we = 0, resp_oneshot = 0, resp_cond = 0, cmd_ack = 0, opw_ack = 0;
  logic        rsel_dir = 0, clear = 0;
  logic [5:0]  save_xfer = 0, restore_xfer = 0;
  logic        opr_we = 0, rsel_we = 0, save_we = 0, restore_we = 0, fpiar_we = 0;
  logic [15:0] resp_v = 0, save_v = 0, restore_v = 0;
  logic [2:0]  expect_v = 0;
  logic [31:0] opr_v = 0, fpiar_v = 0;
  logic [7:0]  rsel_v = 0;

  logic        arch_reset, cmd_pend, cmd_cond, opw_valid, opr_valid, save_req;
  logic        restore_req, resp_read, rsel_read, save_read, abort, pv;
  logic [15:0] cmd_word, restore_word;
  logic [31:0] opw_data, fpiar;
  // RD68885's conversion unit: the sequencer's side, driven by the tests.
  logic        apu_run = 0, pcen = 0, cu_take = 0, cu_load = 0, cu_resume = 0, relatch = 0;
  logic        relatch_cond = 0;
  logic [1:0]  resp_xfer = 0;
  logic [2:0]  cu_idx = 0;
  logic [31:0] cu_data = 0;
  logic [15:0] relatch_word = 0;
  logic        abort_ab, cu_ready, cu_valid, cu_mid;
  logic [15:0] cu_word;
  logic [31:0] cu_d0, cu_d1, cu_d2, cu_w0, cu_w1;
  logic        prec_x = 1, dcc_ack = 0;
  logic [90:0] rfb_q = 0;
  logic        rfb_we, rfb_re, dcc_v, dcc_cc_v;
  logic [2:0]  rfb_wa, rfb_ra;
  logic [90:0] rfb_wd;
  logic [3:0]  dcc_cc;

`ifdef BIU_SYNC
  rd68884_biu #(.BUS_SYNC(1), .BUS_SYNC_WAIT(`BIU_SYNC_WAIT), .MODEL(`BIU_MODEL)) dut (
`elsif BIU_NEGATE
  rd68884_biu #(.DSACK_NEGATE(1), .MODEL(`BIU_MODEL)) dut (
`else
  rd68884_biu #(.MODEL(`BIU_MODEL)) dut (
`endif
      .clk(clk), .rst_n(rst_n), .reset_n_i(reset_n),
      .cs_n_i(cs_n), .as_n_i(as_n), .ds_n_i(ds_n), .rw_i(rw),
      .size_n_i(size_n), .a_i(fpu_a), .d_i(fpu_d_i), .d_o(fpu_d_o),
      .d_oe(fpu_d_oe), .dsack_n_o(dsack_n), .dsack_oe(dsack_oe),
      .arch_reset_o(arch_reset),
      .resp_we_i(resp_we), .resp_i(resp_v), .resp_oneshot_i(resp_oneshot),
      .expect_i(expect_v), .resp_cond_i(resp_cond),
      .cmd_pend_o(cmd_pend), .cmd_cond_o(cmd_cond), .cmd_word_o(cmd_word),
      .cmd_ack_i(cmd_ack),
      .opw_valid_o(opw_valid), .opw_data_o(opw_data), .opw_ack_i(opw_ack),
      .opr_we_i(opr_we), .opr_i(opr_v), .opr_valid_o(opr_valid),
      .rsel_we_i(rsel_we), .rsel_i(rsel_v), .rsel_dir_i(rsel_dir),
      .save_we_i(save_we), .save_i(save_v), .save_xfer_i(save_xfer), .save_req_o(save_req),
      .restore_req_o(restore_req), .restore_word_o(restore_word),
      .restore_we_i(restore_we), .restore_i(restore_v),
      .restore_xfer_i(restore_xfer), .clear_i(clear),
      .fpiar_o(fpiar), .fpiar_we_i(fpiar_we), .fpiar_i(fpiar_v),
      .resp_read_o(resp_read), .rsel_read_o(rsel_read), .save_read_o(save_read),
      .abort_o(abort), .pv_o(pv),
      .apu_run_i(apu_run), .pcen_i(pcen), .resp_xfer_i(resp_xfer), .cu_take_i(cu_take),
      .cu_load_i(cu_load), .cu_idx_i(cu_idx), .cu_data_i(cu_data), .cu_resume_i(cu_resume),
      .relatch_i(relatch), .relatch_word_i(relatch_word), .relatch_cond_i(relatch_cond),
      .abort_ab_o(abort_ab), .cu_ready_o(cu_ready), .cu_valid_o(cu_valid), .cu_mid_o(cu_mid),
      .cu_word_o(cu_word), .cu_d0_o(cu_d0), .cu_d1_o(cu_d1), .cu_d2_o(cu_d2),
      .cu_w0_o(cu_w0), .cu_w1_o(cu_w1),
      .prec_x_i(prec_x), .rfb_q_i(rfb_q), .rfb_we_o(rfb_we), .rfb_wa_o(rfb_wa),
      .rfb_wd_o(rfb_wd), .rfb_re_o(rfb_re), .rfb_ra_o(rfb_ra), .dcc_v_o(dcc_v),
      .dcc_cc_v_o(dcc_cc_v), .dcc_cc_o(dcc_cc), .dcc_ack_i(dcc_ack));

  // ==========================================================================
  // Bookkeeping
  // ==========================================================================
  integer errors = 0;
  integer checks = 0;
  string  where = "";

  task automatic check(input bit ok, input string msg);
    checks++;
    if (!ok) begin
      errors++;
      if (errors < 40) $display("FAIL: [%s] %s (t=%0t)", where, msg, $time);
    end
  endtask

  integer n_resp_read = 0, n_rsel_read = 0, n_save_read = 0, n_abort = 0, n_abort_ab = 0;
  always @(posedge clk) begin
    if (resp_read) n_resp_read++;
    if (rsel_read) n_rsel_read++;
    if (save_read) n_save_read++;
    if (abort)     n_abort++;
    if (abort_ab)  n_abort_ab++;
  end

  // ==========================================================================
  // The board: straps, lane ties, contention
  // ==========================================================================
  logic [31:0] mpu_d_o;
  logic        mpu_d_oe;
  logic [31:0] mpu_d_i;           // what the MPU's pins see

  assign size_n = (port != 8);
  // FPU table 9-2: A0 strapped high (32), low (16), or the MPU's A0 (8).
  assign fpu_a = {mpu_a[4:1], (port == 32) ? 1'b1 : (port == 16) ? 1'b0 : mpu_a[0]};

  // Each tied group of byte lanes resolves to the one byte driven onto it.
  // The MPU's own lanes: all four on a 32-bit port, D31-D16 on a 16-bit port,
  // D31-D24 on an 8-bit one (UM 5.2.1).
  function automatic logic [7:0] lane(input logic [31:0] v, input integer l);
    lane = v[8*l +: 8];
  endfunction

  // The group an FPU lane is tied into.
  function automatic integer group_of(input integer l);
    group_of = (port == 32) ? l : (port == 16) ? (l | 2) : 3;
  endfunction

  function automatic bit mpu_connected(input integer l);
    mpu_connected = (port == 32) || (port == 16 && l >= 2) || (l == 3);
  endfunction

  logic [7:0] grp [0:3];
  integer     drivers [0:3];
  always @* begin : resolve
    integer g, l;
    for (g = 0; g < 4; g++) begin
      grp[g] = 8'hFF;              // pulled up
      drivers[g] = 0;
    end
    for (l = 0; l < 4; l++) begin
      if (fpu_d_oe[l]) begin
        grp[group_of(l)] = lane(fpu_d_o, l);
        drivers[group_of(l)]++;
      end
      if (mpu_d_oe && mpu_connected(l)) begin
        grp[l] = lane(mpu_d_o, l);
        drivers[l]++;
      end
    end
    for (l = 0; l < 4; l++) begin
      fpu_d_i[8*l +: 8] = grp[group_of(l)];
      mpu_d_i[8*l +: 8] = grp[l];
    end
  end

  // Contention, and DSACK only inside an FPU access (FPU 9.8).
  logic [1:0] dsack_bus;
  assign dsack_bus = dsack_oe ? dsack_n : 2'b11;
  always @(posedge clk or negedge clk or posedge bclk or negedge bclk) begin : watch
    integer g;
    #0.5;
    for (g = 0; g < 4; g++) begin
      if (drivers[g] > 1) check(0, $sformatf("contention on byte group %0d", g));
    end
  end

  // The AC behaviour that holds by construction (doc/bus-timing.md), checked
  // at every change of a strobe and every core-clock edge, with no delay at
  // all: START = CS * AS * (DS + /RW), section 12 note 8.
  logic start_pins;
  assign start_pins = !cs_n && !as_n && (!ds_n || !rw);
  // BIU_NEGATE (DSACK_NEGATE = 1): after START falls DSACK may stay driven,
  // negated, until the first core-clock edge that samples the stale guard --
  // at most one core period: START falling at the very instant of an edge is
  // seen on the next one. negations counts the pulses seen, which must be
  // some.
  realtime start_fell = 0.0;
  integer  negations = 0;
  always @(negedge start_pins) start_fell = $realtime;
  always @(posedge dsack_oe) if (!start_pins) negations++;

  always @(start_pins or dsack_oe or fpu_d_oe or posedge clk) begin : ac
    #0;
`ifdef BIU_NEGATE
    // FPU 9.8: driven high (negated) after START falls, then three-stated,
    // within the window; still negated at once (specification 21).
    if (dsack_oe && !start_pins &&
        (dsack_n != 2'b11 || $realtime - start_fell > core_ns + 0.2))
      check(0, $sformatf("DSACK %s with START false",
                         dsack_n != 2'b11 ? "asserted" : "driven more than a core clock"));
`else
    // Specifications 21/22: DSACK released the moment START is false.
    if (dsack_oe && !start_pins)
      check(0, "DSACK driven with START false (specifications 21/22)");
`endif
    // Specifications 15/16: data released with DS (reads only).
    if (fpu_d_oe != 0 && (!start_pins || ds_n || !rw))
      check(0, "data driven outside a read with DS asserted (specification 16)");
    // Specification 20: data valid no later than DSACK on a read.
    if (dsack_oe && dsack_n != 2'b11 && rw && fpu_d_oe == 4'b0000)
      check(0, "DSACK asserted on a read with no data lanes (specification 20)");
    // Specification 19A: DSACK0 and DSACK1 change together.
    if (dsack_oe && dsack_n == 2'b11 && start_pins)
      check(0, "DSACK enabled but neither line asserted (specification 19A)");
  end

  // ==========================================================================
  // The MC68020 bus model
  // ==========================================================================
  integer max_wait = 1000;         // bus clocks before a missing DSACK is an error
  logic [1:0] last_dsack;
  integer     last_waits;

  // One bus cycle. siz is the MC68020's SIZ (1-4 bytes still to go), op the
  // remaining operand right-aligned (OP3 = its least significant byte).
  task automatic cycle_async(input logic [4:0] a, input bit read, input integer siz,
                       input logic [31:0] op, output logic [31:0] bus);
    logic [7:0] o0, o1, o2, o3;
    integer waits;
    o3 = op[7:0]; o2 = op[15:8]; o1 = op[23:16]; o0 = op[31:24];
    @(posedge bclk);                                  // S0
    mpu_a <= a;
    rw    <= read;
    cs_n  <= 1'b0;                                    // early chip select
    @(negedge bclk);                                  // S1
    as_n <= 1'b0;
    if (read) ds_n <= 1'b0;
    if (!read) begin
      // UM table 5-5, the write multiplexer.
      case (siz)
        1: mpu_d_o <= {o3, o3, o3, o3};
        2: mpu_d_o <= a[0] ? {o2, o2, o3, o2} : {o2, o3, o2, o3};
        3: case (a[1:0])
             2'b00: mpu_d_o <= {o1, o2, o3, o0};
             2'b01: mpu_d_o <= {o1, o1, o2, o3};
             2'b10: mpu_d_o <= {o1, o2, o1, o2};
             default: mpu_d_o <= {o1, o1, o2, o1};
           endcase
        default: case (a[1:0])
             2'b00: mpu_d_o <= {o0, o1, o2, o3};
             2'b01: mpu_d_o <= {o0, o0, o1, o2};
             2'b10: mpu_d_o <= {o0, o1, o0, o1};
             default: mpu_d_o <= {o0, o0, o1, o0};
           endcase
      endcase
      mpu_d_oe <= 1'b1;
      @(negedge bclk);                                // S3
      ds_n <= 1'b0;
    end
    waits = 0;
    @(negedge bclk);
    while (dsack_bus == 2'b11 && waits <= max_wait) begin
      waits++;
      @(negedge bclk);
    end
    if (waits > max_wait) check(0, $sformatf("no DSACK at $%02h", a));
    last_dsack = dsack_bus;
    last_waits = waits;
    if (waits > access_maxwait) access_maxwait = waits;
    @(negedge bclk);                                  // data latched, then negate
    bus = mpu_d_i;
    as_n <= 1'b1;
    ds_n <= 1'b1;
    @(posedge bclk);
    cs_n     <= 1'b1;
    mpu_d_oe <= 1'b0;
    rw       <= 1'b1;
  endtask

`ifdef BIU_SYNC
  // The same-clock cycle: see the header. strobe_ns is how long after a
  // falling edge the MC68020 changes AS and DS (UM specs 9, 12), data_ns how
  // long after the rising edge entering S2 its write data is valid.
  real strobe_ns = 7.0;
  real data_ns   = 5.0;
  integer sync_cycles = 0, sync_waits = 0;

  task automatic cycle_sync(input logic [4:0] a, input bit read, input integer siz,
                            input logic [31:0] op, output logic [31:0] bus);
    logic [7:0] o0, o1, o2, o3;
    logic [31:0] wd;
    integer waits, edge_n, acked;
    bit sampled;
    o3 = op[7:0]; o2 = op[15:8]; o1 = op[23:16]; o0 = op[31:24];
    case (siz)                                        // UM table 5-5
      1: wd = {o3, o3, o3, o3};
      2: wd = a[0] ? {o2, o2, o3, o2} : {o2, o3, o2, o3};
      3: case (a[1:0])
           2'b00: wd = {o1, o2, o3, o0};
           2'b01: wd = {o1, o1, o2, o3};
           2'b10: wd = {o1, o2, o1, o2};
           default: wd = {o1, o1, o2, o1};
         endcase
      default: case (a[1:0])
           2'b00: wd = {o0, o1, o2, o3};
           2'b01: wd = {o0, o0, o1, o2};
           2'b10: wd = {o0, o1, o0, o1};
           default: wd = {o0, o0, o1, o0};
         endcase
    endcase
    @(posedge clk);                                   // entering S0
    mpu_a <= a;
    rw    <= read;
    cs_n  <= 1'b0;                                    // early chip select
    @(negedge clk);                                   // entering S1
    // Intra-assignment delays: the strobes change strobe_ns after the edge
    // while the model goes on counting edges, even when that is past the next.
    as_n <= #(strobe_ns) 1'b0;
    if (read) ds_n <= #(strobe_ns) 1'b0;
    // The rising edges from the one entering S2, counted by edge_n. The BIU's
    // combinational take, early_ack and ready are read here, before this
    // edge's register updates (nonblocking) have happened.
    edge_n  = 0;
    acked   = -1;
    waits   = 0;
    sampled = 0;
    while (!sampled) begin
      @(posedge clk);                                 // entering S2, S4 or a WH
      if (acked < 0) begin
        if (dut.take || dut.early_ack) begin
          acked = edge_n;
        end else if (edge_n >= `BIU_SYNC_WAIT && dut.ready && dut.start_s) begin
          // A write too: zero_wait acknowledges it before DS (early_ack).
          check(0, $sformatf("$%02h: not taken at rising edge %0d though ready", a, edge_n));
        end
      end
      edge_n++;
      if (edge_n == 1 && !read) begin
        mpu_d_o  <= #(data_ns) wd;
        mpu_d_oe <= #(data_ns) 1'b1;
      end
      @(negedge clk);                                 // entering S3 or a WL
      if (dsack_bus != 2'b11) begin
        sampled = 1;
      end else begin
        waits++;
        if (waits > max_wait) begin
          check(0, $sformatf("no DSACK at $%02h", a));
          sampled = 1;
        end
      end
      if (!read && edge_n == 1) begin
        ds_n <= #(strobe_ns) 1'b0;
      end
    end
    last_dsack = dsack_bus;
    check(acked >= 0 && waits == acked,
          $sformatf("$%02h: %0d wait states, acknowledged at rising edge %0d", a, waits, acked));
    sync_cycles++;
    sync_waits += waits;
    last_waits = waits;
    if (waits > access_maxwait) access_maxwait = waits;
    @(posedge clk);                                   // entering S4
    @(negedge clk);                                   // entering S5: data latched
    bus = mpu_d_i;
    as_n <= #(strobe_ns) 1'b1;
    ds_n <= #(strobe_ns) 1'b1;
    @(posedge clk);                                   // entering the next S0
    cs_n     <= 1'b1;
    mpu_d_oe <= 1'b0;
    rw       <= 1'b1;
  endtask
`endif

  task automatic cycle(input logic [4:0] a, input bit read, input integer siz,
                       input logic [31:0] op, output logic [31:0] bus);
`ifdef BIU_SYNC
    cycle_sync(a, read, siz, op, bus);
`else
    cycle_async(a, read, siz, op, bus);
`endif
  endtask

  // A whole operand of n bytes at a, with dynamic bus sizing (UM 5.2.2).
  integer access_cycles;
  integer access_maxwait;
  logic [1:0] access_dsack;
  task automatic access(input logic [4:0] a0, input bit read, input integer n,
                        input logic [31:0] wv, output logic [31:0] rv);
    integer rem, w, x, ln;
    logic [4:0] a;
    logic [31:0] bus;
    rem = n; a = a0; rv = 0; access_cycles = 0; access_maxwait = 0;
    while (rem > 0) begin
      cycle(a, read, rem > 4 ? 4 : rem, wv & ((64'd1 << (8 * rem)) - 1), bus);
      access_cycles++;
      if (access_cycles == 1) access_dsack = last_dsack;
      w = (last_dsack == 2'b00) ? 4 : (last_dsack == 2'b01) ? 2 : 1;
      x = w - (a % w);
      if (x > rem) x = rem;
      for (int j = 0; j < x; j++) begin
        // UM table 5-4: the byte at address offset k is on the lane the
        // port's width puts it on.
        ln = (w == 4) ? 3 - ((a + j) % 4) : (w == 2) ? 3 - ((a + j) % 2) : 3;
        rv = (rv << 8) | bus[8*ln +: 8];
      end
      a = a + x;
      rem = rem - x;
    end
  endtask

  task automatic rd(input logic [4:0] a, input integer n, output logic [31:0] v);
    access(a, 1'b1, n, 32'd0, v);
  endtask

  task automatic wr(input logic [4:0] a, input integer n, input logic [31:0] v);
    logic [31:0] dummy;
    access(a, 1'b0, n, v, dummy);
  endtask

  // The DSACK encoding FPU table 9-3 promises for an access at a.
  function automatic logic [1:0] want_dsack(input logic [4:0] a);
    if (port == 8) return 2'b10;
    if (port == 16) return 2'b01;
    return a[4] ? 2'b00 : 2'b01;
  endfunction

  task automatic rd_check(input logic [4:0] a, input integer n, input logic [31:0] want,
                          input string what);
    logic [31:0] v;
    rd(a, n, v);
    check(v == want, $sformatf("%s: read $%0h, want $%0h", what, v, want));
    check(access_dsack == want_dsack(a),
          $sformatf("%s: DSACK %b, want %b", what, access_dsack, want_dsack(a)));
  endtask

  // ==========================================================================
  // The scripted sequencer
  // ==========================================================================
  task automatic seq_resp(input logic [15:0] v, input bit oneshot, input logic [2:0] e);
    @(posedge clk);
    resp_v <= v; resp_oneshot <= oneshot; expect_v <= e; resp_we <= 1'b1;
    @(posedge clk);
    resp_we <= 1'b0;
    @(negedge clk);
  endtask

  task automatic ack_cmd();
    @(posedge clk);
    cmd_ack <= 1'b1;
    @(posedge clk);
    cmd_ack <= 1'b0;
    @(negedge clk);
  endtask

  task automatic ack_opw();
    @(posedge clk);
    opw_ack <= 1'b1;
    @(posedge clk);
    opw_ack <= 1'b0;
    @(negedge clk);
  endtask

  task automatic seq_opr(input logic [31:0] v);
    @(posedge clk);
    opr_v <= v; opr_we <= 1'b1;
    @(posedge clk);
    opr_we <= 1'b0;
    @(negedge clk);
  endtask

  task automatic seq_save(input logic [15:0] v);
    @(posedge clk);
    save_v <= v; save_xfer <= 6'd0; save_we <= 1'b1;
    @(posedge clk);
    save_we <= 1'b0;
    @(negedge clk);
  endtask

  task automatic seq_restore(input logic [15:0] v);
    @(posedge clk);
    restore_v <= v; restore_xfer <= 6'd0; restore_we <= 1'b1;
    @(posedge clk);
    restore_we <= 1'b0;
    @(negedge clk);
  endtask

  task automatic seq_rsel(input logic [7:0] v);
    @(posedge clk);
    rsel_v <= v; rsel_we <= 1'b1;
    @(posedge clk);
    rsel_we <= 1'b0;
    @(negedge clk);
  endtask

  task automatic idle(input integer n);
    repeat (n) @(posedge `BCLK);
  endtask

  // ==========================================================================
  // The tests
  // ==========================================================================
  localparam logic [2:0] E_CMD = 0, E_RESP = 1, E_OPW = 2, E_OPR = 3, E_RSEL = 4;

  task automatic do_reset();
    rst_n = 1'b0;
    reset_n = 1'b1;
    cs_n = 1; as_n = 1; ds_n = 1; rw = 1; mpu_a = 0; mpu_d_o = 0; mpu_d_oe = 0;
    repeat (4) @(negedge clk);
    rst_n = 1'b1;
    repeat (4) @(negedge clk);
  endtask

  task automatic t_idle_and_reserved();
    where = "reserved";
    rd_check(5'h00, 2, 32'h0802, "idle response");
    rd_check(5'h02, 2, 32'hFFFF, "control reads ones");
    rd_check(5'h08, 2, 32'hFFFF, "operation word");
    rd_check(5'h0A, 2, 32'hFFFF, "command reads ones");
    rd_check(5'h0C, 2, 32'hFFFF, "reserved $0C");
    rd_check(5'h0E, 2, 32'hFFFF, "condition reads ones");
    rd_check(5'h16, 2, 32'hFFFF, "reserved $16");
    rd_check(5'h18, 4, 32'hFFFF_FFFF, "instruction address reads ones");
    rd_check(5'h1C, 4, 32'hFFFF_FFFF, "operand address");
    wr(5'h08, 2, 32'h1234);
    wr(5'h1C, 4, 32'h1234_5678);
    check(!pv && !cmd_pend, "writes to unimplemented CIRs are ignored");
    rd_check(5'h00, 2, 32'h0802, "still idle");
  endtask

  // The response hold-off (rtl/rd68884_biu.sv, RESP_HOLD = 20 clocks): a
  // response read that finds a command taken by the sequencer but unanswered
  // waits for the answer, and gets it; with no answer it gets the null
  // come-again primitive once the hold is over. A command the sequencer has not
  // taken is answered come-again at once.
  // The longest the BIU has held a response read since max_hold was cleared.
  integer max_hold = 0;
  always @(posedge clk) if (dut.resp_wait_q > max_hold) max_hold = dut.resp_wait_q;

  task automatic t_resp_hold();
    logic [31:0] v;
    where = "response hold-off";
    wr(5'h0A, 2, 32'h5422);
    ack_cmd();
    fork
      rd(5'h00, 2, v);
      begin
        repeat (6) @(posedge clk);
        seq_resp(16'h9504, 1, E_OPW);
      end
    join
    check(v == 32'h9504, $sformatf("answered during the hold: read $%0h", v));
    wr(5'h02, 2, 32'h0000);                           // abort: back to idle
    idle(2);
    wr(5'h0A, 2, 32'h5422);
    ack_cmd();
    max_hold = 0;
    rd(5'h00, 2, v);
    check(v == 32'h8900, $sformatf("unanswered: come-again after the hold, read $%0h", v));
    check(max_hold == 20, $sformatf("held %0d clocks, want the hold-off's 20", max_hold));
    wr(5'h02, 2, 32'h0000);
    idle(2);
    wr(5'h0A, 2, 32'h5422);
    max_hold = 0;
    rd(5'h00, 2, v);
    check(v == 32'h8900, $sformatf("not taken: come-again, read $%0h", v));
    check(max_hold == 0, $sformatf("not taken: held %0d clocks", max_hold));
    wr(5'h02, 2, 32'h0000);
    idle(2);
  endtask

  task automatic t_command_and_oneshot();
    logic [31:0] v;
    integer n0;
    where = "command";
    wr(5'h0A, 2, 32'h5422);
    idle(1);
    check(cmd_pend && !cmd_cond && cmd_word == 16'h5422, "command latched");
    rd_check(5'h00, 2, 32'h8900, "null CA=1 until answered");
    ack_cmd();
    n0 = n_resp_read;
    seq_resp(16'h9504, 1, E_OPW);
    rd_check(5'h00, 2, 32'h9504, "the primitive");
    idle(1);
    check(n_resp_read == n0 + 1, "one resp_read event");
    rd_check(5'h00, 2, 32'h8900, "one-shot reverts to null");
    idle(1);
    check(n_resp_read == n0 + 2, "every read of the current response is an event");
    // The operand.
    wr(5'h10, 4, 32'h4000_0001);
    idle(1);
    check(opw_valid && opw_data == 32'h4000_0001, "operand long word");
    ack_opw();
    seq_resp(16'h0802, 0, E_CMD);
    wr(5'h0E, 2, 32'h0012);
    idle(1);
    check(cmd_pend && cmd_cond && cmd_word == 16'h0012, "condition latched");
    ack_cmd();
    seq_resp(16'h0801, 0, E_CMD);
    rd_check(5'h00, 2, 32'h0801, "condition result");
  endtask

  task automatic t_backpressure();
    integer t0, t1;
    where = "backpressure";
    seq_resp(16'h960C, 1, E_OPW);
    rd_check(5'h00, 2, 32'h960C, "evaluate EA, 12 bytes");
    wr(5'h10, 4, 32'h1111_1111);
    // The second long word must wait for the sequencer to take the first.
    fork
      wr(5'h10, 4, 32'h2222_2222);
      begin
        // Long enough that even the four byte cycles of an 8-bit port reach
        // the last byte, which is the one that has to wait.
        repeat (200) @(posedge clk);
        check(opw_valid && opw_data == 32'h1111_1111, "first long word held");
        ack_opw();
      end
    join
    idle(1);
    check(opw_valid && opw_data == 32'h2222_2222, "second long word");
    check(access_maxwait > 2, "the second write was held off by DSACK");
    ack_opw();
    wr(5'h10, 4, 32'h3333_3333);
    idle(1);
    check(opw_data == 32'h3333_3333, "third long word");
    ack_opw();
    seq_resp(16'h0802, 0, E_CMD);
  endtask

  task automatic t_short_operands();
    where = "short";
    seq_resp(16'h9501, 1, E_OPW);
    rd_check(5'h00, 2, 32'h9501, "byte immediate primitive");
    wr(5'h10, 1, 32'h0000_00AB);
    idle(1);
    check(opw_valid && opw_data[31:24] == 8'hAB, "byte left-aligned");
    check(access_cycles == 1, "one cycle for an immediate byte");
    ack_opw();
    seq_resp(16'h9502, 1, E_OPW);
    rd_check(5'h00, 2, 32'h9502, "word immediate primitive");
    wr(5'h10, 2, 32'h0000_1234);
    idle(1);
    check(opw_valid && opw_data[31:16] == 16'h1234, "word left-aligned");
    ack_opw();
    seq_resp(16'hB101, 1, E_OPR);
    rd_check(5'h00, 2, 32'hB101, "byte out primitive");
    seq_opr(32'h5A00_0000);
    rd_check(5'h10, 1, 32'h5A, "byte out");
    idle(1);
    check(!opr_valid, "byte out consumed");
    seq_resp(16'h0802, 0, E_CMD);
  endtask

  task automatic t_operand_read();
    logic [31:0] v;
    where = "opr";
    seq_resp(16'hB208, 1, E_OPR);
    rd_check(5'h00, 2, 32'hB208, "evaluate EA out, 8 bytes");
    seq_opr(32'hDEAD_BEEF);
    rd_check(5'h10, 4, 32'hDEAD_BEEF, "first long word out");
    check(!opr_valid, "consumed");
    // A read before the data is there waits for it.
    fork
      rd(5'h10, 4, v);
      begin
        repeat (30) @(posedge clk);
        seq_opr(32'h0123_4567);
      end
    join
    check(v == 32'h0123_4567, "second long word, after waiting");
    check(access_maxwait > 2, "the read was held off by DSACK");
    seq_resp(16'h0802, 0, E_CMD);
  endtask

  task automatic t_regsel();
    integer n0;
    where = "regsel";
    seq_rsel(8'hC5);
    seq_resp(16'h810C, 1, E_RSEL);
    rd_check(5'h00, 2, 32'h810C, "transfer multiple");
    n0 = n_rsel_read;
    rd_check(5'h14, 2, 32'hC500, "register select: mask, low byte zero");
    idle(1);
    check(n_rsel_read == n0 + 1, "rsel_read event");
    check(!pv, "legal");
    seq_resp(16'h0802, 0, E_CMD);
  endtask

  task automatic t_violations();
    integer n0;
    where = "violation";
    wr(5'h10, 4, 32'h0);                       // operand write when idle
    idle(1);
    check(pv, "operand write when a command is expected");
    rd_check(5'h00, 2, 32'h1D0D, "protocol violation primitive");
    n0 = n_abort;
    wr(5'h02, 2, 32'h0002);                    // exception acknowledge
    idle(1);
    check(!pv && n_abort == n0 + 1, "acknowledge clears it");
    rd_check(5'h00, 2, 32'h0802, "idle again");
    wr(5'h14, 2, 32'h0);                       // the register select write
    idle(1);
    check(pv, "register select write");
    wr(5'h02, 2, 32'h0001);
    wr(5'h0A, 2, 32'h0000);
    ack_cmd();
    wr(5'h0A, 2, 32'h0000);                    // a second command, initial phase
    idle(1);
    check(pv, "command in the initial phase");
    wr(5'h02, 2, 32'h0001);
    rd_check(5'h10, 4, 32'hFFFF_FFFF, "operand read when idle");
    check(pv, "operand read when a command is expected");
    wr(5'h02, 2, 32'h0001);
    idle(1);
    check(!pv && !cmd_pend, "clean after the aborts");
  endtask

  task automatic t_save_restore();
    integer n0;
    where = "save";
    rd_check(5'h04, 2, 32'h0118, "come again while unprepared");
    idle(1);
    check(save_req, "save request raised");
    seq_save(16'h1F18);
    check(!save_req, "cleared by the sequencer");
    n0 = n_save_read;
    rd_check(5'h04, 2, 32'h1F18, "the format word");
    idle(1);
    check(n_save_read == n0 + 1, "save_read event");
    rd_check(5'h04, 2, 32'h0118, "consumed");
    seq_save(16'h0018);
    rd_check(5'h04, 2, 32'h0018, "null");
    where = "restore";
    wr(5'h06, 2, 32'h1F18);
    idle(1);
    check(restore_req && restore_word == 16'h1F18, "restore word");
    fork
      rd_check(5'h06, 2, 32'h1F18, "validated read-back");
      begin
        repeat (30) @(posedge clk);
        seq_restore(16'h1F18);
      end
    join
    wr(5'h06, 2, 32'h3F18);
    seq_restore(16'h0218);
    rd_check(5'h06, 2, 32'h0218, "invalid read-back");
    wr(5'h02, 2, 32'h0001);
  endtask

  task automatic t_instaddr();
    where = "fpiar";
    if (`BIU_MODEL == 68882) begin
      // The MC68882 (FPU 7.2.10, 6.1.12): the PC only when asked for, and
      // then before anything else.
      wr(5'h18, 4, 32'h0001_2344);
      idle(1);
      check(pv, "MC68882: an unasked PC is a violation");
      wr(5'h02, 2, 32'h0002);
      idle(1);
      seq_resp(16'h4900, 1'b0, E_CMD);
      rd_check(5'h00, 2, 32'h4900, "release, pass the PC");
      wr(5'h18, 4, 32'h0001_2344);
      idle(1);
      check(fpiar == 32'h0001_2344, "the PC asked for writes FPIAR");
      check(!pv, "the PC asked for is no violation");
      rd_check(5'h00, 2, 32'h4900, "the response stays");
      seq_resp(16'h4900, 1'b0, E_CMD);
      rd_check(5'h00, 2, 32'h4900, "release, pass the PC again");
      wr(5'h0A, 2, 32'h4822);
      idle(1);
      check(pv, "MC68882: a command instead of the PC asked for is a violation");
      wr(5'h02, 2, 32'h0002);
      idle(1);
    end else begin
      wr(5'h18, 4, 32'h0001_2344);
      idle(1);
      check(fpiar == 32'h0001_2344, "instruction address CIR writes FPIAR");
      check(!pv, "never a violation (FPU 6.1.12)");
    end
  endtask

  // An 8-bit port reads the response in two cycles; a rewrite between them
  // must not tear the value or lose the new primitive (FPU 10.1.3).
  task automatic t_split_read();
    logic [31:0] b0, b1;
    integer n0;
    where = "split";
    if (port == 8) begin
    seq_resp(16'h9504, 1, E_OPW);
    n0 = n_resp_read;
    cycle(5'h00, 1'b1, 2, 32'd0, b0);
    seq_resp(16'h1234, 0, E_CMD);
    cycle(5'h01, 1'b1, 1, 32'd0, b1);
    check({b0[31:24], b1[31:24]} == 16'h9504, "no tearing");
    idle(1);
    check(n_resp_read == n0, "no event for a torn read of a superseded one-shot");
    rd_check(5'h00, 2, 32'h1234, "the new primitive kept");
    seq_resp(16'h0802, 0, E_CMD);
    end
  endtask

  // The control CIR (FPU 7.2.2). The MC68881 takes any write as an abort that
  // also clears the exception. The MC68882 keeps a floating-point exception's
  // primitive past an exception acknowledge (XA, $0002), aborts on AB ($0001),
  // and acknowledges anything else (F-line here) as the MC68881 does.
  task automatic t_control();
    integer n0;
    where = "control CIR";
    seq_resp(16'h1C32, 0, E_RESP);                  // take pre-instruction, vector 50
    n0 = n_abort;
    wr(5'h02, 2, 32'h0002);                         // XA
    idle(2);
    if (`BIU_MODEL == 68882) begin
      check(n_abort == n0, "MC68882: XA does not abort a floating-point exception");
      rd_check(5'h00, 2, 32'h1C32, "MC68882: the exception stays reported");
      n0 = n_abort_ab;
      wr(5'h02, 2, 32'h0001);                       // AB
      idle(2);
      check(n_abort_ab == n0 + 1, "MC68882: AB ends the instruction in its window");
    end else begin
      check(n_abort == n0 + 1, "MC68881: any control write aborts");
    end
    rd_check(5'h00, 2, 32'h0802, "idle after the abort");
    seq_resp(16'h1C0B, 0, E_RESP);                  // F-line
    n0 = n_abort;
    wr(5'h02, 2, 32'h0002);                         // XA
    idle(2);
    check(n_abort == n0 + 1, "XA clears an F-line on either model");
    rd_check(5'h00, 2, 32'h0802, "idle after the F-line acknowledge");
  endtask

  // RD68885's conversion unit (doc/rd68885.md; tools/iss/biu.py is the
  // definition). The sequencer's side is scripted: apu_run for "the APU
  // computes a released instruction", cu_take for the microcode taking the
  // CU's instruction.
  task automatic seq_pulse(input integer which);    // 0 take, 1 resume, 2 relatch
    @(posedge clk);
    if (which == 0) cu_take <= 1'b1;
    if (which == 1) cu_resume <= 1'b1;
    if (which == 2) relatch <= 1'b1;
    @(posedge clk);
    cu_take <= 1'b0; cu_resume <= 1'b0; relatch <= 1'b0;
    @(negedge clk);
  endtask

  task automatic seq_cu_load(input logic [2:0] k, input logic [31:0] v);
    @(posedge clk);
    cu_idx <= k; cu_data <= v; cu_load <= 1'b1;
    @(posedge clk);
    cu_load <= 1'b0;
    @(negedge clk);
  endtask

  task automatic t_cu();
    integer n0_ab, n0_a;
    where = "CU";
    if (`BIU_MODEL == 68882) begin
      apu_run = 1'b1;
      // FADD.D <ea>,FP1 while the APU computes: the CU answers $1608
      // (figure 7-19), takes the operand, and releases (CA = 0).
      wr(5'h0A, 2, 32'h5422);
      rd_check(5'h00, 2, 32'h1608, "the CU's evaluate <ea>, CA = 0");
      check(!cmd_pend, "the CU took it, not the latch");
      wr(5'h10, 4, 32'h4000_0000);
      wr(5'h10, 4, 32'h0000_0001);
      idle(1);
      check(cu_valid && cu_ready, "held, ready for the APU");
      check(cu_d0 == 32'h4000_0000 && cu_d1 == 32'h0000_0001, "the operand");
      check(cu_w0 == 32'h5422_5F00, $sformatf("the frame word: $%0h", cu_w0));
      // A third instruction is latched (FPU 5.1.1.2).
      wr(5'h0A, 2, 32'h4423);                       // FMUL.S <ea>,FP0
      rd_check(5'h00, 2, 32'h8900, "a third waits");
      check(cmd_pend, "latched");
      // The sequencer takes the CU's; the CU takes the latched one.
      seq_pulse(0);
      idle(2);
      check(!cmd_pend && cu_valid, "the latched one went to the CU");
      rd_check(5'h00, 2, 32'h1504, "its evaluate <ea>, CA = 0");
      wr(5'h10, 4, 32'h3F80_0000);
      idle(1);
      check(cu_ready, "ready");
      seq_pulse(0);
      check(!cu_valid, "taken");
      // FMOVE.L <ea>,FP2: CA = 1, then $8900 until the take, then $0900.
      wr(5'h0A, 2, 32'h4100);
      rd_check(5'h00, 2, 32'h9504, "B, W, L: CA = 1");
      wr(5'h10, 4, 32'h0000_0007);
      rd_check(5'h00, 2, 32'h8900, "waits for the APU");
      check(cu_ready && cu_mid, "ready, still in its dialog");
      seq_pulse(0);
      rd_check(5'h00, 2, 32'h0900, "released at the take");
      // The PC (FPU 7.2.10): register to register with an exception
      // enabled; the PC goes with the instruction, to FPIAR at the take.
      pcen = 1'b1;
      wr(5'h0A, 2, 32'h0422);
      rd_check(5'h00, 2, 32'h4900, "release, pass the PC");
      check(!cu_ready, "not before its PC");
      wr(5'h18, 4, 32'h0000_2468);
      idle(1);
      check(cu_ready && cu_w1 == 32'h0000_2468, "with its PC");
      check(fpiar != 32'h0000_2468, "FPIAR is the APU's");
      seq_pulse(0);
      idle(1);
      check(fpiar == 32'h0000_2468, "the PC reaches FPIAR at the take");
      pcen = 1'b0;
      // AB in the CU's dialog (FPU 7.2.2): only its instruction ends.
      wr(5'h0A, 2, 32'h4822);
      rd_check(5'h00, 2, 32'h160C, "X: CA = 0");
      n0_ab = n_abort_ab; n0_a = n_abort;
      wr(5'h02, 2, 32'h0001);
      idle(2);
      check(!cu_valid && n_abort == n0_a && n_abort_ab == n0_ab, "the CU's instruction alone ends");
      rd_check(5'h00, 2, 32'h0900, "the APU still computes");
      // FRESTORE of a busy frame with the CU mid-transfer: its words back,
      // then its dialog (cu_resume).
      apu_run = 1'b0;
      seq_cu_load(3'd0, 32'h5422_1F01);              // FADD.D, one long word to come
      seq_cu_load(3'd2, 32'h4000_0000);
      seq_pulse(1);
      rd_check(5'h00, 2, 32'h0900, "the CU's dialog back");
      wr(5'h10, 4, 32'h0000_0001);
      idle(1);
      check(cu_ready && cu_d1 == 32'h0000_0001, "completed");
      seq_pulse(0);
      // An empty CU in a frame is all ones.
      seq_cu_load(3'd0, 32'hFFFF_FFFF);
      check(!cu_valid && cu_w0 == 32'hFFFF_FFFF, "empty");
      // RELATCH: a pending instruction back in the latch.
      relatch_word = 16'h4422; relatch_cond = 1'b0;
      seq_pulse(2);
      check(cmd_pend && cmd_word == 16'h4422, "re-latched");
      rd_check(5'h00, 2, 32'h8900, "waiting");
      ack_cmd();
      seq_resp(16'h0802, 1'b0, E_CMD);
    end
  endtask

  task automatic t_arch_reset();
    where = "reset";
    seq_resp(16'h9504, 1, E_OPW);
    @(negedge `BCLK);
    reset_n = 1'b0;
    repeat (4) @(negedge `BCLK);
    check(arch_reset, "architectural reset seen");
    reset_n = 1'b1;
    repeat (4) @(negedge `BCLK);
    rd_check(5'h00, 2, 32'h0802, "idle after RESET");
    check(fpiar == 0, "FPIAR cleared");
  endtask

  // M4 additions: the conditional end-of-instruction write, the register
  // select direction, the frame transfer count, and clear.
  task automatic seq_resp_cond(input logic [15:0] v);
    @(posedge clk);
    resp_v <= v; resp_oneshot <= 1'b0; expect_v <= E_CMD; resp_cond <= 1'b1; resp_we <= 1'b1;
    @(posedge clk);
    resp_we <= 1'b0; resp_cond <= 1'b0;
    @(negedge clk);
  endtask

  task automatic t_m4();
    logic [31:0] v;
    where = "m4";
    // A command latched before the sequencer's final null keeps its $8900.
    seq_resp(16'h0900, 0, E_CMD);
    wr(5'h0A, 2, 32'h0000);
    seq_resp_cond(16'h0802);
    rd_check(5'h00, 2, 32'h8900, "conditional null dropped under a command");
    ack_cmd();
    seq_resp_cond(16'h0802);
    rd_check(5'h00, 2, 32'h0802, "conditional null taken when idle");
    // Register select then operand writes, no sequencer in between.
    @(posedge clk); rsel_v <= 8'h80; rsel_dir <= 1'b0; rsel_we <= 1'b1;
    @(posedge clk); rsel_we <= 1'b0;
    seq_resp(16'h810C, 1, E_RSEL);
    rd_check(5'h00, 2, 32'h810C, "transfer multiple");
    rd_check(5'h14, 2, 32'h8000, "register select");
    wr(5'h10, 4, 32'h1);
    check(!pv, "operand write right after the register select is expected");
    ack_opw();
    seq_resp(16'h0802, 0, E_CMD);
    // A save frame of three long words.
    @(posedge clk); save_v <= 16'h1F0C; save_xfer <= 6'd3; save_we <= 1'b1;
    @(posedge clk); save_we <= 1'b0;
    rd_check(5'h04, 2, 32'h1F0C, "format word");
    seq_opr(32'hA);
    rd_check(5'h10, 4, 32'hA, "frame long word 1");
    rd_check(5'h04, 2, 32'h0218, "a save during the transfer is invalid");
    seq_opr(32'hB);
    rd_check(5'h10, 4, 32'hB, "frame long word 2");
    seq_opr(32'hC);
    rd_check(5'h10, 4, 32'hC, "frame long word 3");
    wr(5'h0A, 2, 32'h0000);
    idle(1);
    check(!pv && cmd_pend, "a command expected after the last long word");
    ack_cmd();
    seq_resp(16'h0802, 0, E_CMD);
    // A restore frame of two long words.
    wr(5'h06, 2, 32'h1F08);
    @(posedge clk); restore_v <= 16'h1F08; restore_xfer <= 6'd2; restore_we <= 1'b1;
    @(posedge clk); restore_we <= 1'b0;
    rd_check(5'h06, 2, 32'h1F08, "restore read-back");
    wr(5'h10, 4, 32'h1);
    ack_opw();
    wr(5'h10, 4, 32'h2);
    ack_opw();
    wr(5'h0E, 2, 32'h0001);
    idle(1);
    check(!pv && cmd_pend && cmd_cond, "a command expected after the restore frame");
    ack_cmd();
    // Clear.
    wr(5'h10, 4, 32'h0);
    idle(1);
    check(pv, "violation");
    @(posedge clk); clear <= 1'b1;
    @(posedge clk); clear <= 1'b0;
    idle(1);
    check(!pv, "clear");
    rd_check(5'h00, 2, 32'h0802, "idle after clear");
  endtask

  // ==========================================================================
  // The run
  // ==========================================================================
  // Core and bus periods, ns: 3x, 2.5x, 2x, 2.3x the bus clock, and a core
  // no faster than the bus (only wait states may differ; FPU 10.5).
  function automatic real core_of(input integer c);
    core_of = (c == 3) ? 13.0 : (c == 4) ? 61.3 : 20.0;
  endfunction
  function automatic real bus_of(input integer c);
    bus_of = (c == 0) ? 60.0 : (c == 1) ? 50.0 : (c == 2) ? 39.7 : (c == 3) ? 30.0 : 60.0;
  endfunction

`ifdef BIU_SYNC
  // One clock: the MC68020's at 16.67, 20, 25 and 33.33 MHz. The strobes
  // change a quarter period after a falling edge, the write data a sixth after
  // a rising one. With one wait state, a fifth run at 16.67 MHz has the
  // strobes 0.7 of a period after the falling edge: past the rising edge.
  localparam int NCLK = (`BIU_SYNC_WAIT != 0) ? 5 : 4;
  function automatic real sync_of(input integer c);
    sync_of = (c == 0) ? 60.0 : (c == 1) ? 50.0 : (c == 2) ? 40.0 : (c == 3) ? 30.0 : 60.0;
  endfunction
`else
  localparam int NCLK = 5;
`endif


  initial begin
    for (int c = 0; c < NCLK; c++) begin
      for (int p = 0; p < 3; p++) begin
`ifdef BIU_SYNC
        core_ns   = sync_of(c);
        bus_ns    = core_ns;
        strobe_ns = (c == 4) ? core_ns * 0.7 : core_ns / 4.0;
        data_ns   = core_ns / 6.0;
        port      = (p == 0) ? 32 : (p == 1) ? 16 : 8;
        $display("biu_tb: port %0d bits, one clock %.1f ns, strobes %.1f ns late, BUS_SYNC_WAIT %0d",
                 port, core_ns, strobe_ns, `BIU_SYNC_WAIT);
`else
        core_ns = core_of(c);
        bus_ns  = bus_of(c);
        port    = (p == 0) ? 32 : (p == 1) ? 16 : 8;
        $display("biu_tb: port %0d bits, core %.1f ns, bus %.1f ns", port, core_ns, bus_ns);
`endif
        do_reset();
        t_idle_and_reserved();
        t_command_and_oneshot();
        t_resp_hold();
        t_backpressure();
        t_short_operands();
        t_operand_read();
        t_regsel();
        t_violations();
        t_save_restore();
        t_instaddr();
        t_split_read();
        t_m4();
        t_control();
        t_cu();
        t_arch_reset();
      end
    end
`ifdef BIU_SYNC
    $display("biu_tb: %0d same-clock cycles, %0d wait states in all", sync_cycles, sync_waits);
`endif
`ifdef BIU_NEGATE
    $display("biu_tb: DSACK actively negated %0d times", negations);
    check(negations > 100, "DSACK is actively negated (FPU 9.8)");
`endif
    if (errors == 0) $display("PASS: biu_tb, %0d checks", checks);
    else             $display("FAIL: biu_tb, %0d of %0d checks failed", errors, checks);
    $finish;
  end

  initial begin
    #50_000_000;
    $display("FAIL: biu_tb timed out");
    $finish;
  end

endmodule
