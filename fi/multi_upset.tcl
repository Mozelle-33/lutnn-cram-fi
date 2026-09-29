# Accumulated upsets: for each trial, inject K bits, run the test set, restore the K bits and verify.
# Usage: xsdb fi/multi_upset.tcl <bitfile> <trials file> <out.tsv> <ntest> <far_first> <far_last> [first_tid]
# Trial lines (analysis/multi_upset.py gen): "tid K p fi w b fi w b ...". A trial whose verify run
# still mismatches, or whose injections failed, is logged with its flag and the FPGA is reprogrammed.
# Output: tid K p mism corr verify_mism flags seconds (flags: 1 persistent/verify, 2 injection error)
lassign $argv bitfile trialf outf ntest far_first far_last first_tid
if {$first_tid eq ""} { set first_tid 0 }
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
set fl [open [file join $root hw gen farlist.mem] r]
set fars [split [string trim [read $fl]] "\n"]
close $fl
# 40-bit SEM injection command for frame fi of the FAR list, word w, bit b (layout in fi_ctrl.v)
proc sem_cmd {fi w b} {
    set far [expr "0x[lindex $::fars $fi]"]
    set bt [expr {($far >> 23) & 3}]; set h [expr {($far >> 22) & 1}]; set row [expr {($far >> 17) & 31}]
    set col [expr {($far >> 7) & 1023}]; set mi [expr {$far & 127}]
    return [expr {($bt << 35) | ($h << 34) | ($row << 29) | ($col << 19) | ($mi << 12) | ($w << 5) | $b}]
}
# Prepare a freshly configured device: wait until the SEM has finished initialisation (observing,
# or idle with only the heartbeat flag), put it into IDLE so that it accepts injections, record
# the fault-free outputs and check that a test run reproduces them.
proc init_platform {} {
    set t0 [clock milliseconds]
    set s [fi_status]
    if {[dict get $s sig] != 0x4E4649} { error "FI design not running" }
    while {!(([dict get $s sem] >> 2) & 1) && ([dict get $s sem] & 0x3E) != 0} {
        if {[clock milliseconds] - $t0 > 20000} { error "SEM not ready" }
        after 50; set s [fi_status]
    }
    if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
    fi_clear
    fi_cfg $::far_first $::far_last 0 100 1 $::ntest $::ntest 0
    set s [fi_golden]
    set s [fi_test]
    if {[dict get $s last_mism] != 0} { error "fault-free run differs from golden" }
    return [dict get $s golden_corr]
}
proc reprogram {} {
    targets -set -filter {name =~ "xc7k325t*"}
    fpga -file $::bitfile
    jtag targets -set -filter {name == "xc7k325t"}
    after 500
}
proc inject_all {addrs} {
    set bad 0
    foreach a $addrs {
        set s [fi_raw_inject $a]
        if {[dict get $s err]} { incr bad }
    }
    return $bad
}
fi_connect
set golden [init_platform]
puts "golden $golden/$ntest"
set f [open $trialf r]
set o [open $outf a]
puts $o "# [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}] $bitfile golden=$golden ntest=$ntest"
set n 0; set nre 0
foreach line [split [string trim [read $f]] "\n"] {
    set tid [lindex $line 0]; set k [lindex $line 1]; set p [lindex $line 2]
    if {$tid < $first_tid} { continue }
    set addrs {}
    foreach {fi w b} [lrange $line 3 end] { lappend addrs [sem_cmd $fi $w $b] }
    set t0 [clock milliseconds]
    set flags 0
    if {[inject_all $addrs]} { set flags 2 }
    set s1 [fi_test]
    if {[inject_all [lreverse $addrs]]} { set flags 2 }
    set s2 [fi_test]
    if {[dict get $s2 last_mism] != 0} { set flags [expr {$flags | 1}] }
    set sec [expr {([clock milliseconds] - $t0) / 1000.0}]
    puts $o [format "%d\t%d\t%s\t%d\t%d\t%d\t%d\t%.2f" $tid $k $p [dict get $s1 last_mism] [dict get $s1 last_corr] \
        [dict get $s2 last_mism] $flags $sec]
    flush $o
    incr n
    if {$flags} { incr nre; reprogram; set golden [init_platform] }
    if {$n % 20 == 0} { puts "trial $tid K=$k done=$n reprogrammed=$nre" }
}
close $o
close $f
puts "DONE trials $n reprogrammed $nre"
disconnect
exit 0
