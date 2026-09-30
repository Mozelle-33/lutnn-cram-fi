# Clock-rate control for a build with a separate network clock (fi_top_dual.v): the same routed design
# with another divider on the MMCM output that clocks the FI controller and the DUT.
# Usage: vivado -mode batch -source hw/tcl/eco_fastdiv.tcl -tclargs <build> <new build> <divide>
#   (VCO 1000 MHz: divide 5 = 200 MHz, 10 = 100 MHz)
# Only the MMCM setting changes; placement and routing are those of hw/build/<build>/routed.dcp, so
# both bitstreams configure the DUT region identically.
lassign $argv build newb div
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$newb
file mkdir $out
open_checkpoint $root/hw/build/$build/routed.dcp
set_property CLKOUT1_DIVIDE $div [get_cells u_mmcm]
report_timing_summary -file $out/timing.rpt
report_clocks -file $out/clocks.rpt
set_property BITSTREAM.SEU.ESSENTIALBITS YES [current_design]
write_bitstream -force $out/fi_$newb.bit
write_checkpoint -force $out/routed.dcp
puts "ECO_DONE $newb CLKOUT1_DIVIDE=[get_property CLKOUT1_DIVIDE [get_cells u_mmcm]]"
