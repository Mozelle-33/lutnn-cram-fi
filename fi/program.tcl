# Configure the FPGA with an FI platform bitstream over JTAG and check that the platform answers
# (status signature 'NFI', build and DUT ids).
# Usage: xsdb fi/program.tcl <bitfile>
# Every programming is appended to results/program_log.txt.
set bit [file normalize [lindex $argv 0]]
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
file mkdir [file join $root results]
set logf [open [file join $root results program_log.txt] a]
proc record {s} { puts $::logf $s; flush $::logf; puts $s }
# IR capture value (bit 5 = DONE: the device is configured) and IDCODE, read without side effects.
proc ir_status {} {
    set seq [jtag sequence]
    $seq irshift -state IDLE -capture -integer 6 9
    $seq drshift -state IDLE -capture -integer 32 0
    set r [$seq run -integer]
    $seq delete
    return $r
}
if {![file exists $bit]} { error "missing $bit" }
fi_connect
lassign [ir_status] ir id
# the bitstreams are built for the XC7K325T (IDCODE without the revision nibble)
if {($id & 0x0fffffff) != 0x03651093} { error [format "IDCODE mismatch 0x%08x" $id] }
set done [expr {($ir >> 5) & 1}]
record "----"
record "TIME_UTC=[clock format [clock seconds] -gmt 1 -format {%Y-%m-%dT%H:%M:%SZ}]"
record "BITSTREAM=$bit"
record [format "IR_CAPTURE_BEFORE=0x%02x DONE_BEFORE=%d" $ir $done]
targets -set -filter {name =~ "xc7k325t*"}
fpga -file $bit
jtag targets -set -filter {name == "xc7k325t"}
after 500
lassign [ir_status] ir id
set s [fi_status]
record [format "IR_CAPTURE_AFTER=0x%02x DONE_AFTER=%d SIG=0x%06x BUILD=%d DUT=%d LATENCY=%d SEM=%s" $ir [expr {($ir >> 5) & 1}] \
    [dict get $s sig] [dict get $s build_id] [dict get $s dut_id] [dict get $s latency] [fi_sem_str [dict get $s sem]]]
close $logf
disconnect
if {[dict get $s sig] != 0x4E4649} { puts "WARNING: FI signature not found"; exit 4 }
exit 0
