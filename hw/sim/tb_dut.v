// Stand-alone DUT check: stream every test vector (one per cycle) and compare y with the
// software reference class (golden_sw.hex). Prints the mismatch count and exits.
`timescale 1ns / 1ps
module tb_dut;
    parameter IN_W = 160;
    parameter CW = 3;
    parameter LATENCY = 5;
    parameter NVEC = 4096;
    parameter VEC_FILE = "vectors.mem";
    parameter GOLD_FILE = "golden_sw.hex";
    reg clk = 0;
    always #5 clk = ~clk;
    reg [IN_W+CW-1:0] vec [0:NVEC-1];
    reg [3:0] gold [0:NVEC-1];
    initial begin $readmemh(VEC_FILE, vec); $readmemh(GOLD_FILE, gold); end
    reg [IN_W-1:0] x = 0;
    wire [CW-1:0] y;
    dut u_dut (.clk(clk), .x(x), .y(y));
    integer i = 0, mism = 0, corr = 0, checked = 0;
    always @(posedge clk) begin
        if (i < NVEC) x <= vec[i][IN_W-1:0];
        i <= i + 1;
        // x holds vector (i-1) during cycle i; y for it appears LATENCY cycles later
        if (i - 1 - LATENCY >= 0 && i - 1 - LATENCY < NVEC) begin
            checked = checked + 1;
            if (y !== gold[i-1-LATENCY][CW-1:0]) begin
                if (mism < 5) $display("MISMATCH vec %0d: hw %0d sw %0d", i-1-LATENCY, y, gold[i-1-LATENCY]);
                mism = mism + 1;
            end
            if (y === vec[i-1-LATENCY][IN_W+CW-1:IN_W]) corr = corr + 1;
        end
        if (i == NVEC + LATENCY + 3) begin
            $display("RESULT checked=%0d mismatches=%0d correct=%0d", checked, mism, corr);
            $finish;
        end
    end
endmodule
