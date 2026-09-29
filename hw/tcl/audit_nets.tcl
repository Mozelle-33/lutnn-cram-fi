# Inspect the nets flagged by the isolation audit: type, driver and DUT-rectangle tiles they touch.
# Usage: vivado -mode batch -source hw/tcl/audit_nets.tcl -tclargs <name>
set name [lindex $argv 0]
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set out $root/hw/build/$name
open_checkpoint $out/routed.dcp
set fp [open $out/pblock_grid.txt r]; set g [read $fp]; close $fp
regexp {grid_x=(\d+)\.\.(\d+) grid_y=(\d+)\.\.(\d+)} $g -> gx0 gx1 gy0 gy1
set fp [open $out/isolation_audit.txt r]; set lines [split [string trim [read $fp]] "\n"]; close $fp
set rep [open $out/isolation_detail.txt w]
foreach n [lrange $lines 1 end] {
    set net [get_nets -quiet $n]
    if {$net eq ""} { puts $rep "$n : not found"; continue }
    set drv [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == OUT}]
    set dref [expr {$drv eq "" ? "-" : [get_property REF_NAME [get_cells -of_objects $drv]]}]
    set inside {}
    foreach t [get_tiles -quiet -of_objects [get_pips -quiet -of_objects $net]] {
        set x [get_property GRID_POINT_X $t]; set y [get_property GRID_POINT_Y $t]
        if {$x >= $gx0 && $x <= $gx1 && $y >= $gy0 && $y <= $gy1} { lappend inside $t }
    }
    puts $rep "$n : type=[get_property TYPE $net] driver=$dref tiles_in_dut=[llength $inside] [lrange $inside 0 5]"
}
close $rep
