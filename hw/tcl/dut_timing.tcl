# Worst setup slack of the paths inside the network under test (u_dut), per routed build.
# Usage: vivado -mode batch -source hw/tcl/dut_timing.tcl -tclargs <out.tsv> <build> [<build> ...]
# The builds are timed at 100 MHz; the DUT's own maximum frequency follows from the slack of its
# internal register-to-register paths (the harness paths are excluded).
set root [file normalize [file join [file dirname [info script]] .. ..]]
set outf [lindex $argv 0]
set o [open $outf w]
puts $o "build\tperiod_ns\twns_dut_ns\tdatapath_ns\tfmax_mhz\tlogic_levels"
foreach b [lrange $argv 1 end] {
    open_checkpoint $root/hw/build/$b/routed.dcp
    set cells [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ u_dut/*}]
    set p [get_timing_paths -setup -max_paths 1 -from $cells -to $cells]
    set slack [get_property SLACK $p]
    set req [get_property REQUIREMENT $p]
    set dp [get_property DATAPATH_DELAY $p]
    set lv [get_property LOGIC_LEVELS $p]
    set fmax [format %.0f [expr {1000.0 / ($req - $slack)}]]
    puts $o "$b\t$req\t$slack\t$dp\t$fmax\t$lv"
    flush $o
    close_design
}
close $o
