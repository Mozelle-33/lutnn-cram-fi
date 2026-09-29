# Generate the SEM controller IP (7 series, v4.1) and its example design for XC7K325T-FFG900-2.
# Usage: vivado -mode batch -source hw/tcl/gen_sem_ip.tcl   (run from the repository root)
# The generated IP (hw/ip/sem_0) is AMD IP and is not distributed with this repository; run this
# script once before hw/tcl/build.tcl.
set root [file normalize [file join [file dirname [info script]] .. ..]]
set ipdir [file join $root hw ip]
file mkdir $ipdir
create_project -in_memory -part xc7k325tffg900-2
set_property target_language Verilog [current_project]
create_ip -name sem -vendor xilinx.com -library ip -version 4.1 -module_name sem_0 -dir $ipdir
set ip [get_ips sem_0]
puts "=== SEM CONFIG (defaults) ==="
foreach p [lsort [list_property $ip CONFIG.*]] { puts "$p = [get_property $p $ip]" }
# error injection (needed by the platform), correction by repair (used only when the host puts the
# SEM into observation, e.g. in fi/scrub_test.tcl), no classification, 100 MHz clock
foreach {k v} {CONFIG.ENABLE_INJECTION true CONFIG.ENABLE_CORRECTION true CONFIG.ENABLE_CLASSIFICATION false CONFIG.CLOCK_FREQ 100} {
    if {[catch {set_property $k $v $ip} err]} { puts "WARN: cannot set $k=$v: $err" }
}
puts "=== SEM CONFIG (final) ==="
foreach p [lsort [list_property $ip CONFIG.*]] { puts "$p = [get_property $p $ip]" }
generate_target all $ip
puts "=== IP files ==="
foreach f [get_files -of_objects $ip] { puts $f }
open_example_project -force -dir [file join $ipdir sem_0_ex] $ip
puts "=== example files ==="
foreach f [get_files -all] { puts $f }
