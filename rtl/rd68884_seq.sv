// SPDX-License-Identifier: CERN-OHL-S-2.0
// Copyright 2026 Romain Dolbeau
// Source location: https://github.com/MelkhiorVintageComputing/RD68884

// RD68884 - SystemVerilog MC68881 floating-point coprocessor
//
// The microsequencer and its datapath. One microinstruction per clock; every
// action reads the state as it was at the start of the clock (doc/microcode.md).
//
// tools/iss/core.py is the executable definition of every field and this is
// its transcription; tools/iss/lockstep.py runs the two side by side on the
// same BIU signals and compares them clock by clock. The microcode is
// tools/ucode/program.py, the field table tools/ucode/fields.py, and both
// rd68884_ucode_pkg and rd68884_ucode_rom are generated from them.
//
// M4 datapath: the 32-bit transfer bus, the extended-format image XI, the
// working value A, the register file, FPCR and FPSR, the FMOVEM mask, the
// predicate evaluator. The arithmetic arrives with M5.

module rd68884_seq (
    input  logic        clk,
    input  logic        rst_n,

    // ---- from the BIU --------------------------------------------------------
    input  logic        arch_reset_i,
    input  logic        cmd_pend_i,
    input  logic        cmd_cond_i,
    input  logic [15:0] cmd_word_i,
    input  logic        opw_valid_i,
    input  logic [31:0] opw_data_i,
    input  logic        opr_valid_i,
    input  logic        save_req_i,
    input  logic        restore_req_i,
    input  logic [15:0] restore_word_i,
    input  logic [31:0] fpiar_i,
    input  logic        pv_i,
    input  logic        resp_read_i,
    input  logic        rsel_read_i,
    input  logic        save_read_i,
    input  logic        abort_i,

    // ---- to the BIU ------------------------------------------------------------
    output logic        resp_we_o,
    output logic [15:0] resp_o,
    output logic        resp_oneshot_o,
    output logic [2:0]  expect_o,
    output logic        resp_cond_o,
    output logic        cmd_ack_o,
    output logic        opw_ack_o,
    output logic        opr_we_o,
    output logic [31:0] opr_o,
    output logic        rsel_we_o,
    output logic [7:0]  rsel_o,
    output logic        rsel_dir_o,
    output logic        save_we_o,
    output logic [15:0] save_o,
    output logic [5:0]  save_xfer_o,
    output logic        restore_we_o,
    output logic [15:0] restore_o,
    output logic [5:0]  restore_xfer_o,
    output logic        fpiar_we_o,
    output logic [31:0] fpiar_o,
    output logic        clear_o
);

  // ==========================================================================
  // The microword
  // ==========================================================================
  logic [10:0] upc;
  logic [10:0] addr_nxt;
  logic [83:0] uw;

  rd68884_ucode_rom u_rom (
      .clk  (clk),
      .rst_n(rst_n),
      .addr (addr_nxt),
      .uw   (uw)
  );

  logic [2:0]  f_seq;
  logic        f_neg;
  logic [4:0]  f_cond;
  logic [1:0]  f_idx;
  logic [10:0] f_tgt;
  logic [15:0] f_imm;
  logic [1:0]  f_resp;
  logic [2:0]  f_ors;
  logic        f_oneshot;
  logic [2:0]  f_expect;
  logic [3:0]  f_biu;
  logic [5:0]  f_xfer;
  logic [3:0]  f_tsrc;
  logic [3:0]  f_tdst;
  logic [1:0]  f_rf;
  logic [2:0]  f_rfa;
  logic [2:0]  f_asrc;
  logic        f_xop;
  logic [1:0]  f_fpsr;
  logic [1:0]  f_ctr;
  logic [1:0]  f_mask;
  logic [3:0]  f_flag;

  always_comb begin
    f_seq     = uw[rd68884_ucode_pkg::F_SEQ_LSB     +: 3];
    f_neg     = uw[rd68884_ucode_pkg::F_NEG_LSB];
    f_cond    = uw[rd68884_ucode_pkg::F_COND_LSB    +: 5];
    f_idx     = uw[rd68884_ucode_pkg::F_IDX_LSB     +: 2];
    f_tgt     = uw[rd68884_ucode_pkg::F_TGT_LSB     +: 11];
    f_imm     = uw[rd68884_ucode_pkg::F_IMM_LSB     +: 16];
    f_resp    = uw[rd68884_ucode_pkg::F_RESP_LSB    +: 2];
    f_ors     = uw[rd68884_ucode_pkg::F_ORS_LSB     +: 3];
    f_oneshot = uw[rd68884_ucode_pkg::F_ONESHOT_LSB];
    f_expect  = uw[rd68884_ucode_pkg::F_EXPECT_LSB  +: 3];
    f_biu     = uw[rd68884_ucode_pkg::F_BIU_LSB     +: 4];
    f_xfer    = uw[rd68884_ucode_pkg::F_XFER_LSB    +: 6];
    f_tsrc    = uw[rd68884_ucode_pkg::F_TSRC_LSB    +: 4];
    f_tdst    = uw[rd68884_ucode_pkg::F_TDST_LSB    +: 4];
    f_rf      = uw[rd68884_ucode_pkg::F_RF_LSB      +: 2];
    f_rfa     = uw[rd68884_ucode_pkg::F_RFA_LSB     +: 3];
    f_asrc    = uw[rd68884_ucode_pkg::F_ASRC_LSB    +: 3];
    f_xop     = uw[rd68884_ucode_pkg::F_XOP_LSB];
    f_fpsr    = uw[rd68884_ucode_pkg::F_FPSR_LSB    +: 2];
    f_ctr     = uw[rd68884_ucode_pkg::F_CTR_LSB     +: 2];
    f_mask    = uw[rd68884_ucode_pkg::F_MASK_LSB    +: 2];
    f_flag    = uw[rd68884_ucode_pkg::F_FLAG_LSB    +: 4];
  end

  // ==========================================================================
  // State
  // ==========================================================================
  logic [10:0] stack0, stack1, stack2, stack3;
  logic [1:0]  sp;
  logic [15:0] ctr;
  logic [31:0] t;
  logic [7:0]  mask;
  logic [2:0]  rn;
  logic [15:0] cmd;
  logic        is_cond;
  logic [15:0] fpcr;
  logic [31:0] fpsr;
  logic [31:0] xi0, xi1, xi2;
  logic        a_sign;
  logic [17:0] a_exp;
  logic [71:0] a_mant;
  logic        exc_pend;
  logic        null_state;
  logic        pcode;
  logic        ev_resp_q, ev_rsel_q, ev_save_q;
  logic        restore_req_q;

  logic [90:0] rfq;              // the register file's read register

  // ==========================================================================
  // Traps and conditions
  // ==========================================================================
  logic restore_rise;
  logic trap;
  logic [10:0] trap_addr;
  always_comb begin
    restore_rise = restore_req_i & ~restore_req_q;
    trap      = arch_reset_i | abort_i | restore_rise;
    trap_addr = arch_reset_i ? rd68884_ucode_pkg::ENTRY_RESET :
                abort_i      ? rd68884_ucode_pkg::ENTRY_ABORT :
                               rd68884_ucode_pkg::ENTRY_RESTORE;
  end

  logic ev_resp, ev_rsel, ev_save;
  assign ev_resp = ev_resp_q | resp_read_i;
  assign ev_rsel = ev_rsel_q | rsel_read_i;
  assign ev_save = ev_save_q | save_read_i;

  logic [2:0] opclass, rx;
  assign opclass = cmd[15:13];
  assign rx      = cmd[12:10];

  // FPU 4.4: the predicate. Table 4-20 note 3: bit 5 is ignored. The 01xxxx
  // predicates set BSUN when NAN is set.
  logic cc_n, cc_z, cc_nan;
  logic eq, gt, lt, un;
  logic tf, bsun;
  always_comb begin
    cc_n   = fpsr[27];
    cc_z   = fpsr[26];
    cc_nan = fpsr[24];
    eq = cc_z;
    gt = ~(cc_nan | cc_z | cc_n);
    lt = cc_n & ~(cc_nan | cc_z);
    un = cc_nan;
    case (cmd[3:0])
      4'h0: tf = 1'b0;
      4'h1: tf = eq;
      4'h2: tf = gt;
      4'h3: tf = gt | eq;
      4'h4: tf = lt;
      4'h5: tf = lt | eq;
      4'h6: tf = gt | lt;
      4'h7: tf = ~un;
      4'h8: tf = un;
      4'h9: tf = un | eq;
      4'hA: tf = un | gt;
      4'hB: tf = un | gt | eq;
      4'hC: tf = un | lt;
      4'hD: tf = un | lt | eq;
      4'hE: tf = ~eq;
      default: tf = 1'b1;
    endcase
    bsun = cmd[4] & cc_nan;
  end

  // FPU 6.1.9 / 6.1.10: the vector of the highest enabled exception, 49 if
  // none (an exception made pending by software, FPU 6.4.2.2).
  logic [7:0] exc, en;
  logic [7:0] vec;
  always_comb begin
    exc = fpsr[15:8];
    en  = fpcr[15:8];
    if      (exc[7] & en[7]) vec = 8'd48;
    else if (exc[6] & en[6]) vec = 8'd54;
    else if (exc[5] & en[5]) vec = 8'd52;
    else if (exc[4] & en[4]) vec = 8'd53;
    else if (exc[3] & en[3]) vec = 8'd51;
    else if (exc[2] & en[2]) vec = 8'd50;
    else                     vec = 8'd49;
  end

  // The inexact bits need no test of their own: below DZ every case,
  // INEX1, INEX2, an untrapped overflow or none at all, is vector 49.
  logic unused_x;
  assign unused_x = &{1'b1, exc[1:0], en[1:0]};

  logic pcen;
  assign pcen = |fpcr[14:8];

  logic cond_raw;
  logic cond;
  always_comb begin
    case (f_cond)
      rd68884_ucode_pkg::COND_TRUE:       cond_raw = 1'b1;
      rd68884_ucode_pkg::COND_CMD_PEND:   cond_raw = cmd_pend_i;
      rd68884_ucode_pkg::COND_CMD_COND:   cond_raw = cmd_cond_i;
      rd68884_ucode_pkg::COND_OPW_VALID:  cond_raw = opw_valid_i;
      rd68884_ucode_pkg::COND_OPR_VALID:  cond_raw = opr_valid_i;
      rd68884_ucode_pkg::COND_RESP_READ:  cond_raw = ev_resp;
      rd68884_ucode_pkg::COND_RSEL_READ:  cond_raw = ev_rsel;
      rd68884_ucode_pkg::COND_SAVE_REQ:   cond_raw = save_req_i;
      rd68884_ucode_pkg::COND_SAVE_READ:  cond_raw = ev_save;
      rd68884_ucode_pkg::COND_EXC_PEND:   cond_raw = exc_pend;
      rd68884_ucode_pkg::COND_NULL_STATE: cond_raw = null_state;
      rd68884_ucode_pkg::COND_CTR_ZERO:   cond_raw = (ctr == 16'd0);
      rd68884_ucode_pkg::COND_MASK_ZERO:  cond_raw = (mask == 8'd0);
      rd68884_ucode_pkg::COND_TF:         cond_raw = tf;
      rd68884_ucode_pkg::COND_BSUN:       cond_raw = bsun;
      rd68884_ucode_pkg::COND_BSUN_EN:    cond_raw = fpcr[15];
      rd68884_ucode_pkg::COND_REST_NULL:  cond_raw = (restore_word_i[15:8] == 8'h00);
      rd68884_ucode_pkg::COND_REST_IDLE:  cond_raw = (restore_word_i == rd68884_pkg::FRAME_IDLE);
      rd68884_ucode_pkg::COND_FLINE:      cond_raw = (opclass == 3'd1) |
                                                     (((opclass == 3'd0) | ((opclass == 3'd2) & (rx != 3'd7))) & cmd[6]);
      rd68884_ucode_pkg::COND_REPORTS:    cond_raw = (opclass == 3'd0) | (opclass == 3'd2) | (opclass == 3'd3);
      rd68884_ucode_pkg::COND_PCEN:       cond_raw = pcen;
      rd68884_ucode_pkg::COND_LST_CR:     cond_raw = cmd[12];
      rd68884_ucode_pkg::COND_LST_SR:     cond_raw = cmd[11];
      rd68884_ucode_pkg::COND_LST_IAR:    cond_raw = cmd[10] | (rx == 3'd0);
      rd68884_ucode_pkg::COND_DYN_LIST:   cond_raw = cmd[11];
      rd68884_ucode_pkg::COND_PV:         cond_raw = pv_i;
      rd68884_ucode_pkg::COND_PEND_GEN:   cond_raw = (t[30:28] == 3'd3);
      rd68884_ucode_pkg::COND_PEND_COND:  cond_raw = (t[30:28] == 3'd1);
      rd68884_ucode_pkg::COND_IS_COND:    cond_raw = is_cond;
      rd68884_ucode_pkg::COND_CMD_FMOVE:  cond_raw = (cmd[6:0] == 7'd0);
      default:                            cond_raw = 1'b0;
    endcase
    cond = cond_raw ^ f_neg;
  end

  // ==========================================================================
  // The transfer bus
  // ==========================================================================
  logic [31:0] tbus;
  logic [2:0]  frame_code;
  always_comb begin
    frame_code = pcode ? (is_cond ? 3'd1 : 3'd3) : 3'd7;
    case (f_tsrc)
      rd68884_ucode_pkg::TSRC_T:     tbus = t;
      rd68884_ucode_pkg::TSRC_OPW:   tbus = opw_data_i;
      rd68884_ucode_pkg::TSRC_IMM:   tbus = {16'd0, f_imm};
      rd68884_ucode_pkg::TSRC_FPCR:  tbus = {16'd0, fpcr};
      rd68884_ucode_pkg::TSRC_FPSR:  tbus = fpsr;
      rd68884_ucode_pkg::TSRC_FPIAR: tbus = fpiar_i;
      rd68884_ucode_pkg::TSRC_CMDW:  tbus = pcode ? {cmd, 16'hFFFF} : 32'hFFFF_FFFF;
      rd68884_ucode_pkg::TSRC_XI0:   tbus = xi0;
      rd68884_ucode_pkg::TSRC_XI1:   tbus = xi1;
      rd68884_ucode_pkg::TSRC_XI2:   tbus = xi2;
      // FPU figure 6-6 / table 6-4: the BIU flags of the idle frame.
      rd68884_ucode_pkg::TSRC_FLAGS: tbus = {pv_i, frame_code, ~exc_pend, 1'b1, 10'd0, 16'hFFFF};
      rd68884_ucode_pkg::TSRC_RESTW: tbus = {16'd0, restore_word_i};
      default:                       tbus = 32'hFFFF_FFFF;
    endcase
  end

  // ==========================================================================
  // Extended-format pack/unpack, FPU table 3-3 (doc/microcode.md)
  // ==========================================================================
  logic [17:0] ux_exp;
  logic [14:0] px_be;
  always_comb begin
    ux_exp = {3'b000, xi0[30:16]} - 18'd16383;
    px_be  = a_exp[14:0] + 15'd16383;
  end

  // Condition codes from A, FPU table 2-1.
  logic a_special, a_nan, a_inf, a_zero;
  logic [3:0] a_cc;
  always_comb begin
    a_special = (a_exp == 18'd16384);
    a_nan  = a_special & (a_mant[70:8] != 63'd0);
    a_inf  = a_special & (a_mant[70:8] == 63'd0);
    a_zero = ~a_special & (a_mant[71:8] == 64'd0);
    a_cc   = {a_sign, a_zero, a_inf, a_nan};
  end

  // The FMOVEM mask: the highest set bit, and the register it names.
  logic [2:0] mbit;
  always_comb begin
    casez (mask)
      8'b1???????: mbit = 3'd7;
      8'b01??????: mbit = 3'd6;
      8'b001?????: mbit = 3'd5;
      8'b0001????: mbit = 3'd4;
      8'b00001???: mbit = 3'd3;
      8'b000001??: mbit = 3'd2;
      8'b0000001?: mbit = 3'd1;
      default:     mbit = 3'd0;
    endcase
  end

  // ==========================================================================
  // The register file
  // ==========================================================================
  logic [6:0] rf_addr;
  always_comb begin
    case (f_rfa)
      rd68884_ucode_pkg::RFA_IMM:   rf_addr = f_imm[6:0];
      rd68884_ucode_pkg::RFA_RX:    rf_addr = {4'd0, rx};
      rd68884_ucode_pkg::RFA_RY:    rf_addr = {4'd0, cmd[9:7]};
      rd68884_ucode_pkg::RFA_RN:    rf_addr = {4'd0, rn};
      rd68884_ucode_pkg::RFA_ETEMP: rf_addr = 7'd8;
      default:                      rf_addr = ctr[6:0];
    endcase
  end

  logic rf_we, rf_re;
  assign rf_we = ~trap & (f_rf == rd68884_ucode_pkg::RF_WRITE);
  assign rf_re = ~trap & (f_rf == rd68884_ucode_pkg::RF_READ);

  rd68884_regfile u_rf (
      .clk(clk),
      .we (rf_we),
      .wa (rf_addr),
      .wd ({a_sign, a_exp, a_mant}),
      .re (rf_re),
      .ra (rf_addr),
      .q  (rfq)
  );

  // ==========================================================================
  // To the BIU
  // ==========================================================================
  logic [15:0] ors;
  always_comb begin
    ors = 16'd0;
    case (f_ors)
      rd68884_ucode_pkg::ORS_PC:   ors = {1'b0, pcen, 14'd0};
      rd68884_ucode_pkg::ORS_TF:   ors = {15'd0, tf};
      rd68884_ucode_pkg::ORS_VEC:  ors = {8'd0, vec};
      rd68884_ucode_pkg::ORS_DN:   ors = {13'd0, cmd[6:4]};
      rd68884_ucode_pkg::ORS_DNPC: ors = {1'b0, pcen, 11'd0, cmd[6:4]};
      default:                     ors = 16'd0;
    endcase
  end

  always_comb begin
    resp_we_o      = ~trap & (f_resp != rd68884_ucode_pkg::RESP_NONE);
    resp_o         = f_imm | ors;
    resp_oneshot_o = f_oneshot;
    expect_o       = f_expect;
    resp_cond_o    = (f_resp == rd68884_ucode_pkg::RESP_WRC);
    cmd_ack_o      = ~trap & (f_biu == rd68884_ucode_pkg::BIU_CMD_ACK);
    opw_ack_o      = ~trap & (f_biu == rd68884_ucode_pkg::BIU_OPW_ACK);
    opr_we_o       = ~trap & (f_biu == rd68884_ucode_pkg::BIU_OPR_WR);
    opr_o          = tbus;
    rsel_we_o      = ~trap & (f_biu == rd68884_ucode_pkg::BIU_RSEL_WR);
    rsel_o         = mask;
    rsel_dir_o     = cmd[13];
    save_we_o      = ~trap & (f_biu == rd68884_ucode_pkg::BIU_SAVE_WR);
    save_o         = tbus[15:0];
    save_xfer_o    = f_xfer;
    restore_we_o   = ~trap & (f_biu == rd68884_ucode_pkg::BIU_RESTORE_WR);
    restore_o      = tbus[15:0];
    restore_xfer_o = f_xfer;
    fpiar_we_o     = ~trap & (f_biu == rd68884_ucode_pkg::BIU_FPIAR_WR);
    fpiar_o        = tbus;
    clear_o        = ~trap & (f_biu == rd68884_ucode_pkg::BIU_CLEAR);
  end

  // ==========================================================================
  // The next micro-address
  // ==========================================================================
  logic [10:0] upc_inc;
  logic [10:0] seq_nxt;
  logic [6:0]  idx;
  always_comb begin
    upc_inc = upc + 11'd1;
    case (f_idx)
      rd68884_ucode_pkg::IDX_OPCLASS: idx = {4'd0, opclass};
      rd68884_ucode_pkg::IDX_RX:      idx = {4'd0, rx};
      default:                        idx = cmd[6:0];
    endcase
    case (f_seq)
      rd68884_ucode_pkg::SEQ_JUMP: seq_nxt = f_tgt;
      rd68884_ucode_pkg::SEQ_CALL: seq_nxt = f_tgt;
      rd68884_ucode_pkg::SEQ_RET: begin
        case (sp - 2'd1)
          2'd0:    seq_nxt = stack0;
          2'd1:    seq_nxt = stack1;
          2'd2:    seq_nxt = stack2;
          default: seq_nxt = stack3;
        endcase
      end
      rd68884_ucode_pkg::SEQ_BR:   seq_nxt = cond ? f_tgt : upc_inc;
      rd68884_ucode_pkg::SEQ_WAIT: seq_nxt = cond ? upc_inc : upc;
      rd68884_ucode_pkg::SEQ_DISP: seq_nxt = f_tgt | {4'd0, idx};
      rd68884_ucode_pkg::SEQ_LOOP: seq_nxt = (ctr != 16'd0) ? f_tgt : upc_inc;
      default:                     seq_nxt = upc_inc;
    endcase
    addr_nxt = trap ? trap_addr : seq_nxt;
  end

  // FPSR: the bus, then the condition codes and the EXC clear, then the flags
  // that OR into it -- the order of tools/iss/core.py.
  logic [31:0] fpsr_nxt;
  always_comb begin
    fpsr_nxt = fpsr;
    if (f_tdst == rd68884_ucode_pkg::TDST_FPSR) fpsr_nxt = tbus & 32'h0FFF_FFF8;
    if (f_fpsr == rd68884_ucode_pkg::FPSR_CC || f_fpsr == rd68884_ucode_pkg::FPSR_CC_CLREXC)
      fpsr_nxt[27:24] = a_cc;
    if (f_fpsr == rd68884_ucode_pkg::FPSR_CLREXC || f_fpsr == rd68884_ucode_pkg::FPSR_CC_CLREXC)
      fpsr_nxt[15:8] = 8'd0;
    if (f_flag == rd68884_ucode_pkg::FLAG_BSUN)  fpsr_nxt = fpsr_nxt | 32'h0000_8080;
    if (f_flag == rd68884_ucode_pkg::FLAG_RESET) fpsr_nxt = 32'd0;
  end

  // ==========================================================================
  // The clock
  // ==========================================================================
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      upc           <= rd68884_ucode_pkg::ENTRY_RESET;
      stack0        <= 11'd0;
      stack1        <= 11'd0;
      stack2        <= 11'd0;
      stack3        <= 11'd0;
      sp            <= 2'd0;
      ctr           <= 16'd0;
      t             <= 32'd0;
      mask          <= 8'd0;
      rn            <= 3'd0;
      cmd           <= 16'd0;
      is_cond       <= 1'b0;
      fpcr          <= 16'd0;
      fpsr          <= 32'd0;
      xi0           <= 32'd0;
      xi1           <= 32'd0;
      xi2           <= 32'd0;
      a_sign        <= 1'b0;
      a_exp         <= -18'sd16383;
      a_mant        <= 72'd0;
      exc_pend      <= 1'b0;
      null_state    <= 1'b0;
      pcode         <= 1'b0;
      ev_resp_q     <= 1'b0;
      ev_rsel_q     <= 1'b0;
      ev_save_q     <= 1'b0;
      restore_req_q <= 1'b0;
    end else begin
      restore_req_q <= restore_req_i;
      upc           <= addr_nxt;
      if (trap) begin
        ev_resp_q <= ev_resp;
        ev_rsel_q <= ev_rsel;
        ev_save_q <= ev_save;
      end else begin
        // The sticky events, re-armed by the action that awaits the next.
        ev_resp_q <= ev_resp & (f_resp == rd68884_ucode_pkg::RESP_NONE);
        ev_rsel_q <= ev_rsel & (f_biu != rd68884_ucode_pkg::BIU_RSEL_WR);
        ev_save_q <= ev_save & (f_biu != rd68884_ucode_pkg::BIU_SAVE_WR);

        t <= tbus;

        // ---- the transfer bus's destination ----------------------------
        case (f_tdst)
          rd68884_ucode_pkg::TDST_FPCR:  fpcr <= tbus[15:0];
          rd68884_ucode_pkg::TDST_MASK:  mask <= tbus[7:0];
          rd68884_ucode_pkg::TDST_XI0:   xi0  <= tbus;
          rd68884_ucode_pkg::TDST_XI1:   xi1  <= tbus;
          rd68884_ucode_pkg::TDST_XI2:   xi2  <= tbus;
          rd68884_ucode_pkg::TDST_CMD:   cmd  <= tbus[31:16];
          rd68884_ucode_pkg::TDST_FLAGS: exc_pend <= ~tbus[27];
          default: ;
        endcase

        fpsr <= fpsr_nxt;

        // ---- the BIU's side effects in here -----------------------------
        if (f_biu == rd68884_ucode_pkg::BIU_CMD_ACK) begin
          cmd     <= cmd_word_i;
          is_cond <= cmd_cond_i;
        end

        // ---- A and XI -------------------------------------------------------
        case (f_asrc)
          rd68884_ucode_pkg::ASRC_RFQ: begin
            a_sign <= rfq[90];
            a_exp  <= rfq[89:72];
            a_mant <= rfq[71:0];
          end
          rd68884_ucode_pkg::ASRC_UNPACKX: begin
            a_sign <= xi0[31];
            a_exp  <= ux_exp;
            a_mant <= {xi1, xi2, 8'd0};
          end
          rd68884_ucode_pkg::ASRC_NAN: begin
            a_sign <= 1'b0;
            a_exp  <= 18'd16384;
            a_mant <= {64'hFFFF_FFFF_FFFF_FFFF, 8'd0};
          end
          rd68884_ucode_pkg::ASRC_ZERO: begin
            a_sign <= 1'b0;
            a_exp  <= -18'sd16383;
            a_mant <= 72'd0;
          end
          default: ;
        endcase
        if (f_xop) begin
          xi0 <= {a_sign, px_be, 16'd0};
          xi1 <= a_mant[71:40];
          xi2 <= a_mant[39:8];
        end

        // ---- counters, mask, flags ------------------------------------------
        if (f_ctr == rd68884_ucode_pkg::CTR_LOAD) begin
          ctr <= f_imm;
        end else if (f_ctr == rd68884_ucode_pkg::CTR_DEC) begin
          ctr <= ctr - 16'd1;
        end
        if (f_seq == rd68884_ucode_pkg::SEQ_LOOP && ctr != 16'd0) begin
          ctr <= ctr - 16'd1;
        end
        if (f_mask == rd68884_ucode_pkg::MASK_LOAD) begin
          mask <= cmd[7:0];
        end else if (f_mask == rd68884_ucode_pkg::MASK_NEXT && mask != 8'd0) begin
          rn         <= cmd[12] ? (3'd7 - mbit) : mbit;
          mask[mbit] <= 1'b0;
        end
        case (f_flag)
          rd68884_ucode_pkg::FLAG_SET_EXC:   exc_pend   <= 1'b1;
          rd68884_ucode_pkg::FLAG_CLR_EXC:   exc_pend   <= 1'b0;
          rd68884_ucode_pkg::FLAG_SET_NULL:  null_state <= 1'b1;
          rd68884_ucode_pkg::FLAG_CLR_NULL:  null_state <= 1'b0;
          rd68884_ucode_pkg::FLAG_RESET: begin
            fpcr       <= 16'd0;
            exc_pend   <= 1'b0;
            null_state <= 1'b1;
          end
          rd68884_ucode_pkg::FLAG_PEND_NONE: pcode   <= 1'b0;
          rd68884_ucode_pkg::FLAG_PEND_CMD:  pcode   <= 1'b1;
          rd68884_ucode_pkg::FLAG_SET_COND:  is_cond <= 1'b1;
          rd68884_ucode_pkg::FLAG_CLR_COND:  is_cond <= 1'b0;
          default: ;
        endcase

        // ---- the return stack ------------------------------------------------
        if (f_seq == rd68884_ucode_pkg::SEQ_CALL) begin
          case (sp)
            2'd0:    stack0 <= upc_inc;
            2'd1:    stack1 <= upc_inc;
            2'd2:    stack2 <= upc_inc;
            default: stack3 <= upc_inc;
          endcase
          sp <= sp + 2'd1;
        end else if (f_seq == rd68884_ucode_pkg::SEQ_RET) begin
          sp <= sp - 2'd1;
        end
      end
    end
  end

endmodule
