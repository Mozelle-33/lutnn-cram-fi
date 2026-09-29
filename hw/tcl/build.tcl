# Non-project build of the FI platform for one DUT.
# Usage: vivado -mode batch -source hw/tcl/build.tcl -tclargs <name> <latency> <in_w> <build_id> <dut_id> [rows]
#   rows: auto | 1 | 2   (DUT pblock height in clock regions starting at X0Y4)
# Outputs in hw/build/<name>/: fi_<name>.bit (+ .ebd/.ebc essential bits), routed.dcp, reports,
# dut_cells.csv (every DUT leaf cell with BEL/site/INIT), pblock.txt, isolation_audit.txt.
lassign $argv name lat in_w bid did rows y0 gen_name cw nvec
if {$rows eq ""} { set rows auto }
if {$y0 eq ""} { set y0 200 }
if {$gen_name eq ""} { set gen_name $name }
if {$cw eq ""} { set cw 3 }
if {$nvec eq ""} { set nvec 4096 }
set_param tcl.collectionResultDisplayLimit 0
set root [file normalize [file join [file dirname [info script]] .. ..]]
set gen  $root/hw/gen/$gen_name
set out  $root/hw/build/$name
file mkdir $out
cd $out
file copy -force $gen/vectors.mem $out/vectors.mem
file copy -force $root/hw/gen/farlist.mem $out/farlist.mem
set part xc7k325tffg900-2

create_project -in_memory -part $part
set_property target_language Verilog [current_project]
read_verilog [list $root/hw/rtl/fi_top.v $root/hw/rtl/fi_ctrl.v $root/hw/rtl/jtag_regs.v $gen/dut.v]
read_ip $root/hw/ip/sem_0/sem_0.xci
set_property GENERATE_SYNTH_CHECKPOINT false [get_files sem_0.xci]
generate_target all [get_ips sem_0]
read_xdc $root/hw/xdc/fi_board.xdc

# -max_dsp 0: every design is implemented in the fabric only (LUTs, carry chains, flip-flops), so that
# the networks are compared on the same kind of configuration memory
synth_design -top fi_top -part $part -max_dsp 0 -generic "IN_W=$in_w CW=$cw NVEC=$nvec LATENCY=$lat BUILD_ID=$bid DUT_ID=$did"
report_utilization -hierarchical -file $out/util_synth_hier.rpt

