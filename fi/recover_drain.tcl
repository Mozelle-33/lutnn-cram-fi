# Recover event-log records still held on chip after the host process died mid-campaign.
# The controller finishes its segment on its own and back-pressures when wr - rd reaches LOGD, so
# nothing is lost as long as the board kept power. Appends to <outdir>/events.tsv exactly as
# run_campaign.tcl would have, then runs one fault-free test as a sanity check.
# Usage: xsdb fi/recover_drain.tcl <outdir> <first_rec> <session>
lassign $argv outdir first session
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
fi_connect
set s [fi_status]
if {[dict get $s sig] != 0x4E4649} { error "FI design not running" }
if {[dict get $s running] || ![dict get $s done]} { error "controller still running: $s" }
set wr [dict get $s wr]
if {$first > $wr} { error "first_rec $first beyond wr $wr" }
puts "on-chip wr=$wr, recovering records $first..[expr {$wr - 1}]"
set ev [open [file join $outdir events.tsv] a]
set rd $first
while {$rd < $wr} {
    set recs [fi_window $rd]
    set n [expr {min($::FI_WINN, $wr - $rd)}]
    for {set i 0} {$i < $n} {incr i} {
        set r [expr "0x[lindex $recs $i]"]
        puts $ev [format "%d\t%d\t%d\t%d\t%d\t%d\t%d" [expr {$r & 0xFFFF}] [expr {($r >> 16) & 0x7F}] \
            [expr {($r >> 23) & 0x1F}] [expr {($r >> 28) & 0x3FFF}] [expr {($r >> 42) & 0x3FFF}] [expr {($r >> 56) & 0xF}] $session]
    }
    flush $ev
    incr rd $n
}
close $ev
set s [fi_status]
puts "recovered [expr {$rd - $first}] records; next position fi=[dict get $s fi] w=[dict get $s w] b=[dict get $s b]"
puts "inj=[dict get $s inj] nonsilent=[dict get $s nonsilent] persist=[dict get $s persist] timeouts=[dict get $s timeouts] elapsed_k=[dict get $s elapsed_k]"
set s [fi_test]
puts "fault-free check: mism=[dict get $s last_mism] corr=[dict get $s last_corr] golden=[dict get $s golden_corr]"
disconnect
exit 0
