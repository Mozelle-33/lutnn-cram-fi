# Injection-loop timing: run the same n injections with different test/verify lengths and read the
# on-chip run timer (units of 1024 cycles at 100 MHz). The bits must contain no LUT-mode bits.
# Usage: xsdb fi/throughput.tcl <outfile> <far_first> <far_last> <fi> <w> <b> <n>
lassign $argv outfile far_first far_last pfi pw pb n
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
fi_connect
set s [fi_status]
if {[dict get $s sig] != 0x4E4649} { error "FI design not running" }
if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
set out [open $outfile a]
puts $out "# [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}] build=[dict get $s build_id] start=$pfi/$pw/$pb n=$n"
puts $out "ntest\tnverify\tflags\tinj\telapsed_k\tus_per_inj\tinj_per_s\tnonsilent"
# {ntest nverify flags}; flags b1 = skip the verify run, b2 = stop on persistent error
foreach cfg {{4096 1024 4} {4096 1024 6} {2048 1024 6} {1024 1024 6} {256 1024 6} {16 1024 6} {16 16 4} {16 256 4} {16 1024 4}} {
    lassign $cfg ntest nverify flags
    fi_clear
    fi_cfg $far_first $far_last 0 100 1 $ntest $nverify $flags
    fi_golden
    set s [fi_test]
    if {[dict get $s last_mism] != 0} { error "fault-free run differs from golden" }
    set tag [fi_cmd 2 [list [list $pfi 56 16] [list $pw 72 7] [list $pb 80 5] [list $n 88 32]]]
    set s [fi_wait $tag 600000]
    if {[dict get $s err] || [dict get $s persist]} { error "run failed: $s" }
    set inj [dict get $s inj]
    set ek [dict get $s elapsed_k]
    set us [expr {$ek * 1024 * 0.01 / $inj}]
    set line [format "%d\t%d\t%d\t%d\t%d\t%.3f\t%.0f\t%d" $ntest $nverify $flags $inj $ek $us [expr {1e6 / $us}] [dict get $s nonsilent]]
    puts $out $line; flush $out
    puts $line
}
fi_clear
fi_cfg $far_first $far_last 0 100 1 4096 1024 4
fi_golden
set s [fi_test]
puts "final fault-free check: mism=[dict get $s last_mism]"
close $out
disconnect
exit 0
