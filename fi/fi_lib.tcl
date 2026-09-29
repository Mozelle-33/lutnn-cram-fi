# xsdb helper library for the FI platform (hw/rtl/fi_top.v). Source it from xsdb scripts.
# JTAG IR codes (XC7K325T, 6-bit IR): USER1 0x02 status, USER2 0x03 command, USER3 0x22 window,
# USER4 0x23 SEM monitor text, IDCODE 0x09. Every access ends with IR = IDCODE so the USER
# registers are deselected (the status snapshot is frozen only while USER1 is selected).
set ::FI_KEY 0x5EEDF1A5                              ;# must match KEY in jtag_regs.v
set ::FI_TAG [expr {[clock milliseconds] & 0x7FFF}]  ;# command tags start at a random value per session
set ::FI_STW 512                                     ;# status width (USER1)
set ::FI_WINN 128                                    ;# records per window (WIN_N in fi_ctrl.v)
set ::FI_WINW [expr {64 + 64 * $::FI_WINN}]          ;# window width (USER3)
set ::FI_MONW [expr {32 + 256 * 8}]                  ;# SEM monitor ring width (USER4)

# Connect to the local hw_server and select the FPGA as the JTAG target.
proc fi_connect {} {
    connect -url tcp:localhost:3121
    after 200
    jtag targets -set -filter {name == "xc7k325t"}
}

# Read a w-bit USER data register: select it (IR), capture and shift it out with TDI = 0, and put
# IDCODE back into the IR so that the USER register is deselected again. Returns a bit string.
proc fi_scan {ir w} {
    set seq [jtag sequence]
    $seq irshift -state IDLE -integer 6 $ir
    $seq state IDLE 32
    $seq drshift -capture -state IDLE -tdi 0 $w
    $seq irshift -state IDLE -integer 6 9
    set b [lindex [$seq run -bits] 0]
    $seq delete
    return $b
}

proc fi_val {b lo w} {
    # bit string is LSB first; returns an unsigned integer (Tcl bignum)
    set s [string reverse [string range $b $lo [expr {$lo + $w - 1}]]]
    if {$s eq ""} { return 0 }
    return [expr "0b$s"]
}

# Status snapshot as a dict; bit positions follow the `status` assignment in hw/rtl/fi_ctrl.v.
proc fi_status {} {
    set b [fi_scan 2 $::FI_STW]
    set d [dict create]
    dict set d sig      [fi_val $b 0 24]
    dict set d st       [fi_val $b 24 5]
    dict set d running  [fi_val $b 29 1]
    dict set d done     [fi_val $b 30 1]
    dict set d err      [fi_val $b 31 1]
    dict set d sem      [fi_val $b 32 8]
    dict set d last_op  [fi_val $b 40 8]
    dict set d tag      [fi_val $b 48 16]
    dict set d inj      [fi_val $b 64 32]
    dict set d wr       [fi_val $b 96 32]
    dict set d rd       [fi_val $b 128 32]
    dict set d nonsilent [fi_val $b 160 32]
    dict set d persist  [fi_val $b 192 32]
    dict set d timeouts [fi_val $b 224 32]
    dict set d b        [fi_val $b 256 5]
    dict set d w        [fi_val $b 264 7]
    dict set d fi       [fi_val $b 272 16]
    dict set d last_mism [fi_val $b 288 14]
    dict set d last_corr [fi_val $b 304 14]
    dict set d golden_corr [fi_val $b 320 14]
    dict set d ntest    [fi_val $b 336 14]
    dict set d elapsed_k [fi_val $b 352 32]
    dict set d sem_corr [fi_val $b 384 32]
    dict set d win_start [fi_val $b 416 32]
    dict set d latency  [fi_val $b 448 16]
    dict set d build_id [fi_val $b 464 16]
    dict set d dut_id   [fi_val $b 480 16]
    dict set d win_tag  [fi_val $b 496 16]
    return $d
}

# SEM state flags as a readable list, e.g. "hb,obs".
proc fi_sem_str {s} {
    # {unc, ess, inj, cls, cor, obs, init, hb}
    set names {hb init obs cor cls inj ess unc}
    set out {}
    for {set i 0} {$i < 8} {incr i} { if {($s >> $i) & 1} { lappend out [lindex $names $i] } }
    return [join $out ,]
}