set dut_luts [llength [get_cells -hier -filter {PRIMITIVE_GROUP == LUT && NAME =~ u_dut/*}]]
set dut_ffs  [llength [get_cells -hier -filter {PRIMITIVE_GROUP == FLOP_LATCH && NAME =~ u_dut/*}]]
set dut_carry [llength [get_cells -hier -filter {REF_NAME == CARRY4 && NAME =~ u_dut/*}]]
puts "DUT_LUTS=$dut_luts DUT_FFS=$dut_ffs DUT_CARRY4=$dut_carry"

# ---- identity pin mapping for the DWN LUT6 primitives (INIT bit a = table entry a) ----
set dl [get_cells -quiet -hier -filter {REF_NAME == LUT6 && NAME =~ u_dut/lut_l*}]
if {[llength $dl]} { set_property LOCK_PINS {I0:A1 I1:A2 I2:A3 I3:A4 I4:A5 I5:A6} $dl }

# ---- pblocks ----
# The DUT gets a rectangle starting at the left edge of clock region X0Y4 (y0 = 200), one or two
# clock regions high, wide enough for about 55 % LUT utilisation. CONTAIN_ROUTING keeps its nets
# inside the rectangle and EXCLUDE_PLACEMENT keeps other logic out, so that injecting into the
# rectangle's frames can only disturb the harness through the DUT outputs. The harness is confined
# to clock regions X0Y0..X0Y2.
if {$rows eq "auto"} { set rows [expr {$dut_luts > 7000 ? 2 : 1}] }
set y1 [expr {$y0 + 50 * $rows - 1}]
set capacity_per_col [expr {400 * $rows}]
set ncols [expr {int(ceil(double(max($dut_luts, $dut_ffs / 2)) / (0.55 * $capacity_per_col))) + 1}]
set xs [lsort -integer -unique [lmap s [get_sites -of_objects [get_clock_regions X0Y[expr {$y0 / 50}]] -filter {SITE_TYPE =~ SLICE*}] {regsub {SLICE_X(\d+)Y\d+} $s {\1}}]]
set nsx [expr {min(2 * $ncols, [llength $xs])}]
# The right edge must be an *_R CLB tile: an *_L tile's interconnect (INT_L) sits to its right and
# would fall outside the rectangle, making its pins unroutable under CONTAIN_ROUTING.
while {$nsx < [llength $xs] && [string match "*_L" [get_property TYPE [get_tiles -of_objects [get_sites SLICE_X[lindex $xs [expr {$nsx - 1}]]Y$y0]]]]} {
    incr nsx 2
}
set xmin [lindex $xs 0]
set xmax [lindex $xs [expr {$nsx - 1}]]
create_pblock pb_dut
add_cells_to_pblock pb_dut [get_cells u_dut]
resize_pblock pb_dut -add "SLICE_X${xmin}Y${y0}:SLICE_X${xmax}Y${y1}"
set_property CONTAIN_ROUTING true [get_pblocks pb_dut]
set_property EXCLUDE_PLACEMENT true [get_pblocks pb_dut]
create_pblock pb_harness
add_cells_to_pblock pb_harness [get_cells {u_ctrl u_jtag u_sem u_mon}]
resize_pblock pb_harness -add {CLOCKREGION_X0Y0:CLOCKREGION_X0Y2}
set_property CONTAIN_ROUTING true [get_pblocks pb_harness]
set fp [open $out/pblock.txt w]
puts $fp "name=$name rows=$rows ncols=$ncols slice_x=$xmin..$xmax y=$y0..$y1 dut_luts=$dut_luts dut_ffs=$dut_ffs dut_carry4=$dut_carry latency=$lat"
close $fp

opt_design
place_design
phys_opt_design
route_design
report_timing_summary -file $out/timing.rpt
report_utilization -hierarchical -file $out/util_routed_hier.rpt
report_utilization -pblocks pb_dut -file $out/util_pblock_dut.rpt
set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
puts "TIMING WNS=$wns WHS=$whs"
write_checkpoint -force $out/routed.dcp

# ---- export DUT leaf cells (for bit attribution) ----
set fp [open $out/dut_cells.csv w]
puts $fp "cell,ref,site,bel,init"
foreach c [get_cells -hier -filter {IS_PRIMITIVE && NAME =~ u_dut/*}] {
    set init [get_property -quiet INIT $c]
    puts $fp "$c,[get_property REF_NAME $c],[get_property -quiet LOC $c],[get_property -quiet BEL $c],$init"
}
close $fp

# ---- isolation audit: nets routed through the DUT pblock rectangle (all tiles, incl. INT) that do
# not connect to the DUT ----
# Expected result: only the device-wide GND and VCC nets. They appear under many hierarchical alias
# names (e.g. u_sem/inst/controller_kcpsm3/lopt); hw/tcl/audit_nets.tcl lists each flagged net's
# type and driver to confirm that they are all constant nets.
set ct [get_tiles -of_objects [get_sites -of_objects [get_pblocks pb_dut]]]
set gxs [get_property GRID_POINT_X $ct]
set gys [get_property GRID_POINT_Y $ct]
set gx0 [tcl::mathfunc::min {*}$gxs]; set gx1 [tcl::mathfunc::max {*}$gxs]
set gy0 [tcl::mathfunc::min {*}$gys]; set gy1 [tcl::mathfunc::max {*}$gys]
set rt [dict create]
foreach t [get_tiles -filter "GRID_POINT_X >= $gx0 && GRID_POINT_X <= $gx1 && GRID_POINT_Y >= $gy0 && GRID_POINT_Y <= $gy1"] { dict set rt $t 1 }
set fp [open $out/pblock_grid.txt w]
puts $fp "grid_x=$gx0..$gx1 grid_y=$gy0..$gy1 tiles=[dict size $rt]"
close $fp
set foreign [dict create]
foreach n [get_nets -hier -quiet -filter {ROUTE_STATUS == ROUTED}] {
    if {[string match u_dut/* $n]} { continue }
    set touches_dut 0
    foreach p [get_pins -quiet -of_objects $n -leaf] { if {[string match u_dut/* $p]} { set touches_dut 1; break } }
    if {$touches_dut} { continue }
    foreach x [get_tiles -quiet -of_objects [get_pips -quiet -of_objects $n]] {
        if {[dict exists $rt $x]} { dict set foreign $n 1; break }
    }
}
set fp [open $out/isolation_audit.txt w]
puts $fp "foreign_nets=[dict size $foreign]"
foreach n [dict keys $foreign] { puts $fp $n }
close $fp
puts "ISOLATION foreign_nets=[dict size $foreign]"

# essential-bits files (.ebd/.ebc) for the essential-bit statistics, and the logic-location file
# (.ll) that maps flip-flop and memory bits to frame addresses
set_property BITSTREAM.SEU.ESSENTIALBITS YES [current_design]
write_bitstream -force -logic_location_file $out/fi_$name.bit
puts "BUILD_DONE $out/fi_$name.bit"
