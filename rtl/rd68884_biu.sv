// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 - SystemVerilog MC68881 floating-point coprocessor
//
// Bus interface unit: the MC68881's asynchronous bus (FPU section 10) and its
// coprocessor interface registers (FPU 7.2), on one side; a register-level
// interface to the sequencer on the other.
//
// THE PINS. Every cycle is answered asynchronously (FPU 10.4.2/10.4.3); the
// optional synchronous read timing of the response and save CIRs is not
// reproduced, which the protocol allows: the main processor only waits for
// DSACK. A cycle starts on START = CS * AS * (DS + /RW) (section 12, note 8)
// and ends on the first strobe negated. The strobes are synchronised into the
// core clock; the DSACK and data enables are gated combinationally by the raw
// START term, so they let go of the bus the instant START falls (specifications
// 16, 21) whatever the core clock is doing. DSACK is then floated rather than
// actively driven high first (FPU 9.8): doc/divergences.md.
//
// A cycle that ends while the core has not yet seen it end must not leave its
// acknowledge armed for the next one. `stale` is set asynchronously the moment
// START falls and cleared by the core once the cycle is retired; the enables
// need it clear, and the core takes its synchronised copy as the end of the
// cycle -- so even a DS-negated gap shorter than a core clock (specification
// 13) is seen.
//
// PORT SIZE. FPU table 9-2 and figure 10-2. Each byte of a CIR always travels
// on its natural lane of the 32-bit register -- byte 0 (most significant) on
// D31-D24 -- and the port size only chooses which lanes are enabled; the board
// ties the lanes of an 8- or 16-bit port together (FPU 11). So there is no data
// multiplexing: lanes are enabled per access, and a register is complete when
// the access carrying its least significant byte is (the "least significant
// byte" of FPU 10.5).
//
// THE REGISTERS. What every CIR access does is decided here, without the
// sequencer, so the main processor gets a correct answer while the sequencer
// is deep in a computation:
//   - a command or condition write is latched and the response becomes null
//     (CA=1, IA=1), FPU 7.2.6/7.2.7;
//   - a save read with no format word prepared answers come-again, FPU 6.4.3;
//   - an access the current expectation forbids is a protocol violation and the
//     response becomes $1D0D at once, FPU 6.1.12;
//   - a control write aborts, FPU 7.2.2;
//   - one-shot primitives revert to null (CA=1) once read, FPU 7.4.2.2-7.4.2.4;
//   - reads of write-only, reserved and unimplemented CIRs return all ones and
//     their writes are ignored, FPU 7.2.
// An operand access the sequencer is not ready for is held off by withholding
// DSACK, which FPU 10.5 allows.
//
// The golden model of all of this is tools/model/cpif.py.