# Integer -> w-character bit string, LSB first (the order in which jtag sequence shifts bits).
proc fi_bits {v w} {
    set s ""
    for {set i 0} {$i < $w} {incr i} { append s [expr {($v >> $i) & 1}] }
    return $s
}

# Send a command: [31:0] key, [39:32] opcode, [55:40] tag, arguments from bit 56 (see fi_ctrl.v).
# fields: list of {value lo width} placed into the 256-bit command. Returns the tag, which the
# controller echoes in its status when the command has completed.
proc fi_cmd {op fields} {
    set ::FI_TAG [expr {($::FI_TAG + 1) & 0xFFFF}]
    if {$::FI_TAG == 0} { set ::FI_TAG 1 }
    set v [expr {$::FI_KEY | ($op << 32) | ($::FI_TAG << 40)}]
    foreach f $fields {
        lassign $f val lo w
        set v [expr {$v | (($val & ((1 << $w) - 1)) << $lo)}]
    }
    set bits [fi_bits $v 256]
    set seq [jtag sequence]
    $seq irshift -state IDLE -integer 6 3
    $seq drshift -state IDLE -bits 256 $bits
    $seq irshift -state IDLE -integer 6 9
    $seq run
    $seq delete
    return $::FI_TAG
}

# wait until the main FSM echoed tag with done=1 (or the window loader echoed it)
proc fi_wait {tag {timeout_ms 10000} {win 0}} {
    set t0 [clock milliseconds]
    while {1} {
        set s [fi_status]
        if {$win} {
            if {[dict get $s win_tag] == $tag} { return $s }
        } elseif {[dict get $s tag] == $tag && [dict get $s done]} { return $s }
        if {[clock milliseconds] - $t0 > $timeout_ms} { error "timeout waiting for tag $tag: $s" }
        after 2
    }
}

# Send a command and wait for it to complete; returns the status after completion.
proc fi_do {op fields {timeout_ms 10000}} {
    set t [fi_cmd $op $fields]
    return [fi_wait $t $timeout_ms]
}

# Campaign configuration: frame-list range, word range, stride, test and verify lengths and flags
# (b0 log every injection, b1 skip the verify run, b2 stop on a persistent error).
proc fi_cfg {far_first far_last word_lo word_hi stride ntest nverify flags} {
    return [fi_do 1 [list [list $far_first 56 16] [list $far_last 72 16] [list $word_lo 88 7] [list $word_hi 96 7] \
        [list $stride 104 32] [list $ntest 136 14] [list $nverify 152 14] [list $flags 168 8]]]
}

proc fi_raw_inject {addr} { return [fi_do 5 [list [list $addr 56 40]]] } ;# one SEM injection (40-bit address)
proc fi_golden {} { return [fi_do 4 {} 20000] }        ;# record the fault-free outputs of ntest vectors
proc fi_test {} { return [fi_do 6 {} 20000] }          ;# run ntest vectors: last_mism, last_corr
proc fi_sem_idle {} { return [fi_do 7 {} 10000] }      ;# SEM -> IDLE (required for injection)
proc fi_sem_observe {} { return [fi_do 8 {} 10000] }   ;# SEM -> OBSERVATION (scrubbing)
proc fi_clear {} { return [fi_do 11 {}] }              ;# reset counters and the event log

# read WIN_N records starting at rec (also frees everything before rec); returns list of hex strings
proc fi_window {rec} {
    set t [fi_cmd 10 [list [list $rec 56 32]]]
    fi_wait $t 5000 1
    set b [fi_scan 0x22 $::FI_WINW]
    if {[fi_val $b 0 32] != 0x57494E44} { error "bad window header" }
    if {[fi_val $b 32 32] != $rec} { error "window start mismatch" }
    set recs {}
    for {set i 0} {$i < $::FI_WINN} {incr i} {
        lappend recs [format %016llX [fi_val $b [expr {64 + 64 * $i}] 64]]
    }
    return $recs
}

# Text most recently printed by the SEM on its monitor interface (up to 256 characters).
proc fi_monitor {} {
    set b [fi_scan 0x23 $::FI_MONW]
    set wp [fi_val $b 16 16]
    set txt ""
    set n [expr {min($wp, 256)}]
    for {set i [expr {$wp - $n}]} {$i < $wp} {incr i} {
        set c [fi_val $b [expr {32 + 8 * ($i & 255)}] 8]
        if {$c == 13} { continue }
        append txt [format %c $c]
    }
    return $txt
}
