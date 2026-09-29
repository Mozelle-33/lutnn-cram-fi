# For every wire listed in hw/build/<name>/bridge_wires.txt ("TILE/WIRE" per line), the node it belongs
# to and the net routed on that node (empty if none).
# Usage: vivado -mode batch -source hw/tcl/export_wirenets.tcl -tclargs <build name>
# Output: hw/build/<name>/wire_nets.csv  lines: wire,node,net
set name [lindex $argv 0]
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$name
open_checkpoint $out/routed.dcp
set f [open $out/bridge_wires.txt r]
set wires [split [string trim [read $f]] "\n"]
close $f
set o [open $out/wire_nets.csv w]
puts $o "wire,node,net"
set n 0
foreach w $wires {
    set wo [get_wires -quiet $w]
    if {$wo eq ""} { continue }
    set nd [get_nodes -quiet -of_objects $wo]
    set net [expr {$nd eq "" ? "" : [lindex [get_nets -quiet -of_objects $nd] 0]}]
    puts $o "$w,$nd,$net"
    incr n
}
close $o
puts "WIRENETS $n"
