# Exhaustive fault-injection campaign with a skip list, draining the event log to <outdir>/events.tsv.
# Usage: xsdb fi/run_campaign.tcl <outdir> <bitfile> <far_first> <far_last> <word_lo> <word_hi>
#                                 <ntest> <nverify> <flags> <skipfile|-> [start_lin] [end_lin]
# Bits are numbered linearly: lin = ((fi - far_first) * nwords + (w - word_lo)) * 32 + b.
# Bits in the skip file ("fi w b ..." lines) are not injected; the campaign runs the segments between
# them. flags: b0 log every injection, b1 skip verify run, b2 stop on persistent error.
# Recovery: persistent error (b2) or controller error state (SEM timeout) -> reprogram, resume.
# events.tsv lines: fi w b mism corr flags session
lassign $argv outdir bitfile far_first far_last word_lo word_hi ntest nverify flags skipfile start_lin end_lin
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
set nw [expr {$word_hi - $word_lo + 1}]                    ;# words per frame in the range
set total [expr {($far_last - $far_first + 1) * $nw * 32}] ;# bits in the range
if {$start_lin eq ""} { set start_lin 0 }
if {$end_lin eq ""} { set end_lin $total }
file mkdir $outdir
set ev [open [file join $outdir events.tsv] a]
set lg [open [file join $outdir run.log] a]
proc log {s} { puts $::lg "[clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}] $s"; flush $::lg; puts $s }
proc lin {fi w b} { expr {(($fi - $::far_first) * $::nw + ($w - $::word_lo)) * 32 + $b} }
proc pos_of {l} {
    set t [expr {$l / 32}]
    return [list [expr {$::far_first + $t / $::nw}] [expr {$::word_lo + $t % $::nw}] [expr {$l % 32}]]
}

# linear indices of the bits not to inject (LUT-mode bits of used SLICEMs), sorted
set skips {}
if {$skipfile ne "-"} {
    set f [open $skipfile r]
    foreach line [split [string trim [read $f]] "\n"] {
        lassign $line sfi sw sb
        lappend skips [lin $sfi $sw $sb]
    }
    close $f
    set skips [lsort -integer -unique $skips]
}

# Wait until the SEM has finished initialisation: observing (bit 2) or idle (only the heartbeat).
proc wait_sem_ready {} {
    set t0 [clock milliseconds]
    set s [fi_status]
    while {!(([dict get $s sem] >> 2) & 1) && ([dict get $s sem] & 0x3E) != 0} {
        if {[clock milliseconds] - $t0 > 20000} { error "SEM not ready: [fi_sem_str [dict get $s sem]]" }
        after 50
        set s [fi_status]
    }
    return $s
}
# Prepare a (re)configured device: SEM to IDLE, clear counters and log, configure the campaign,
# record the fault-free outputs and check them with one test run. Returns the golden correct count.
proc init_platform {} {
    set s [fi_status]
    if {[dict get $s sig] != 0x4E4649} { error "FI design not running" }
    set s [wait_sem_ready]
    if {([dict get $s sem] >> 2) & 1} { fi_sem_idle }
    fi_clear
    fi_cfg $::far_first $::far_last $::word_lo $::word_hi 1 $::ntest $::nverify $::flags
    set s [fi_golden]
    set g [dict get $s golden_corr]
    set s [fi_test]
    if {[dict get $s last_mism] != 0} { error "fault-free run differs from golden" }
    return $g
}
# Reconfiguration over JTAG repairs every upset, including persistent (SRL/LUT-RAM) ones.
proc reprogram {} {
    targets -set -filter {name =~ "xc7k325t*"}
    fpga -file $::bitfile
    jtag targets -set -filter {name == "xc7k325t"}
    after 500
}
# Copy every event record the controller has logged since the last call (window by window) to
# events.tsv. Loading a window also frees the log entries before it, which keeps the on-chip ring
# buffer from filling up. Returns the status read at the start.
proc drain {} {
    set s [fi_status]
    set wr [dict get $s wr]
    while {$::rd < $wr} {
        set recs [fi_window $::rd]
        set n [expr {min($::FI_WINN, $wr - $::rd)}]
        for {set i 0} {$i < $n} {incr i} {
            set r [expr "0x[lindex $recs $i]"]
            puts $::ev [format "%d\t%d\t%d\t%d\t%d\t%d\t%d" [expr {$r & 0xFFFF}] [expr {($r >> 16) & 0x7F}] \
                [expr {($r >> 23) & 0x1F}] [expr {($r >> 28) & 0x3FFF}] [expr {($r >> 42) & 0x3FFF}] [expr {($r >> 56) & 0xF}] $::session]
        }
        flush $::ev
        incr ::rd $n
    }
    return $s
}

