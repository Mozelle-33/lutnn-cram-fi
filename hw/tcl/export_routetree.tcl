# Export the routing trees of selected DUT nets: every PIP with its uphill and downhill node, and
# every sink pin with its node, so that the sinks downstream of any multiplexer can be computed.
# Usage: vivado -mode batch -source hw/tcl/export_routetree.tcl -tclargs <build name> [net pattern]
# Output: hw/build/<name>/route_pips.csv (net,pip,up_node,down_node)
#         hw/build/<name>/route_sinks.csv (net,cell,pin,node)
set name [lindex $argv 0]
set pat [expr {[llength $argv] > 1 ? [lindex $argv 1] : "u_dut/t_r*"}]
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$name
open_checkpoint $out/routed.dcp
set fp [open $out/route_pips.csv w]
set fs [open $out/route_sinks.csv w]
puts $fp "net,pip,up_node,down_node"
puts $fs "net,cell,pin,node"
set np 0
set ns 0
foreach net [get_nets -hier -filter "NAME =~ $pat && ROUTE_STATUS == ROUTED"] {
    foreach p [get_pips -quiet -of_objects $net] {
        set u [get_nodes -quiet -uphill -of_objects $p]
        set d [get_nodes -quiet -downhill -of_objects $p]
        puts $fp "$net,$p,$u,$d"
        incr np
    }
    foreach pin [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == IN}] {
        set sp [get_site_pins -quiet -of_objects $pin]
        set nd [expr {$sp eq "" ? "" : [get_nodes -quiet -of_objects $sp]}]
        puts $fs "$net,[get_cells -of_objects $pin],[get_property REF_PIN_NAME $pin],$nd"
        incr ns
    }
}
close $fp
close $fs
puts "ROUTETREE pips=$np sinks=$ns"
