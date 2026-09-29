# Rewrite the INIT of the DWN LUT6 cells in a routed checkpoint (identical placement and routing)
# and write a new bitstream with essential bits.
# Usage: vivado -mode batch -source hw/tcl/reinit_luts.tcl -tclargs <src build> <new name> <init csv>
#   init csv: cell,init (e.g. u_dut/lut_l0_17,64'h0123456789ABCDEF)
lassign $argv src new csv
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set sdir $root/hw/build/$src
set out $root/hw/build/$new
file mkdir $out
open_checkpoint $sdir/routed.dcp
set f [open $csv r]
set n 0
foreach line [split [string trim [read $f]] "\n"] {
    lassign [split $line ,] cell init
    set c [get_cells $cell]
    set_property INIT $init $c
    incr n
}
close $f
puts "REINIT $n cells"
write_checkpoint -force $out/routed.dcp
set fp [open $out/dut_cells.csv w]
puts $fp "cell,ref,site,bel,init"
foreach c [get_cells -hier -filter {IS_PRIMITIVE && NAME =~ u_dut/*}] {
    puts $fp "$c,[get_property REF_NAME $c],[get_property -quiet LOC $c],[get_property -quiet BEL $c],[get_property -quiet INIT $c]"
}
close $fp
# placement and routing are unchanged, so the pblock (and hence the campaign range) is the source's
file copy -force $sdir/pblock.txt $out/pblock.txt
file copy -force $sdir/pblock_grid.txt $out/pblock_grid.txt
set_property BITSTREAM.SEU.ESSENTIALBITS YES [current_design]
write_bitstream -force $out/fi_$new.bit
puts "BUILD_DONE $out/fi_$new.bit"
