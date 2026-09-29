# Export every PIP used inside the DUT pblock rectangle with its net (for routing-bit attribution).
# Usage: vivado -mode batch -source hw/tcl/export_pips.tcl -tclargs <build name>
# Output: hw/build/<name>/dut_pips.csv  lines: tile,tile_type,pip,net
set name [lindex $argv 0]
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$name
open_checkpoint $out/routed.dcp
set fp [open $out/pblock_grid.txt r]; set g [read $fp]; close $fp
regexp {grid_x=(\d+)\.\.(\d+) grid_y=(\d+)\.\.(\d+)} $g -> gx0 gx1 gy0 gy1
set inrect [dict create]
foreach t [get_tiles -filter "GRID_POINT_X >= $gx0 && GRID_POINT_X <= $gx1 && GRID_POINT_Y >= $gy0 && GRID_POINT_Y <= $gy1"] {
    dict set inrect $t 1
}
set o [open $out/dut_pips.csv w]
puts $o "tile,tile_type,pip,net,net_type,driver"
set n 0
foreach net [get_nets -hier -filter {ROUTE_STATUS == ROUTED}] {
    set ps [get_pips -quiet -of_objects $net]
    if {$ps eq ""} { continue }
    set drv [get_cells -quiet -of_objects [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == OUT}]]
    set drv [expr {$drv eq "" ? "-" : [lindex $drv 0]}]
    set nt [get_property TYPE $net]
    foreach p $ps {
        set t [get_tiles -of_objects $p]
        if {[dict exists $inrect $t]} {
            puts $o "$t,[get_property TYPE $t],[get_property NAME $p],$net,$nt,$drv"
            incr n
        }
    }
}
close $o
puts "PIPS $n"
