# Control experiment for the frozen-input effect: inject listed bits while the DUT inputs rest on a
# chosen idle vector (instead of vector 1024, where the campaigns leave them), then test and restore.
# A run of `idle` vectors before each injection leaves vector `idle` on the inputs.
# Usage: xsdb fi/idle_control.tcl <bitfile> <list file> <out.tsv> <idle vector> <far_first> <far_last>
# List lines: "fi w b ...". Output: fi w b mism corr verify_mism
lassign $argv bitfile listf outf idle far_first far_last
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
fi_cfg $far_first $far_last 0 100 1 4096 4096 0
fi_golden
if {[dict get [fi_test] last_mism] != 0} { error "fault-free run differs from golden" }
set o [open $outf w]
set f [open $listf r]
set n 0
foreach line [split [string trim [read $f]] "\n"] {
    lassign $line fi w b
    fi_cfg $far_first $far_last 0 100 1 $idle 4096 0     ;# pre-run of `idle` vectors
    fi_test
    fi_cfg $far_first $far_last 0 100 1 4096 4096 0
    set a [sem_cmd $fi $w $b]
    fi_raw_inject $a
    set s1 [fi_test]
    fi_raw_inject $a
    set s2 [fi_test]
    puts $o [format "%d\t%d\t%d\t%d\t%d\t%d" $fi $w $b [dict get $s1 last_mism] [dict get $s1 last_corr] [dict get $s2 last_mism]]
    if {[dict get $s2 last_mism] != 0} { error "persistent corruption after restore at $fi/$w/$b" }
    if {[incr n] % 1000 == 0} { flush $o; puts "done $n" }
}
close $f
close $o
puts "DONE $n"
disconnect
exit 0
