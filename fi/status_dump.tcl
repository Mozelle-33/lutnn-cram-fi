# Read-only: print the FI controller status (USER1) without sending any command.
# Usage: xsdb fi/status_dump.tcl
set root [file normalize [file join [file dirname [info script]] ..]]
source [file join $root fi fi_lib.tcl]
fi_connect
set s [fi_status]
foreach k [dict keys $s] {
    set v [dict get $s $k]
    if {$k eq "sig"} { set v [format 0x%06X $v] }
    puts [format "%-12s %s" $k $v]
}
puts [format "%-12s %s" sem_str [fi_sem_str [dict get $s sem]]]
disconnect
exit 0
