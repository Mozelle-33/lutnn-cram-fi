// JTAG register file on BSCANE2 USER1..USER4 (XC7K325T IR codes 0x02, 0x03, 0x22, 0x23).
//   USER1 (read)  : STW-bit status snapshot. The clk-domain copy is frozen while USER1 is
//                   selected, so a DR capture never sees a half-updated counter.
//   USER2 (write) : CMDW-bit command; accepted on UPDATE only if bits [31:0] == KEY. The command
//                   word is held in the TCK domain and a toggle is synchronised into clk; the host
//                   must see the command tag echoed in the status before sending the next one.
//   USER3 (read)  : WINW-bit window (event records copied by the controller on request).
//   USER4 (read)  : MONW-bit SEM monitor text ring buffer.
// All read registers shift LSB first (bit 0 of the captured vector appears first on TDO).
`timescale 1ns / 1ps
module jtag_regs #(
    parameter STW  = 512,
    parameter CMDW = 256,
    parameter WINW = 8256,
    parameter MONW = 2080,
    parameter [31:0] KEY = 32'h5EEDF1A5
) (
    input  wire            clk,
    input  wire [STW-1:0]  status,       // clk domain, sampled while USER1 not selected
    output reg  [CMDW-1:0] cmd = 0,      // stable after cmd_valid (clk domain pulse)
    output reg             cmd_valid = 0,
    input  wire [WINW-1:0] window,       // stable while the host reads it (controller contract)
    input  wire [MONW-1:0] mon,
    output wire            mon_freeze    // clk domain: USER4 selected, stop writing mon
);
    // ---------------- BSCANE2 primitives ----------------
    wire tck1, cap1, sh1, sel1, tdi1;
    wire tck2, cap2, sh2, upd2, sel2, tdi2;
    wire tck3, cap3, sh3, sel3, tdi3;
    wire tck4, cap4, sh4, sel4, tdi4;
    reg [STW-1:0]  st_sr;
    reg [CMDW-1:0] cm_sr;
    reg [WINW-1:0] wn_sr;
    reg [MONW-1:0] mn_sr;

    BSCANE2 #(.JTAG_CHAIN(1)) u_bscan1 (.TCK(tck1), .CAPTURE(cap1), .SHIFT(sh1), .SEL(sel1), .TDI(tdi1),
        .TDO(st_sr[0]), .DRCK(), .RESET(), .RUNTEST(), .UPDATE(), .TMS());
    BSCANE2 #(.JTAG_CHAIN(2)) u_bscan2 (.TCK(tck2), .CAPTURE(cap2), .SHIFT(sh2), .SEL(sel2), .TDI(tdi2),
        .TDO(cm_sr[0]), .DRCK(), .RESET(), .RUNTEST(), .UPDATE(upd2), .TMS());
    BSCANE2 #(.JTAG_CHAIN(3)) u_bscan3 (.TCK(tck3), .CAPTURE(cap3), .SHIFT(sh3), .SEL(sel3), .TDI(tdi3),
        .TDO(wn_sr[0]), .DRCK(), .RESET(), .RUNTEST(), .UPDATE(), .TMS());
    BSCANE2 #(.JTAG_CHAIN(4)) u_bscan4 (.TCK(tck4), .CAPTURE(cap4), .SHIFT(sh4), .SEL(sel4), .TDI(tdi4),
        .TDO(mn_sr[0]), .DRCK(), .RESET(), .RUNTEST(), .UPDATE(), .TMS());

    // ---------------- USER1: status ----------------
    // st_shadow follows the status word in the clk domain and freezes as soon as the (synchronised)
    // USER1 select goes high; the TCK-domain capture then copies a stable, consistent snapshot.
    (* ASYNC_REG = "TRUE" *) reg [2:0] sel1_s = 0;
    reg [STW-1:0] st_shadow = 0;
    always @(posedge clk) begin
        sel1_s <= {sel1_s[1:0], sel1};
        if (!sel1_s[2] && !sel1_s[1]) st_shadow <= status;
    end
    always @(posedge tck1) begin
        if (sel1 && cap1) st_sr <= st_shadow;
        else if (sel1 && sh1) st_sr <= {tdi1, st_sr[STW-1:1]};
    end

    // ---------------- USER2: command ----------------
    // A shifted word becomes a command only if its low 32 bits equal KEY, so random JTAG activity
    // (TAP resets, cable enumeration after a host reboot) cannot start an operation. The accepted
    // word is held in cm_hold and announced to the clk domain by toggling cm_tog.
    reg [CMDW-1:0] cm_hold = 0;
    reg cm_tog = 0;
    always @(posedge tck2) begin
        if (sel2 && cap2) cm_sr <= cm_hold;
        else if (sel2 && sh2) cm_sr <= {tdi2, cm_sr[CMDW-1:1]};
        if (sel2 && upd2 && cm_sr[31:0] == KEY) begin
            cm_hold <= cm_sr;
            cm_tog  <= ~cm_tog;
        end
    end
    (* ASYNC_REG = "TRUE" *) reg [2:0] tog_s = 0;
    reg tog_d = 0;
    always @(posedge clk) begin
        tog_s <= {tog_s[1:0], cm_tog};
        tog_d <= tog_s[2];
        cmd_valid <= 1'b0;
        if (tog_s[2] != tog_d) begin
            cmd <= cm_hold;           // held stable in the TCK domain until the next UPDATE
            cmd_valid <= 1'b1;
        end
    end

    // ---------------- USER3: window ----------------
    // The controller changes the window only on the next LOAD_WINDOW, which the host issues after it
    // has read the current one, so no synchronisation is needed here.
    always @(posedge tck3) begin
        if (sel3 && cap3) wn_sr <= window;
        else if (sel3 && sh3) wn_sr <= {tdi3, wn_sr[WINW-1:1]};
    end

    // ---------------- USER4: SEM monitor text ----------------
    // While USER4 is selected the monitor ring is frozen (the SEM is stalled through txfull).
    (* ASYNC_REG = "TRUE" *) reg [2:0] sel4_s = 0;
    always @(posedge clk) sel4_s <= {sel4_s[1:0], sel4};
    assign mon_freeze = sel4_s[2] | sel4_s[1];
    always @(posedge tck4) begin
        if (sel4 && cap4) mn_sr <= mon;
        else if (sel4 && sh4) mn_sr <= {tdi4, mn_sr[MONW-1:1]};
    end
endmodule
