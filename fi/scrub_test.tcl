# Does SEM scrubbing repair persistent (SRL-mode) corruption?
# Usage: xsdb fi/scrub_test.tcl <bitfile> <list file> <out.tsv> <ntest> <far_first> <far_last>
# For each listed bit: inject, test, re-inject (restore), test, let the SEM controller scan in the
# observation state (it detects and repairs single-bit frame errors), return to idle, test again,
# record SEM status/monitor text, then reprogram so every bit starts from a clean device.
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
    fi_golden
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
set o [open $outf w]
puts $o "fi\tw\tb\tmism_fault\tmism_after_restore\tmism_after_scrub\tsem_corrections\tsem_state_after_scan\tmonitor"
foreach line [split [string trim [read $f]] "\n"] {
    lassign $line fi w b
    set a [sem_cmd $fi $w $b]
    fi_raw_inject $a
    set m1 [dict get [fi_test] last_mism]
    fi_raw_inject $a
    set m2 [dict get [fi_test] last_mism]
    # hand the device to the SEM: in observation it scans all frames, corrects single-bit ECC errors
    # and reports what it cannot correct on its monitor interface
    set c0 [dict get [fi_status] sem_corr]
    fi_sem_observe
    after 1500                                ;# several full scans (about 25 ms each)
    set s [fi_status]
    set sem_after [fi_sem_str [dict get $s sem]]
    set c1 [dict get $s sem_corr]
    set ok [catch {fi_sem_idle}]
    set m3 [expr {$ok ? -1 : [dict get [fi_test] last_mism]}]
    set mon [string map {"\n" " " "\t" " "} [string range [fi_monitor] end-160 end]]
    puts $o "$fi\t$w\t$b\t$m1\t$m2\t$m3\t[expr {$c1 - $c0}]\t$sem_after\t$mon"
    flush $o
    puts "bit $fi/$w/$b: fault $m1, after restore $m2, after SEM scrub $m3, corrections [expr {$c1 - $c0}], SEM $sem_after"
    reprogram
    init_platform
}
close $o
close $f
disconnect
exit 0
