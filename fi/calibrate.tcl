# Inject each planned address (RAW_INJECT), run the test set, restore, run again.
# Usage: xsdb fi/calibrate.tcl <plan> <out.tsv> <ntest>
# Plan lines: addr_hex \t ... (first column is the 40-bit SEM command in hex); remaining columns are copied.
lassign $argv plan outf ntest
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
fi_connect
set s [fi_status]
if {[dict get $s sig] != 0x4E4649} { puts "STOP: FI design not running"; exit 3 }
puts "SEM=[fi_sem_str [dict get $s sem]]"
# wait until the SEM has finished initialisation: either observing (bit 2) or idle (no state flag
# other than the heartbeat), then put it into IDLE, the only state that accepts injections
set t0 [clock milliseconds]
while {!(([dict get $s sem] >> 2) & 1) && ([dict get $s sem] & 0x3E) != 0} {
    if {[clock milliseconds] - $t0 > 10000} { puts "STOP: SEM not ready"; exit 4 }
    after 50; set s [fi_status]
}
if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
# only the test length matters here (no campaign is run); record the fault-free outputs
fi_cfg 0 0 0 100 1 $ntest $ntest 0
set s [fi_golden]
puts "GOLDEN correct=[dict get $s golden_corr]/$ntest"
set s [fi_test]
puts "SELFTEST mism=[dict get $s last_mism] corr=[dict get $s last_corr]"
set f [open $plan r]
set o [open $outf w]
puts $o "addr\thw_mism\thw_corr\thw_mism_after_restore\tplan"
foreach line [split [string trim [read $f]] "\n"] {
    set addr [expr "0x[lindex [split $line \t] 0]"]
    fi_raw_inject $addr              ;# flip
    set s1 [fi_test]                 ;# outcome with the bit flipped
    fi_raw_inject $addr              ;# restore
    set s2 [fi_test]                 ;# must match the golden run again
    puts $o [format "%010llX\t%d\t%d\t%d\t%s" $addr [dict get $s1 last_mism] [dict get $s1 last_corr] [dict get $s2 last_mism] $line]
    flush $o
}
close $o
close $f
puts "MONITOR:\n[fi_monitor]"
disconnect
exit 0
