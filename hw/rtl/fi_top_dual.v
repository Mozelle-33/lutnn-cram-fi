// Top level with a separate clock for the network under test (otherwise as fi_top.v, whose sem_cfg and
// sem_mon_cap modules it uses; the instance names are the same, so that the build script's pblocks and
// the analysis scripts apply unchanged).
//   sysclk 100 MHz -> MMCM (VCO 1000 MHz) -> CLKOUT0 100 MHz: SEM controller and ICAP, whose maximum
//                                            clock rate is 100 MHz
//                                         -> CLKOUT1 1000/FAST_DIV MHz: FI controller, JTAG registers, DUT
// The two clocks are treated as asynchronous: the SEM status flags are synchronised into the controller
// clock, and an injection command crosses as a toggle that the SEM clock turns into a one-cycle strobe.
// The injection address is set together with the strobe and held until the next command, so it is
// stable when the SEM samples it.
`timescale 1ns / 1ps
module fi_top_dual #(
    parameter IN_W    = 160,
    parameter CW      = 3,
    parameter NVEC    = 4096,
    parameter LATENCY = 4,
    parameter VEC_FILE = "vectors.mem",
    parameter FAR_FILE = "farlist.mem",
    parameter [15:0] BUILD_ID = 16'h0001,
    parameter [15:0] DUT_ID   = 16'h0000,
    parameter FAST_DIV = 5              // controller and DUT clock: 1000 MHz / FAST_DIV (5: 200 MHz)
) (
    input  wire       sysclk_p,
    input  wire       sysclk_n,
    input  wire       keep_u27,
    input  wire [3:0] keep_flash,
    output wire [7:0] led
);
    localparam WIN_N = 128;
    localparam WINW  = 64 + 64 * WIN_N;
    localparam MONW  = 32 + 256 * 8;

    // ---------------- clocks ----------------
    wire clk_ibuf, clk_fb, clk_sem_mmcm, clk_mmcm, locked;
    wire clk_sem;   // SEM controller, ICAP, monitor capture
    wire clk;       // FI controller, JTAG registers, DUT
    IBUFDS #(.DIFF_TERM("FALSE"), .IOSTANDARD("LVDS_25")) u_ibufds (.I(sysclk_p), .IB(sysclk_n), .O(clk_ibuf));
    MMCME2_BASE #(.CLKIN1_PERIOD(10.0), .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(10.0),
                  .CLKOUT0_DIVIDE_F(10.0), .CLKOUT1_DIVIDE(FAST_DIV), .BANDWIDTH("OPTIMIZED"),
                  .STARTUP_WAIT("FALSE")) u_mmcm (
        .CLKIN1(clk_ibuf), .CLKFBIN(clk_fb), .CLKFBOUT(clk_fb), .CLKFBOUTB(),
        .CLKOUT0(clk_sem_mmcm), .CLKOUT0B(), .CLKOUT1(clk_mmcm), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
        .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .LOCKED(locked), .PWRDWN(1'b0), .RST(1'b0));
    BUFG u_bufg_sem (.I(clk_sem_mmcm), .O(clk_sem));
    BUFG u_bufg (.I(clk_mmcm), .O(clk));

    // ---------------- SEM controller (clk_sem) ----------------
    wire st_hb, st_init, st_obs, st_cor, st_cls, st_inj, st_ess, st_unc;
    wire [7:0] mon_txdata;
    wire mon_txwrite, mon_txfull, mon_rxread;
    wire [39:0] inject_address;
    wire [31:0] icap_o, icap_i;
    wire icap_csib, icap_rdwrb, icap_req;
    wire fecc_crcerr, fecc_eccerr, fecc_eccerrsingle, fecc_syndromevalid;
    wire [12:0] fecc_syndrome;
    wire [25:0] fecc_far;
    wire [4:0] fecc_synbit;
    wire [6:0] fecc_synword;

    // injection command: toggle in clk, one-cycle strobe in clk_sem
    wire ctrl_strobe;
    reg inj_tog = 0;
    always @(posedge clk) if (ctrl_strobe) inj_tog <= ~inj_tog;
    (* ASYNC_REG = "TRUE" *) reg [1:0] inj_s = 0;
    reg inj_d = 0;
    always @(posedge clk_sem) begin inj_s <= {inj_s[0], inj_tog}; inj_d <= inj_s[1]; end
    wire inject_strobe = inj_s[1] ^ inj_d;

    sem_0 u_sem (
        .status_heartbeat(st_hb), .status_initialization(st_init), .status_observation(st_obs),
        .status_correction(st_cor), .status_classification(st_cls), .status_injection(st_inj),
        .status_essential(st_ess), .status_uncorrectable(st_unc),
        .monitor_txdata(mon_txdata), .monitor_txwrite(mon_txwrite), .monitor_txfull(mon_txfull),
        .monitor_rxdata(8'h00), .monitor_rxread(mon_rxread), .monitor_rxempty(1'b1),
        .inject_strobe(inject_strobe), .inject_address(inject_address),
        .icap_o(icap_o), .icap_csib(icap_csib), .icap_rdwrb(icap_rdwrb), .icap_i(icap_i),
        .icap_clk(clk_sem), .icap_request(icap_req), .icap_grant(1'b1),

        .fecc_crcerr(fecc_crcerr), .fecc_eccerr(fecc_eccerr), .fecc_eccerrsingle(fecc_eccerrsingle),
        .fecc_syndromevalid(fecc_syndromevalid), .fecc_syndrome(fecc_syndrome), .fecc_far(fecc_far),
        .fecc_synbit(fecc_synbit), .fecc_synword(fecc_synword));
    sem_cfg u_cfg (
        .icap_clk(clk_sem), .icap_o(icap_o), .icap_csib(icap_csib), .icap_rdwrb(icap_rdwrb), .icap_i(icap_i),
        .fecc_crcerr(fecc_crcerr), .fecc_eccerr(fecc_eccerr), .fecc_eccerrsingle(fecc_eccerrsingle),
        .fecc_syndromevalid(fecc_syndromevalid), .fecc_syndrome(fecc_syndrome), .fecc_far(fecc_far),
        .fecc_synbit(fecc_synbit), .fecc_synword(fecc_synword));

    // SEM monitor text capture; the JTAG freeze request is synchronised into clk_sem
    wire mon_freeze;
    (* ASYNC_REG = "TRUE" *) reg [1:0] frz_s = 0;
    always @(posedge clk_sem) frz_s <= {frz_s[0], mon_freeze};
    wire [MONW-1:0] mon_vec;
    sem_mon_cap u_mon (.clk(clk_sem), .txdata(mon_txdata), .txwrite(mon_txwrite), .freeze(frz_s[1]),
                       .txfull(mon_txfull), .mon(mon_vec));

    // SEM status flags synchronised into clk
    (* ASYNC_REG = "TRUE" *) reg [7:0] st_s1 = 0, st_s2 = 0;
    always @(posedge clk) begin
        st_s1 <= {st_unc, st_ess, st_inj, st_cls, st_cor, st_obs, st_init, st_hb};
        st_s2 <= st_s1;
    end

    // ---------------- JTAG registers and controller (clk) ----------------
    wire [255:0] cmd;
    wire cmd_valid;
    wire [511:0] status;
    wire [WINW-1:0] window;
    jtag_regs #(.WINW(WINW), .MONW(MONW)) u_jtag (
        .clk(clk), .status(status), .cmd(cmd), .cmd_valid(cmd_valid),
        .window(window), .mon(mon_vec), .mon_freeze(mon_freeze));

    wire [IN_W-1:0] dut_x;
    wire [CW-1:0] dut_y;
    fi_ctrl #(.IN_W(IN_W), .CW(CW), .NVEC(NVEC), .LATENCY(LATENCY), .WIN_N(WIN_N), .VEC_FILE(VEC_FILE), .FAR_FILE(FAR_FILE),
              .BUILD_ID(BUILD_ID), .DUT_ID(DUT_ID), .WIN_PIPE(1)) u_ctrl (
        .clk(clk), .cmd(cmd), .cmd_valid(cmd_valid), .status(status), .window(window),
        .inject_strobe(ctrl_strobe), .inject_address(inject_address),
        .sem_st(st_s2), .dut_x(dut_x), .dut_y(dut_y));

    (* KEEP_HIERARCHY = "yes" *) dut u_dut (.clk(clk), .x(dut_x), .y(dut_y));

    // ---------------- LEDs ----------------
    reg [26:0] beat = 0;
    always @(posedge clk) beat <= beat + 1'b1;
    reg [7:0] led_r = 0;
    always @(posedge clk) led_r <= {&{keep_u27, keep_flash}, locked, status[31], status[29], st_s2[5], st_s2[2], st_s2[0], beat[26]};
    assign led = led_r;   // as fi_top.v; [6] MMCM locked
endmodule
