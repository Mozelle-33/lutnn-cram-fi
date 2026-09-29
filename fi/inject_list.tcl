# Targeted injection of listed bits with full reconfiguration after persistent corruption.
# Usage: xsdb fi/inject_list.tcl <bitfile> <list file> <out.tsv> <ntest> <far_first> <far_last>
# List lines: "fi w b [anything]" (fi = index into hw/gen/farlist.mem). For each bit: inject, run
# the test set, re-inject (restore), verify with the full test set; if the verify run still
# mismatches, the corruption is persistent and the FPGA is reprogrammed before the next bit.
# Output: fi w b mism corr verify_mism persistent rest-of-line
lassign $argv bitfile listf outf ntest far_first far_last
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
    while {!(([dict get $s sem] >> 2) & 1) && ([dict get $s sem] & 0x3E) != 0} {
        if {[clock milliseconds] - $t0 > 20000} { error "SEM not ready" }
        after 50; set s [fi_status]
    }
    if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
    fi_cfg $::far_first $::far_last 0 100 1 $::ntest $::ntest 0
    set s [fi_golden]
    set s [fi_test]
    if {[dict get $s last_mism] != 0} { error "fault-free run differs from golden" }
}
proc reprogram {} {
    targets -set -filter {name =~ "xc7k325t*"}
    fpga -file $::bitfile
    jtag targets -set -filter {name == "xc7k325t"}
    after 500
}
fi_connect
init_platform
set f [open $listf r]
set o [open $outf a]
set n 0; set np 0
foreach line [split [string trim [read $f]] "\n"] {
    lassign $line fi w b
    set a [sem_cmd $fi $w $b]
    fi_raw_inject $a
    set s1 [fi_test]
    fi_raw_inject $a
    set s2 [fi_test]
    set pers [expr {[dict get $s2 last_mism] != 0}]
    puts $o [format "%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s" $fi $w $b [dict get $s1 last_mism] [dict get $s1 last_corr] \
        [dict get $s2 last_mism] $pers [lrange $line 3 end]]
    flush $o
    incr n
    if {$pers} { incr np; reprogram; init_platform }
    if {$n % 20 == 0} { puts "done $n persistent $np" }
}
close $o
close $f
puts "DONE $n persistent $np"
disconnect
exit 0
