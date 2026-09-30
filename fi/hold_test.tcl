# Does a disconnected multiplexer keep its value? Inject one bit, then run the test set repeatedly for
# up to a minute (the other routing keeps toggling) and compare every run with the campaign result.
# Usage: xsdb fi/hold_test.tcl <bitfile> <list file> <out.tsv> <ntest> <far_first> <far_last> [t1,t2,...]
# List lines: "fi w b expected_mism expected_corr [...]" (further fields ignored; analysis/freeze_hold.py
# writes the outcomes of a frozen 0 and a frozen 1). Output: fi w b t_s mism corr expected_mism
lassign $argv bitfile listf outf ntest far_first far_last
# optional: the test times in seconds after the upset, e.g. 0,0.1,1,10 (the batch wrapper of xsdb may
# split the list at the commas into separate arguments, so all remaining arguments are joined)
set times [split [join [lrange $argv 6 end] ,] ,]
if {![llength $times]} { set times {0 1 2 5 10 20 30 60} }
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
fi_connect
set s [fi_status]
if {[dict get $s sig] != 0x4E4649} { error "FI design not running" }
if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
fi_clear
# verify runs of 1024 vectors, as in the campaigns, so that the inputs rest on vector 1024
fi_cfg $far_first $far_last 0 100 1 $ntest 1024 0
fi_golden
set s [fi_test]
if {[dict get $s last_mism] != 0} { error "fault-free run differs from golden" }
set o [open $outf a]
set f [open $listf r]
foreach line [split [string trim [read $f]] "\n"] {
    lassign $line fi w b em ec
    # leave the inputs on vector 1024 as after a campaign verify run: run 1024 vectors first
    fi_cfg $far_first $far_last 0 100 1 1024 1024 0
    fi_test
    fi_cfg $far_first $far_last 0 100 1 $ntest 1024 0
    set a [sem_cmd $fi $w $b]
    fi_raw_inject $a
    set t0 [clock milliseconds]
    foreach t $times {
        while {[clock milliseconds] - $t0 < $t * 1000} { after 5 }
        set s1 [fi_test]
        set el [expr {([clock milliseconds] - $t0) / 1000.0}]
        puts $o [format "%d\t%d\t%d\t%.1f\t%d\t%d\t%d" $fi $w $b $el [dict get $s1 last_mism] [dict get $s1 last_corr] $em]
        flush $o
    }
    fi_raw_inject $a
    set s2 [fi_test]
    puts "bit $fi/$w/$b expected $em: done, verify mism [dict get $s2 last_mism]"
    if {[dict get $s2 last_mism] != 0} { error "persistent corruption after restore" }
}
close $f
close $o
disconnect
exit 0