fi_connect
log "ARGS $argv total_bits=$total skips=[llength $skips]"
set session 1
set golden [init_platform]
log "SESSION $session golden_corr=$golden/$ntest"
set rd 0
set sess_persist 0
set base_inj 0 ; set base_ns 0 ; set base_persist 0 ; set base_to 0 ; set base_ev 0
set cur $start_lin
set si 0
set nskipped 0
set tstart [clock milliseconds]
set tlog 0
set rate 9000.0                         ;# injections/s, updated from the measured segment times
# Run the range as segments between skip bits. Each segment is one START of the on-chip loop with
# max_inj = its length; while it runs, the log is drained. After a segment, a controller error (SEM
# timeout) or a new persistent error triggers reconfiguration and the campaign resumes at the
# position the controller reports.
while {$cur < $end_lin} {
    while {$si < [llength $skips] && [lindex $skips $si] < $cur} { incr si }
    if {$si < [llength $skips] && [lindex $skips $si] == $cur} { incr si; incr cur; incr nskipped; continue }
    set stop [expr {$si < [llength $skips] ? min([lindex $skips $si], $end_lin) : $end_lin}]
    set n [expr {$stop - $cur}]
    lassign [pos_of $cur] pfi pw pb
    set t0 [clock milliseconds]
    set tag [fi_cmd 2 [list [list $pfi 56 16] [list $pw 72 7] [list $pb 80 5] [list $n 88 32]]]
    # sleep for about 90 % of the expected run time before polling
    after [expr {int(max(1, $n / $rate * 1000.0 * 0.9))}]
    while {1} {
        set s [drain]
        if {[dict get $s err]} { break }
        if {[dict get $s tag] == $tag && [dict get $s done] && ![dict get $s running]} { break }
        after [expr {$n > 20000 ? 100 : 3}]
    }
    set dt [expr {max(1, [clock milliseconds] - $t0)}]
    if {$n > 2000} { set rate [expr {0.8 * $rate + 0.2 * $n * 1000.0 / $dt}] }
    set here [lin [dict get $s fi] [dict get $s w] [dict get $s b]]
    set now [clock milliseconds]
    if {$now - $tlog > 15000} {
        set tlog $now
        log [format "  lin %d/%d (%.1f%%) inj %d nonsilent %d persist %d skipped %d rate %.0f/s" $here $end_lin \
            [expr {100.0 * $here / $end_lin}] [expr {$base_inj + [dict get $s inj]}] [expr {$base_ns + [dict get $s nonsilent]}] \
            [expr {$base_persist + [dict get $s persist]}] $nskipped $rate]
    }
    set recover 0
    if {[dict get $s err]} {
        log "ERROR (SEM timeout) at [join [pos_of $here] /]; skipping it"
        puts $ev [format "%d\t%d\t%d\t-1\t-1\t2\t%d" {*}[pos_of $here] $session]
        set cur [expr {$here + 1}]
        set recover 1
    } elseif {[dict get $s persist] > $sess_persist} {
        # a verify run failed after restore: the DUT stays corrupted until reconfigured
        log "persistent error before [join [pos_of $here] /]"
        set cur $here
        set recover 1
    } else {
        set cur $here
    }
    set sess_persist [dict get $s persist]
    if {$recover} {
        incr base_inj [dict get $s inj]; incr base_ns [dict get $s nonsilent]; incr base_persist [dict get $s persist]
        incr base_to [dict get $s timeouts]; incr base_ev [dict get $s wr]
        reprogram
        incr session
        set golden [init_platform]
        set rd 0
        set sess_persist 0
        log "SESSION $session golden_corr=$golden/$ntest resume at [join [pos_of $cur] /]"
    }
}
set s [drain]
incr base_inj [dict get $s inj]; incr base_ns [dict get $s nonsilent]; incr base_persist [dict get $s persist]
incr base_to [dict get $s timeouts]; incr base_ev [dict get $s wr]
close $ev
set sec [expr {([clock milliseconds] - $tstart) / 1000.0}]
log [format "DONE inj %d nonsilent %d persist %d timeouts %d events %d skipped %d sessions %d elapsed %.1fs" \
    $base_inj $base_ns $base_persist $base_to $base_ev $nskipped $session $sec]
set m [open [file join $outdir summary.txt] w]
puts $m "golden_corr=$golden ntest=$ntest inj=$base_inj nonsilent=$base_ns persist=$base_persist timeouts=$base_to events=$base_ev skipped=$nskipped sessions=$session seconds=$sec far_first=$far_first far_last=$far_last word_lo=$word_lo word_hi=$word_hi start_lin=$start_lin end_lin=$end_lin skipfile=$skipfile"
close $m
close $lg
disconnect
exit 0
