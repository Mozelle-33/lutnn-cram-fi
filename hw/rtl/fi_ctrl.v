// Autonomous configuration-memory fault-injection controller (clk domain, 100 MHz).
//
// For every target bit (frame index fi into the FAR list, word w, bit b):
//   inject (SEM physical-frame-address command) -> run the test set and compare the DUT's class
//   output with the fault-free golden output and with the label -> inject the same address again
//   (restore) -> verification run -> log a 64-bit record if anything differed -> advance.
// Record: [15:0] fi [22:16] w [27:23] b [41:28] mismatches vs golden [55:42] correct vs label
//         [59:56] flags {verify_mismatch, restore_timeout, inject_timeout, logged_all}.
//
// Commands (256 bit, [31:0] key checked in jtag_regs, [39:32] opcode, [55:40] tag, args from 56):
//   01 CFG    far_first[15:0]@56 far_last[15:0]@72 word_lo[6:0]@88 word_hi[6:0]@96 stride[31:0]@104
//             ntest[13:0]@136 nverify[13:0]@152 flags[7:0]@168 (b0 log all, b1 skip verify, b2 stop on persist)
//   02 START  start_fi[15:0]@56 start_w[6:0]@72 start_b[4:0]@80 max_inj[31:0]@88 (0 = no limit)
//   03 STOP (any state)   04 GOLDEN   05 RAW_INJECT addr[39:0]@56   06 RUN_TEST   07 SEM_IDLE
//   08 SEM_OBSERVE   09 SEM_RESET   0A LOAD_WINDOW rec_start[31:0]@56 (any state)   0B CLEAR   0C NOP
// Main-FSM commands are accepted only when idle; their completion is signalled by tag echo with
// done=1. LOAD_WINDOW completion is signalled by win_tag.
`timescale 1ns / 1ps
module fi_ctrl #(
    parameter IN_W    = 160,     // DUT input width (features x quantisation bits)
    parameter CW      = 3,       // class-index width (3: up to 8 classes, 4: up to 16)
    parameter LATENCY = 4,       // DUT pipeline latency in cycles (dut_x -> dut_y)
    parameter NVEC    = 4096,    // test vectors in the ROM (power of two)
    parameter NFAR    = 4096,    // entries of the frame-address list
    parameter LOGD    = 16384,   // depth of the event-log ring buffer (records)
    parameter WIN_N   = 128,     // records per LOAD_WINDOW transfer to the host
    parameter VEC_FILE = "vectors.mem",   // one hex line per vector: {label[CW-1:0], x[IN_W-1:0]}
    parameter FAR_FILE = "farlist.mem",   // one hex line per frame: 25-bit frame address (FAR)
    parameter [15:0] BUILD_ID = 16'h0001, // reported in the status word so that the host can check
    parameter [15:0] DUT_ID   = 16'h0000  // which bitstream is running
) (
    input  wire              clk,
    input  wire [255:0]      cmd,        // command word from jtag_regs (key already checked)
    input  wire              cmd_valid,  // one-cycle pulse per new command
    output wire [511:0]      status,     // status snapshot read by the host through USER1
    output wire [64+64*WIN_N-1:0] window, // {WIN_N records, start index, "WIND"} read through USER3
    // SEM controller
    output reg               inject_strobe = 0,
    output reg  [39:0]       inject_address = 0,
    input  wire [7:0]        sem_st,     // {unc, ess, inj, cls, cor, obs, init, heartbeat}
    // DUT
    output reg  [IN_W-1:0]   dut_x = 0,
    input  wire [CW-1:0]     dut_y
);
    localparam VEC_W = IN_W + CW;
    localparam VA = $clog2(NVEC);
    localparam FA = $clog2(NFAR);
    localparam LA = $clog2(LOGD);
    // Pipeline entry k is valid in cycle t+3+k for the vector addressed in cycle t; the DUT sees it
    // in dut_x at t+2 and y_r holds its result at t+3+LATENCY, i.e. entry k = LATENCY.
    localparam D  = LATENCY + 1;
    localparam [15:0] LAT16 = LATENCY;

    // SEM controller state flags (PG036). The SEM is in its IDLE state, the only state in which it
    // accepts error-injection commands, when none of the state flags is set.
    wire s_inj  = sem_st[5];     // injection in progress
    wire s_cls  = sem_st[4];     // classification
    wire s_cor  = sem_st[3];     // correction
    wire s_obs  = sem_st[2];     // observation (scanning and correcting the configuration memory)
    wire s_init = sem_st[1];     // initialisation after configuration
    wire sem_idle = ~(s_init | s_obs | s_cor | s_cls | s_inj);

    // ---------------- memories ----------------
    // vrom: test vectors and labels; farrom: frame addresses of the campaign region;
    // gold: fault-free class of every vector (written by GOLDEN); logm: event-log ring buffer.
    (* rom_style = "block" *) reg [VEC_W-1:0] vrom [0:NVEC-1];
    (* rom_style = "block" *) reg [24:0] farrom [0:NFAR-1];
    (* ram_style = "block" *) reg [CW-1:0] gold [0:NVEC-1];
    (* ram_style = "block" *) reg [63:0] logm [0:LOGD-1];
    initial $readmemh(VEC_FILE, vrom);
    initial $readmemh(FAR_FILE, farrom);

    reg  [VA-1:0] vaddr = 0;
    reg  [VEC_W-1:0] vq = 0;
    reg  [CW-1:0] gq = 0;
    reg  gwe = 0;
    reg  [VA-1:0] gwaddr = 0;
    reg  [CW-1:0] gwdata = 0;
    always @(posedge clk) begin
        vq <= vrom[vaddr];
        gq <= gold[vaddr];
        if (gwe) gold[gwaddr] <= gwdata;
    end
    reg  [FA:0]  fi = 0;              // one extra bit so running past the list end is detectable
    reg  [24:0]  farq = 0;
    always @(posedge clk) farq <= farrom[fi[FA-1:0]];

    // Event log: records are written at wr_ptr and read by the window loader; the host frees
    // records by loading a window that starts past them (rd_ptr). wr_ptr - rd_ptr >= LOGD means the
    // buffer is full, and the campaign waits in C_LOG (back-pressure) so that no record is lost.
    reg  lwe = 0;
    reg  [LA-1:0] lwaddr = 0, lraddr = 0;
    reg  [63:0] lwdata = 0, lq = 0;
    always @(posedge clk) begin
        if (lwe) logm[lwaddr] <= lwdata;
        lq <= logm[lraddr];
    end

    // ---------------- configuration ----------------
    reg [15:0] far_first = 0, far_last = 0;
    reg [6:0]  word_lo = 0, word_hi = 100;
    reg [31:0] stride = 1;
    reg [13:0] ntest = 1024, nverify = 256;
    reg [7:0]  cflags = 0;
    reg [31:0] max_inj = 0;
    reg [15:0] tag = 0;
    reg [7:0]  last_op = 0;

    // ---------------- counters / status ----------------
    reg [6:0]  w = 0;
    reg [4:0]  b = 0;
    reg [31:0] inj_count = 0, wr_ptr = 0, rd_ptr = 0, nonsilent = 0, persist = 0, timeouts = 0;
    reg [31:0] sem_corr = 0, elapsed = 0;
    reg [9:0]  elapsed_div = 0;
    reg [13:0] last_mism = 0, last_corr = 0, golden_corr = 0;
    reg [31:0] win_start = 0;
    reg        running = 0, stop_req = 0, err = 0, done_flag = 0;
    reg        cor_d = 0;
    wire [7:0] op = cmd[39:32];

    // ---------------- test runner ----------------
    // tr_go starts a run of tr_n vectors. Timeline for vector i issued in cycle t (vaddr = i):
    // t+1 vq/gq valid, t+2 dut_x = vector, DUT output valid at t+2+LATENCY, y_r at t+3+LATENCY.
    reg        tr_go = 0, tr_busy = 0, tr_gold = 0;
    reg [13:0] tr_n = 0, tr_i = 0;
    reg [15:0] tr_drain = 0;
    reg        issue1 = 0, issue2 = 0;
    reg [VA-1:0] a1 = 0, a2 = 0;
    reg [CW-1:0] lab2 = 0, gld2 = 0;
    reg [D-1:0]  pv = 0;
    reg [VA-1:0] pidx [0:D-1];
    reg [CW-1:0] plab [0:D-1];
    reg [CW-1:0] pgld [0:D-1];
    reg [CW-1:0] y_r = 0;
    reg [13:0]   tr_mism = 0, tr_corr = 0;
    integer k;
    initial for (k = 0; k < D; k = k + 1) begin pidx[k] = 0; plab[k] = 0; pgld[k] = 0; end
    always @(posedge clk) begin
        y_r   <= dut_y;
        dut_x <= vq[IN_W-1:0];
        // stage t+1: ROM outputs valid for address a1
        issue1 <= tr_busy && (tr_i < tr_n);
        a1     <= vaddr;
        // stage t+2: dut_x holds the vector of a2
        issue2 <= issue1;
        a2     <= a1;
        lab2   <= vq[IN_W+CW-1:IN_W];
        gld2   <= gq;
        pv <= {pv[D-2:0], issue2};
        pidx[0] <= a2;  plab[0] <= lab2;  pgld[0] <= gld2;
        for (k = 1; k < D; k = k + 1) begin pidx[k] <= pidx[k-1]; plab[k] <= plab[k-1]; pgld[k] <= pgld[k-1]; end
        gwe <= 1'b0;
        if (tr_go) begin
            tr_busy <= 1'b1; tr_i <= 0; vaddr <= 0; tr_mism <= 0; tr_corr <= 0; tr_drain <= 0;
        end else if (tr_busy) begin
            if (tr_i < tr_n) begin
                tr_i  <= tr_i + 1'b1;
                vaddr <= vaddr + 1'b1;
            end else begin
                tr_drain <= tr_drain + 1'b1;
                if (tr_drain == D + 6) tr_busy <= 1'b0;
            end
            // pv[D-1]: y_r holds the DUT output for pidx[D-1]
            if (pv[D-1]) begin
                if (y_r != pgld[D-1]) tr_mism <= tr_mism + 1'b1;
                if (y_r == plab[D-1]) tr_corr <= tr_corr + 1'b1;
                if (tr_gold) begin gwe <= 1'b1; gwaddr <= pidx[D-1]; gwdata <= y_r; end
            end
        end
    end

    // ---------------- main FSM ----------------
    localparam S_IDLE = 5'd0, S_SEMCMD = 5'd1, S_SEMWAIT = 5'd2, S_TESTW = 5'd4,
               S_RAW = 5'd5, S_RAWW1 = 5'd6, S_RAWW2 = 5'd7,
               C_CHECK = 5'd10, C_FAR = 5'd11, C_INJ = 5'd12, C_INJW1 = 5'd13, C_INJW2 = 5'd14,
               C_RUNW = 5'd16, C_RES = 5'd17, C_RESW1 = 5'd18, C_RESW2 = 5'd19,
               C_VERW = 5'd21, C_LOG = 5'd22, C_ADV = 5'd23, S_ERR = 5'd31;
    reg [4:0]  st = S_IDLE;
    reg [31:0] tmo = 0;
    reg [2:0]  semop = 0;              // 1 idle, 2 observe, 3 reset
    reg [39:0] cur_addr = 0;
    reg [13:0] r_mism = 0, r_corr = 0, v_mism = 0;
    reg [3:0]  r_flags = 0;
    reg [31:0] adv_left = 0;
    reg [31:0] run_inj = 0;
    wire past_end = (fi > far_last[FA:0]);
    wire log_full = (wr_ptr - rd_ptr) >= LOGD;
    // SEM error-injection command with a physical frame address (PG036):
    // {0, SLR=00, block type[1:0], top/bottom half, row[4:0], column[9:0], minor[6:0], word[6:0], bit[4:0]}.
    // Injecting the same address twice flips the bit and then restores it.
    wire [39:0] tgt_addr = {1'b0, 2'b00, farq[24:23], farq[22], farq[21:17], farq[16:7], farq[6:0], w, b};

    always @(posedge clk) begin
        inject_strobe <= 1'b0;
        tr_go <= 1'b0;
        lwe <= 1'b0;
        cor_d <= s_cor;
        if (s_cor && !cor_d) sem_corr <= sem_corr + 1'b1;
        if (running) begin
            elapsed_div <= elapsed_div + 1'b1;
            if (&elapsed_div) elapsed <= elapsed + 1'b1;
        end
        if (cmd_valid && op == 8'h03) stop_req <= 1'b1;
        case (st)
        S_IDLE: begin
            running <= 1'b0;
            if (cmd_valid && op != 8'h0A) begin
                tag <= cmd[55:40];
                last_op <= op;
                done_flag <= 1'b0;
                case (op)
                8'h01: begin
                    far_first <= cmd[71:56];  far_last <= cmd[87:72];
                    word_lo <= cmd[94:88];    word_hi <= cmd[102:96];
                    stride <= (cmd[135:104] == 0) ? 32'd1 : cmd[135:104];
                    ntest <= cmd[149:136];    nverify <= cmd[165:152];
                    cflags <= cmd[175:168];
                    done_flag <= 1'b1;
                end
                8'h02: begin
                    fi <= cmd[56+FA:56]; w <= cmd[78:72]; b <= cmd[84:80];
                    max_inj <= cmd[119:88]; run_inj <= 0;
                    stop_req <= 1'b0; err <= 1'b0;
                    elapsed <= 0; elapsed_div <= 0;
                    if (sem_idle) begin running <= 1'b1; st <= C_CHECK; end
                    else begin err <= 1'b1; done_flag <= 1'b1; end
                end
                8'h04: begin tr_gold <= 1'b1; tr_n <= ntest; tr_go <= 1'b1; st <= S_TESTW; end
                8'h05: begin cur_addr <= cmd[95:56]; st <= S_RAW; end
                8'h06: begin tr_gold <= 1'b0; tr_n <= ntest; tr_go <= 1'b1; st <= S_TESTW; end
                8'h07: begin semop <= 3'd1; st <= S_SEMCMD; end
                8'h08: begin semop <= 3'd2; st <= S_SEMCMD; end
                8'h09: begin semop <= 3'd3; st <= S_SEMCMD; end
                8'h0B: begin
                    inj_count <= 0; wr_ptr <= 0; nonsilent <= 0; persist <= 0;
                    timeouts <= 0; sem_corr <= 0; err <= 0; done_flag <= 1'b1;
                end
                default: done_flag <= 1'b1;
                endcase
            end
        end
        // ---- SEM directed state change ----
        // Directed commands on the injection interface: E0.. enter IDLE, A0.. enter OBSERVATION,
        // B0.. software reset. The campaign keeps the SEM in IDLE so that it does not correct the
        // injected bit on its own.
        S_SEMCMD: begin
            inject_address <= (semop == 3'd1) ? 40'hE000000000 : (semop == 3'd2) ? 40'hA000000000 : 40'hB000000000;
            inject_strobe <= 1'b1;
            tmo <= 0;
            st <= S_SEMWAIT;
        end
        S_SEMWAIT: begin
            tmo <= tmo + 1'b1;
            if (tmo > 32'd16 && ((semop == 3'd1 && sem_idle) || (semop != 3'd1 && s_obs))) begin
                done_flag <= 1'b1; st <= S_IDLE;
            end else if (tmo == 32'd400_000_000) begin
                timeouts <= timeouts + 1'b1; err <= 1'b1; done_flag <= 1'b1; st <= S_IDLE;
            end
        end
        // ---- stand-alone test run / golden capture ----
        S_TESTW: begin
            if (!tr_go && !tr_busy) begin
                last_mism <= tr_mism; last_corr <= tr_corr;
                if (tr_gold) golden_corr <= tr_corr;
                tr_gold <= 1'b0; done_flag <= 1'b1; st <= S_IDLE;
            end
        end
        // ---- raw injection (host-controlled, SEM must be idle) ----
        S_RAW: begin
            if (!sem_idle) begin err <= 1'b1; done_flag <= 1'b1; st <= S_IDLE; end
            else begin inject_address <= cur_addr; inject_strobe <= 1'b1; tmo <= 0; st <= S_RAWW1; end
        end
        S_RAWW1: begin
            tmo <= tmo + 1'b1;
            if (s_inj) begin tmo <= 0; st <= S_RAWW2; end
            else if (tmo == 32'd100_000) begin timeouts <= timeouts + 1'b1; err <= 1'b1; done_flag <= 1'b1; st <= S_IDLE; end
        end
        S_RAWW2: begin
            tmo <= tmo + 1'b1;
            if (!s_inj) begin done_flag <= 1'b1; st <= S_IDLE; end
            else if (tmo == 32'd10_000_000) begin timeouts <= timeouts + 1'b1; err <= 1'b1; done_flag <= 1'b1; st <= S_IDLE; end
        end
        // ---- campaign: one iteration per target bit ----
        // C_CHECK: stop on STOP, at the end of the frame list or after max_inj injections.
        C_CHECK: begin
            if (stop_req || past_end || (max_inj != 0 && run_inj >= max_inj)) begin
                running <= 1'b0; done_flag <= 1'b1; st <= S_IDLE;
            end else st <= C_FAR;
        end
        C_FAR: st <= C_INJ;            // farq for the current fi is valid from here
        // C_INJ..C_INJW2: flip the target bit. The SEM raises its injection flag while it reads,
        // modifies and writes back the frame (~24 us); the timeouts are 1 ms to start, 100 ms to end.
        C_INJ: begin
            cur_addr <= tgt_addr;
            inject_address <= tgt_addr;
            inject_strobe <= 1'b1;
            r_flags <= 0; tmo <= 0;
            st <= C_INJW1;
        end
        C_INJW1: begin
            tmo <= tmo + 1'b1;
            if (s_inj) begin tmo <= 0; st <= C_INJW2; end
            else if (tmo == 32'd100_000) begin timeouts <= timeouts + 1'b1; st <= S_ERR; end
        end
        C_INJW2: begin
            tmo <= tmo + 1'b1;
            if (!s_inj) begin tr_gold <= 1'b0; tr_n <= ntest; tr_go <= 1'b1; st <= C_RUNW; end
            else if (tmo == 32'd10_000_000) begin timeouts <= timeouts + 1'b1; st <= S_ERR; end
        end
        // C_RUNW: the test run with the bit flipped; keep its mismatch and correct counts.
        C_RUNW: begin
            if (!tr_go && !tr_busy) begin r_mism <= tr_mism; r_corr <= tr_corr; st <= C_RES; end
        end
        // C_RES..C_RESW2: restore the bit by injecting the same address again, then (unless flag b1)
        // a verify run of nverify vectors: a mismatch now means the upset corrupted state that the
        // restore did not repair (e.g. an SRL or LUT-RAM mode bit), i.e. a persistent error.
        C_RES: begin
            inject_address <= cur_addr; inject_strobe <= 1'b1; tmo <= 0; st <= C_RESW1;
        end
        C_RESW1: begin
            tmo <= tmo + 1'b1;
            if (s_inj) begin tmo <= 0; st <= C_RESW2; end
            else if (tmo == 32'd100_000) begin timeouts <= timeouts + 1'b1; st <= S_ERR; end
        end
        C_RESW2: begin
            tmo <= tmo + 1'b1;
            if (!s_inj) begin
                if (cflags[1]) begin v_mism <= 0; st <= C_LOG; end
                else begin tr_gold <= 1'b0; tr_n <= nverify; tr_go <= 1'b1; st <= C_VERW; end
            end else if (tmo == 32'd10_000_000) begin timeouts <= timeouts + 1'b1; st <= S_ERR; end
        end
        C_VERW: begin
            if (!tr_go && !tr_busy) begin v_mism <= tr_mism; st <= C_LOG; end
        end
        // C_LOG: log a record for every non-silent bit (or every bit with flag b0); wait while the
        // ring buffer is full. A persistent error stops the campaign if flag b2 is set, so that the
        // host can reconfigure the device before continuing.
        C_LOG: begin
            if (r_mism != 0 || v_mism != 0 || cflags[0]) begin
                if (!log_full) begin
                    lwe <= 1'b1;
                    lwaddr <= wr_ptr[LA-1:0];
                    lwdata <= {4'd0, (v_mism != 0), r_flags[2], r_flags[1], cflags[0], r_corr, r_mism, b, w,
                               {(15-FA){1'b0}}, fi};
                    wr_ptr <= wr_ptr + 1'b1;
                    if (r_mism != 0) nonsilent <= nonsilent + 1'b1;
                    if (v_mism != 0) begin persist <= persist + 1'b1; if (cflags[2]) stop_req <= 1'b1; end
                    last_mism <= r_mism; last_corr <= r_corr;
                    inj_count <= inj_count + 1'b1; run_inj <= run_inj + 1'b1;
                    adv_left <= stride; st <= C_ADV;
                end
            end else begin
                inj_count <= inj_count + 1'b1; run_inj <= run_inj + 1'b1;
                adv_left <= stride; st <= C_ADV;
            end
        end
        // C_ADV: advance `stride` bits in campaign order (bit, then word word_lo..word_hi, then frame).
        C_ADV: begin
            if (adv_left == 0) st <= C_CHECK;
            else begin
                adv_left <= adv_left - 1'b1;
                if (b != 5'd31) b <= b + 1'b1;
                else begin
                    b <= 0;
                    if (w != word_hi) w <= w + 1'b1;
                    else begin w <= word_lo; fi <= fi + 1'b1; end
                end
            end
        end
        S_ERR: begin
            running <= 1'b0;
            err <= 1'b1;
            done_flag <= 1'b1;
            if (cmd_valid && op != 8'h0A && op != 8'h03) st <= S_IDLE;   // next command leaves error state (not executed)
        end
        default: st <= S_IDLE;
        endcase
    end

    // ---------------- window loader (independent of the main FSM) ----------------
    // LOAD_WINDOW(rec_start): copy WIN_N records starting at rec_start into win_mem and free every
    // record before rec_start (rd_ptr). Completion: win_tag == command tag.
    reg        wl_busy = 0;
    reg [15:0] win_tag = 0, wl_tag = 0;
    reg [8:0]  win_i = 0;
    reg [63:0] win_mem [0:WIN_N-1];
    initial for (k = 0; k < WIN_N; k = k + 1) win_mem[k] = 0;
    always @(posedge clk) begin
        if (!wl_busy && cmd_valid && op == 8'h0A) begin
            wl_busy <= 1'b1; wl_tag <= cmd[55:40];
            win_start <= cmd[87:56]; rd_ptr <= cmd[87:56];
            lraddr <= cmd[56+LA-1:56]; win_i <= 0;
        end else if (!wl_busy && cmd_valid && op == 8'h0B && st == S_IDLE) begin
            rd_ptr <= 0;
        end else if (wl_busy) begin
            // the log RAM has one cycle of read latency: lq holds the record addressed one cycle ago
            lraddr <= lraddr + 1'b1;
            if (win_i != 0) win_mem[win_i - 1] <= lq;
            if (win_i == WIN_N) begin wl_busy <= 1'b0; win_tag <= wl_tag; end
            win_i <= win_i + 1'b1;
        end
    end

    // ---------------- status and window ----------------
    assign status = {
        win_tag, DUT_ID,                                 // [511:480]
        BUILD_ID, LAT16,                                 // [479:448]
        win_start,                                       // [447:416]
        sem_corr,                                        // [415:384]
        elapsed,                                         // [383:352]  units of 1024 cycles
        2'd0, ntest, 2'd0, golden_corr,                  // [351:320]
        2'd0, last_corr, 2'd0, last_mism,                // [319:288]
        {(15-FA){1'b0}}, fi, 1'b0, w, 3'd0, b,           // [287:256]
        timeouts,                                        // [255:224]
        persist,                                         // [223:192]
        nonsilent,                                       // [191:160]
        rd_ptr,                                          // [159:128]
        wr_ptr,                                          // [127:96]
        inj_count,                                       // [95:64]
        tag,                                             // [63:48]
        last_op, sem_st,                                 // [47:32]
        err, done_flag, running, st, 24'h4E4649          // [31:0]
    };
    genvar gi;
    generate for (gi = 0; gi < WIN_N; gi = gi + 1) begin : g_win
        assign window[64 + gi*64 +: 64] = win_mem[gi];
    end endgenerate
    assign window[63:0] = {win_start, 32'h57494E44};
endmodule
