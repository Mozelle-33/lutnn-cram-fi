# Export, for every input pin of every DUT LUT, the site pin and the routing node that reaches it
# (the INT-tile IMUX wire whose multiplexer is the last hop of the route).
# Usage: vivado -mode batch -source hw/tcl/export_lutpins.tcl -tclargs <build name>
# Output: hw/build/<name>/lut_pins.csv  lines: cell,bel,pin,site_pin,node,imux_wire,net
set name [lindex $argv 0]
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$name
open_checkpoint $out/routed.dcp
set o [open $out/lut_pins.csv w]
puts $o "cell,bel,pin,site_pin,node,imux_wire,net"
set n 0
foreach c [get_cells -hier -filter {NAME =~ u_dut/* && PRIMITIVE_GROUP == LUT}] {
    set bel [get_property BEL $c]
    foreach p [get_pins -of_objects $c -filter {DIRECTION == IN}] {
        set net [get_nets -quiet -of_objects $p]
        set sp [get_site_pins -quiet -of_objects $p]
        set nd [expr {$sp eq "" ? "" : [get_nodes -quiet -of_objects $sp]}]
        # the site-pin node is fed through a fixed CLB-tile PIP (CLBLL_IMUXk -> CLBLL_xx_A1) whose
        # uphill node contains the INT-tile IMUX wire
        set iw ""
        if {$nd ne ""} {
            foreach up [get_pips -quiet -uphill -of_objects $nd] {
                foreach un [get_nodes -quiet -uphill -of_objects $up] {
                    set w [get_wires -quiet -of_objects $un -filter {NAME =~ INT_*/IMUX*}]
                    if {$w ne ""} { set iw [lindex $w 0] }
                }
            }
        }
        puts $o "$c,$bel,[get_property REF_PIN_NAME $p],$sp,$nd,$iw,$net"
        incr n
    }
}
close $o
puts "LUTPINS $n"
