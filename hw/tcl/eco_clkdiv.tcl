# Clock-rate control: the same routed design at half the clock rate.
# Usage: vivado -mode batch -source hw/tcl/eco_clkdiv.tcl -tclargs <build> <new build> <build id>
# Opens hw/build/<build>/routed.dcp, inserts an MMCM (100 MHz in, 50 MHz out) between the clock input
# buffer and the global clock buffer in the clock region of the input pin, routes only the changed
# clock nets and writes hw/build/<new build>/fi_<new build>.bit with its essential-bit files. The
# routing and cells of the network under test are not touched, so both bitstreams configure the
# DUT region identically (checked by comparing the frames of the DUT columns).
lassign $argv build newb bid
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$newb
file mkdir $out
open_checkpoint $root/hw/build/$build/routed.dcp
# lock the placement and routing of everything that exists, except the clock-input net, which now
# ends at the MMCM instead of the global buffer
lock_design -level routing
set ibuf_net [get_nets clk_ibuf]
set_property DONT_TOUCH false $ibuf_net
set_property DONT_TOUCH false [get_cells {u_bufg u_ibufds}]
set_property IS_ROUTE_FIXED false $ibuf_net
route_design -unroute -nets $ibuf_net
set bufg_i [get_pins u_bufg/I]
disconnect_net -net $ibuf_net -objects $bufg_i
create_cell -reference MMCME2_BASE u_mmcm_eco
set_property -dict {CLKIN1_PERIOD 10.0 CLKIN2_PERIOD 10.0 DIVCLK_DIVIDE 1 CLKFBOUT_MULT_F 10.0
    CLKOUT0_DIVIDE_F 20.0 BANDWIDTH OPTIMIZED COMPENSATION INTERNAL SS_EN FALSE SS_MODE CENTER_HIGH
    SS_MOD_PERIOD 10000 STARTUP_WAIT FALSE} [get_cells u_mmcm_eco]
connect_net -net $ibuf_net -objects [get_pins u_mmcm_eco/CLKIN1]
create_net clk_mmcm_out
connect_net -net clk_mmcm_out -objects [list [get_pins u_mmcm_eco/CLKOUT0] $bufg_i]
create_net clk_mmcm_fb
connect_net -net clk_mmcm_fb -objects [list [get_pins u_mmcm_eco/CLKFBOUT] [get_pins u_mmcm_eco/CLKFBIN]]
create_cell -reference GND u_gnd_eco
create_net gnd_eco
connect_net -net gnd_eco -objects [list [get_pins u_gnd_eco/G] [get_pins u_mmcm_eco/RST] [get_pins u_mmcm_eco/PWRDWN]]
# the MMCM sits in the clock region of the input pin, far from the DUT region (clock region X0Y4)
set_property LOC MMCME2_ADV_X0Y1 [get_cells u_mmcm_eco]
place_design
route_design
report_route_status -file $out/route_status.txt
report_timing_summary -file $out/timing.rpt
report_clocks -file $out/clocks.rpt
# the build id is the value the harness reports; it is a constant in the design and cannot be
# changed here, so the host identifies this bitstream by its file name
set_property BITSTREAM.SEU.ESSENTIALBITS YES [current_design]
write_bitstream -force $out/fi_$newb.bit
write_checkpoint -force $out/routed.dcp
puts "ECO_DONE $newb"