module rd68884_biu (
    input  logic        clk,
    input  logic        rst_n,

    // ---- pins (doc/pinout.md) ----------------------------------------------
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
    output logic        dsack_oe,

    // ---- sequencer side ------------------------------------------------------
    // The architectural RESET pin, synchronised (FPU 9.9).
    output logic        arch_reset_o,

    // Writing the response CIR. With oneshot set the primitive is read once,
    // then becomes null (CA=1, IA=1) and the expectation becomes expect_i;
    // without it, the expectation becomes expect_i at once and the primitive
    // stays until replaced. Expectations: rd68884_pkg::EXP_*.
    input  logic        resp_we_i,
    input  logic [15:0] resp_i,
    input  logic        resp_oneshot_i,
    input  logic [2:0]  expect_i,

    // A command or condition word, held until the sequencer takes it.
    output logic        cmd_pend_o,
    output logic        cmd_cond_o,
    output logic [15:0] cmd_word_o,
    input  logic        cmd_ack_i,

    // Operand CIR, main processor to FPU: one long word (or the left-aligned
    // byte or word of an immediate), held until the sequencer takes it.
    output logic        opw_valid_o,
    output logic [31:0] opw_data_o,
    input  logic        opw_ack_i,

    // Operand CIR, FPU to main processor: the next long word to be read.
    input  logic        opr_we_i,
    input  logic [31:0] opr_i,
    output logic        opr_valid_o,

    // Register select CIR (FPU 7.2.9): the FMOVEM mask.
    input  logic        rsel_we_i,
    input  logic [7:0]  rsel_i,

    // Save CIR: the format word to answer with; until one is written a save
    // read answers come-again and raises save_req_o (FPU 6.4.3).
    input  logic        save_we_i,
    input  logic [15:0] save_i,
    output logic        save_req_o,

    // Restore CIR: the format word written, and the validation to read back.
    output logic        restore_req_o,
    output logic [15:0] restore_word_o,
    input  logic        restore_we_i,
    input  logic [15:0] restore_i,

    // FPIAR lives here: the instruction address CIR writes it (FPU 7.2.10).
    output logic [31:0] fpiar_o,
    input  logic        fpiar_we_i,
    input  logic [31:0] fpiar_i,

    // Events, one clock each.
    output logic        resp_read_o,     // a one-shot primitive was read
    output logic        rsel_read_o,     // the register select CIR was read
    output logic        save_read_o,     // a prepared format word was read
    output logic        abort_o,         // the control CIR was written
    output logic        pv_o             // a protocol violation is pending
);

  // ==========================================================================
  // Synchronisers
  // ==========================================================================
  // {reset, cs, as, ds, rw}, all as they are at the pin.
  logic [4:0] pins_q;

  rd68884_sync #(
      .WIDTH    (5),
      .RESET_VAL(5'b11111)
  ) u_sync_pins (
      .clk  (clk),
      .rst_n(rst_n),
      .d    ({reset_n_i, cs_n_i, as_n_i, ds_n_i, rw_i}),
      .q    (pins_q)
  );

  logic reset_s, cs_s, as_s, ds_s, rd_s;
  assign reset_s = ~pins_q[4];
  assign cs_s    = ~pins_q[3];
  assign as_s    = ~pins_q[2];
  assign ds_s    = ~pins_q[1];
  assign rd_s    =  pins_q[0];
  assign arch_reset_o = reset_s;

  // START, FPU 10.3: "A cycle start is detected when AS, CS, and DS or R/W (for
  // a write cycle) are asserted."
  logic start_raw;
  assign start_raw = ~cs_n_i & ~as_n_i & (~ds_n_i | ~rw_i);

  logic start_s;
  assign start_s = cs_s & as_s & (ds_s | ~rd_s);

  // ==========================================================================
  // The stale guard (see the header)
  // ==========================================================================
  logic stale;
  logic stale_set;
  logic stale_clr;
  assign stale_set = ~start_raw | ~rst_n;

  always_ff @(posedge clk or posedge stale_set) begin
    if (stale_set) begin
      stale <= 1'b1;
    end else if (stale_clr) begin
      stale <= 1'b0;
    end
  end

  logic stale_s;
  rd68884_sync #(
      .WIDTH    (1),
      .RESET_VAL(1'b1)
  ) u_sync_stale (
      .clk  (clk),
      .rst_n(rst_n),
      .d    (stale),
      .q    (stale_s)
  );

  // ==========================================================================
  // Decoding an access
  // ==========================================================================
  // Sampled once the synchronised START is seen: the address, R/W and the
  // port-size straps have been stable since before AS (specification 6), the
  // write data since before DS (specification 17).
  logic [4:0]  a_q;
  logic        rd_q;
  logic        size_n_q;

  // CIR identities. A4 = 0: sixteen-bit registers, A3-A1 select. A4 = 1:
  // A3-A2 select a 32-bit register (the register select CIR and its reserved
  // neighbour share one).
  logic is_resp, is_ctrl, is_save, is_rest, is_cmd, is_cond;
  logic is_oper, is_rsel, is_iar;
  always_comb begin
    is_resp = ~a_q[4] & (a_q[3:1] == 3'b000);
    is_ctrl = ~a_q[4] & (a_q[3:1] == 3'b001);
    is_save = ~a_q[4] & (a_q[3:1] == 3'b010);
    is_rest = ~a_q[4] & (a_q[3:1] == 3'b011);
    is_cmd  = ~a_q[4] & (a_q[3:1] == 3'b101);
    is_cond = ~a_q[4] & (a_q[3:1] == 3'b111);
    is_oper =  a_q[4] & (a_q[3:2] == 2'b00);
    is_rsel =  a_q[4] & (a_q[3:2] == 2'b01);
    is_iar  =  a_q[4] & (a_q[3:2] == 2'b10);
  end

  // Lanes, bit 3 = D31-D24. FPU figure 10-2.
  logic [3:0] lanes;
  always_comb begin
    if (size_n_q && a_q[0]) begin                     // 32-bit port
      lanes = a_q[4] ? 4'b1111 : 4'b1100;
    end else if (size_n_q) begin                      // 16-bit port
      lanes = (a_q[4] & a_q[1]) ? 4'b0011 : 4'b1100;
    end else if (a_q[4]) begin                        // 8-bit port
      lanes = 4'b1000 >> a_q[1:0];
    end else begin
      lanes = a_q[0] ? 4'b0100 : 4'b1000;
    end
  end

  // DSACK1/DSACK0, FPU table 9-3, bit 1 = DSACK1, active low.
  // An assignment, not an always_comb: iverilog 12 dies in code generation
  // (ivl_nexus_ptrs assertion) on the equivalent if/else chain. Measured;
  // doc/coding-standard.md.
  logic [1:0] dsack_val;
  assign dsack_val = !size_n_q              ? 2'b10 :     // 8-bit
                     (a_q[0] && a_q[4])     ? 2'b00 :     // 32-bit
                                              2'b01;      // 16-bit

  // Short operands: FPU 10.1 -- an immediate byte or word is one transfer,
  // left-aligned. Taken from the length field of the evaluate-effective-
  // address primitive the sequencer issued.
  logic [1:0] oplen_q;            // 0 long, 1 byte, 2 word

  // Which lane carries the register's least significant byte, and whether this
  // access carries its most significant one.
  logic last_part;
  logic first_part;
  always_comb begin
    first_part = lanes[3];
    if (!a_q[4] || is_rsel) begin
      last_part = lanes[2];                           // a 16-bit register
    end else if (is_oper && oplen_q == 2'd1) begin
      last_part = lanes[3];
    end else if (is_oper && oplen_q == 2'd2) begin
      last_part = lanes[2];
    end else begin
      last_part = lanes[0];
    end
  end

  // ==========================================================================
  // The registers the main processor sees
  // ==========================================================================
  logic [15:0] resp_q;
  logic        oneshot_q;
  logic [2:0]  expect_q;
  logic [2:0]  expect_next_q;
  logic        resp_dirty_q;     // the sequencer rewrote resp since the snapshot
  logic        pv_q;
  logic        cmd_pend_q;
  logic        cmd_cond_q;
  logic [15:0] cmd_word_q;
  logic        opw_valid_q;
  logic [31:0] opw_data_q;
  logic        opr_valid_q;
  logic [31:0] opr_q;
  logic [7:0]  rsel_q;
  logic        save_valid_q;
  logic [15:0] save_q;
  logic        save_req_q;
  logic        restore_req_q;
  logic        restore_valid_q;
  logic [15:0] restore_word_q;
  logic [15:0] restore_q;
  logic [31:0] fpiar_q;
  logic [31:0] wstage_q;         // write bytes collected so far
  logic [31:0] hold_q;           // read value snapshotted on the first part
  logic        hold_save_q;      // ... the save read it was a format word

  assign cmd_pend_o     = cmd_pend_q;
  assign cmd_cond_o     = cmd_cond_q;
  assign cmd_word_o     = cmd_word_q;
  assign opw_valid_o    = opw_valid_q;
  assign opw_data_o     = opw_data_q;
  assign opr_valid_o    = opr_valid_q;
  assign save_req_o     = save_req_q;
  assign restore_req_o  = restore_req_q;
  assign restore_word_o = restore_word_q;
  assign fpiar_o        = fpiar_q;
  assign pv_o           = pv_q;

  // What a read returns, by register, before any snapshot.
  logic [31:0] rd_word;
  always_comb begin
    rd_word = 32'hFFFF_FFFF;
    if (is_resp) begin
      rd_word = {resp_q, 16'hFFFF};
    end else if (is_save) begin
      rd_word = {(save_valid_q ? save_q : rd68884_pkg::FRAME_COME_AGAIN), 16'hFFFF};
    end else if (is_rest) begin
      rd_word = {restore_q, 16'hFFFF};
    end else if (is_oper) begin
      // An operand read the dialog does not expect is a protocol violation
      // and gets all ones, as tools/model/cpif.py (FPU 6.1.12 only says the
      // data is inconsistent).
      rd_word = (expect_q == rd68884_pkg::EXP_OPR && !pv_q) ? opr_q : 32'hFFFF_FFFF;
    end else if (is_rsel) begin
      // FPU 7.2.9: the low byte reads as zeros; 10.1.1: bits 15-0 driven high.
      rd_word = {rsel_q, 8'h00, 16'hFFFF};
    end
  end

  // The protocol-violation rules of FPU 6.1.12, for the MC68881, against the
  // current expectation. Only accesses that touch a checked register count.
  logic touches_rsel;
  logic violation;
  always_comb begin
    // $14/$15 are the register select CIR, $16/$17 its reserved neighbour:
    // A1 tells them apart on every port (a 32-bit port enables all lanes).
    touches_rsel = is_rsel & ~a_q[1];
    violation = 1'b0;
    if ((is_cmd || is_cond) && !rd_q) begin
      violation = expect_q != rd68884_pkg::EXP_CMD;
    end else if (is_oper && !rd_q) begin
      violation = expect_q != rd68884_pkg::EXP_OPW;
    end else if (is_oper && rd_q) begin
      violation = expect_q != rd68884_pkg::EXP_OPR;
    end else if (touches_rsel && rd_q) begin
      violation = expect_q != rd68884_pkg::EXP_RSEL;
    end else if (touches_rsel && !rd_q) begin
      violation = 1'b1;                       // the one write that always is
    end
  end

  // Can this access complete now? An operand access waits for the sequencer
  // (FPU 10.5), as does the read-back of a restore format word.
  logic ready;
  always_comb begin
    ready = 1'b1;
    if (!violation && !pv_q) begin
      if (is_oper && rd_q) begin
        ready = opr_valid_q;                  // every part reads opr_q
      end else if (is_oper && !rd_q && last_part) begin
        ready = ~opw_valid_q;
      end else if (is_rest && rd_q) begin
        ready = restore_valid_q;
      end
    end
  end

  // ==========================================================================
  // The cycle state machine
  // ==========================================================================
  typedef enum logic [1:0] {
    S_IDLE,
    S_DECODE,
    S_ACK
  } cyc_e;

  cyc_e state;
  logic ack_q;
  logic [3:0] lanes_q;
  logic [1:0] dsack_q;

  // The access completes in the clock it is acknowledged.
  logic take;
  assign take = (state == S_DECODE) && (rd_q || ds_s) && ready;

  assign stale_clr = (state == S_IDLE);

  logic [31:0] wmerged;          // the register as the last part completes it
  logic [31:0] lane_mask;
  always_comb begin
    lane_mask = {{8{lanes[3]}}, {8{lanes[2]}}, {8{lanes[1]}}, {8{lanes[0]}}};
    wmerged   = (wstage_q & ~lane_mask) | (d_i & lane_mask);
  end

  // The value a read drives: the snapshot, unless this part is the first.
  logic [31:0] rd_out;
  assign rd_out = first_part ? rd_word : hold_q;

  // Events of the completing access.
  logic ev_resp, ev_rsel, ev_save, ev_save_again, ev_abort, ev_cmd, ev_opw;
  logic ev_opr, ev_rest_w, ev_iar, ev_pv;
  always_comb begin
    ev_resp = 1'b0; ev_rsel = 1'b0; ev_save = 1'b0; ev_save_again = 1'b0;
    ev_abort = 1'b0; ev_cmd = 1'b0; ev_opw = 1'b0; ev_opr = 1'b0;
    ev_rest_w = 1'b0; ev_iar = 1'b0; ev_pv = 1'b0;
    if (take && last_part) begin
      if (violation && !pv_q) begin
        ev_pv = 1'b1;
      end else if (!violation && !pv_q) begin
        ev_resp       = is_resp & rd_q;
        ev_rsel       = touches_rsel & rd_q;
        ev_save       = is_save & rd_q & (first_part ? save_valid_q : hold_save_q);
        ev_save_again = is_save & rd_q & ~(first_part ? save_valid_q : hold_save_q);
        ev_cmd        = (is_cmd | is_cond) & ~rd_q;
        ev_opw        = is_oper & ~rd_q;
        ev_opr        = is_oper & rd_q;
        ev_rest_w     = is_rest & ~rd_q;
        ev_iar        = is_iar & ~rd_q;
      end
      // FPU 7.2.2: a control write is never illegal, and with a violation
      // pending it is the acknowledge that clears it. A save read and the
      // response read stay legal too (FPU 7.2.1, 7.2.3).
      ev_abort = is_ctrl & ~rd_q;
      if (pv_q) begin
        ev_save       = is_save & rd_q & (first_part ? save_valid_q : hold_save_q);
        ev_save_again = is_save & rd_q & ~(first_part ? save_valid_q : hold_save_q);
        ev_rest_w     = is_rest & ~rd_q;
        ev_iar        = is_iar & ~rd_q;
      end
    end
  end

  // The primitive just read is the current one unless the sequencer rewrote
  // it between the parts of a split read (an 8-bit port reads it in two).
  logic resp_stale;
  assign resp_stale = ~first_part & resp_dirty_q;

  assign resp_read_o = ev_resp & oneshot_q & ~resp_stale & ~resp_we_i;
  assign rsel_read_o = ev_rsel;
  assign save_read_o = ev_save;
  assign abort_o     = ev_abort;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state           <= S_IDLE;
      ack_q           <= 1'b0;
      a_q             <= 5'd0;
      rd_q            <= 1'b1;
      size_n_q        <= 1'b1;
      lanes_q         <= 4'd0;
      dsack_q         <= 2'b11;
      d_o             <= 32'd0;
      wstage_q        <= 32'd0;
      hold_q          <= 32'hFFFF_FFFF;
      hold_save_q     <= 1'b0;
      resp_q          <= rd68884_pkg::PRIM_NULL_IDLE;
      oneshot_q       <= 1'b0;
      expect_q        <= rd68884_pkg::EXP_CMD;
      expect_next_q   <= rd68884_pkg::EXP_CMD;
      resp_dirty_q    <= 1'b0;
      pv_q            <= 1'b0;
      cmd_pend_q      <= 1'b0;
      cmd_cond_q      <= 1'b0;
      cmd_word_q      <= 16'd0;
      opw_valid_q     <= 1'b0;
      opw_data_q      <= 32'd0;
      opr_valid_q     <= 1'b0;
      opr_q           <= 32'hFFFF_FFFF;
      oplen_q         <= 2'd0;
      rsel_q          <= 8'd0;
      save_valid_q    <= 1'b0;
      save_q          <= 16'd0;
      save_req_q      <= 1'b0;
      restore_req_q   <= 1'b0;
      restore_valid_q <= 1'b0;
      restore_word_q  <= 16'd0;
      restore_q       <= 16'hFFFF;
      fpiar_q         <= 32'd0;
    end else begin
      // ---- the bus cycle --------------------------------------------------
      case (state)
        S_IDLE: begin
          ack_q <= 1'b0;
          if (start_s && !stale_s) begin
            a_q      <= a_i;
            rd_q     <= rd_s;
            size_n_q <= size_n_i;
            state    <= S_DECODE;
          end
        end
        S_DECODE: begin
          if (stale_s) begin
            state <= S_IDLE;                  // gone before it could be answered
          end else if (take) begin
            ack_q   <= 1'b1;
            lanes_q <= lanes;
            dsack_q <= dsack_val;
            d_o     <= rd_out;
            if (first_part) begin
              hold_q      <= rd_word;
              hold_save_q <= save_valid_q;
            end
            if (!rd_q) begin
              wstage_q <= wmerged;
            end
            state <= S_ACK;
          end
        end
        default: begin                        // S_ACK, until the cycle ends
          if (stale_s) begin
            ack_q <= 1'b0;
            state <= S_IDLE;
          end
        end
      endcase

      // ---- what the completing access does ------------------------------
      // The sequencer's writes come first in the source and the bus events
      // after them, so that in a clock where both touch a register the bus
      // wins only where it should (see each).
      if (resp_we_i) begin
        resp_q       <= resp_i;
        oneshot_q    <= resp_oneshot_i;
        resp_dirty_q <= 1'b1;
        if (resp_oneshot_i) begin
          expect_q      <= rd68884_pkg::EXP_RESP;
          expect_next_q <= expect_i;
        end else begin
          expect_q <= expect_i;
        end
        // FPU 10.1: the length field of an evaluate-effective-address
        // primitive (bit 12 set, bit 11 clear) gives the operand size.
        if (resp_i[12:11] == 2'b10 && resp_i[7:0] == 8'd1) begin
          oplen_q <= 2'd1;
        end else if (resp_i[12:11] == 2'b10 && resp_i[7:0] == 8'd2) begin
          oplen_q <= 2'd2;
        end else begin
          oplen_q <= 2'd0;
        end
      end
      if (take && first_part && is_resp) begin
        resp_dirty_q <= 1'b0;
      end
      if (cmd_ack_i)  cmd_pend_q  <= 1'b0;
      if (opw_ack_i)  opw_valid_q <= 1'b0;
      if (opr_we_i) begin
        opr_q       <= opr_i;
        opr_valid_q <= 1'b1;
      end
      if (rsel_we_i) rsel_q <= rsel_i;
      if (save_we_i) begin
        save_q       <= save_i;
        save_valid_q <= 1'b1;
        save_req_q   <= 1'b0;
      end
      if (restore_we_i) begin
        restore_q       <= restore_i;
        restore_valid_q <= 1'b1;
        restore_req_q   <= 1'b0;
      end
      if (fpiar_we_i) fpiar_q <= fpiar_i;

      // A one-shot primitive becomes null once read, unless the sequencer
      // replaced it in the meantime (FPU 7.4.2.2).
      if (resp_read_o) begin
        resp_q    <= rd68884_pkg::PRIM_NULL_WAIT;
        oneshot_q <= 1'b0;
        expect_q  <= expect_next_q;
      end
      if (ev_cmd) begin
        // FPU 7.2.6/7.2.7: latched; null (CA=1, IA=1) until the sequencer
        // answers. Another command before then is a violation.
        cmd_pend_q <= 1'b1;
        cmd_cond_q <= is_cond;
        cmd_word_q <= wmerged[31:16];
        resp_q     <= rd68884_pkg::PRIM_NULL_WAIT;
        oneshot_q  <= 1'b0;
        expect_q   <= rd68884_pkg::EXP_RESP;
      end
      if (ev_opw) begin
        opw_data_q  <= wmerged;
        opw_valid_q <= 1'b1;
      end
      if (ev_opr) begin
        opr_valid_q <= 1'b0;
      end
      if (ev_save) begin
        save_valid_q <= 1'b0;
      end
      if (ev_save_again) begin
        save_req_q <= 1'b1;
      end
      if (ev_rest_w) begin
        restore_word_q  <= wmerged[31:16];
        restore_req_q   <= 1'b1;
        restore_valid_q <= 1'b0;
      end
      if (ev_iar) begin
        fpiar_q <= wmerged;
      end
      if (ev_pv) begin
        // FPU 6.1.12: the take-mid-instruction primitive with the protocol
        // violation vector, at once.
        pv_q      <= 1'b1;
        resp_q    <= rd68884_pkg::PRIM_PROTOCOL;
        oneshot_q <= 1'b0;
      end
      if (ev_abort || reset_s) begin
        // FPU 7.2.2: terminate, clear pending exceptions (the sequencer's
        // business), reset the BIU to idle.
        pv_q            <= 1'b0;
        resp_q          <= rd68884_pkg::PRIM_NULL_IDLE;
        oneshot_q       <= 1'b0;
        expect_q        <= rd68884_pkg::EXP_CMD;
        cmd_pend_q      <= 1'b0;
        opw_valid_q     <= 1'b0;
        opr_valid_q     <= 1'b0;
        save_valid_q    <= 1'b0;
        save_req_q      <= 1'b0;
        restore_req_q   <= 1'b0;
        restore_valid_q <= 1'b0;
      end
      if (reset_s) begin
        fpiar_q <= 32'd0;                   // FPU 2.4: cleared by reset
      end
    end
  end

  // ==========================================================================
  // The pins
  // ==========================================================================
  logic drive;
  assign drive     = ack_q & start_raw & ~stale;
  assign dsack_oe  = drive;
  assign dsack_n_o = drive ? dsack_q : 2'b11;
  assign d_oe      = (drive & rd_q) ? lanes_q : 4'b0000;

  logic unused_x;
  assign unused_x = &{1'b1, hold_q[15:0]};

endmodule
