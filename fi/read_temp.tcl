# Die temperature of the XC7K325T from its XADC through the JTAG DRP interface (XADC_DRP instruction,
# UG480), without any design support: a read-only access to status register 0x00.
# Usage: xsdb fi/read_temp.tcl
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
fi_connect
# XADC_DRP data register: [15:0] data, [25:16] DRP address, [29:26] command (1 = read). The read
# command is shifted in first; the addressed register is captured by the next DR shift.
proc xadc_read {addr} {
    set seq [jtag sequence]
    $seq irshift -state IDLE -integer 6 0x37
    $seq drshift -state IDLE -integer 32 [expr {(1 << 26) | ($addr << 16)}]
    $seq state IDLE 32
    $seq drshift -capture -state IDLE -tdi 0 32
    $seq irshift -state IDLE -integer 6 9
    set b [lindex [$seq run -bits] 0]
    $seq delete
    return [fi_val $b 0 16]
}
set t {}
for {set i 0} {$i < 4} {incr i} {
    lappend t [format %.1f [expr {([xadc_read 0] >> 4) * 503.975 / 4096 - 273.15}]]
    after 250
}
puts "DIE_TEMP_C $t"
disconnect
exit 0
