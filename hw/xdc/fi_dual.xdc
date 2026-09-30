# Additional constraints of fi_top_dual.v (separate clock for the FI controller and the DUT).
# The MMCM outputs are derived from sysclk; the JTAG clocks are asynchronous to both.
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks sysclk] \
    -group tck1 -group tck2 -group tck3 -group tck4
# SEM clock <-> controller clock: every crossing goes through a synchroniser (status flags, injection
# toggle, monitor freeze) or is a bus held stable around its use (injection address), so only the
# data-path delay is bounded
set clk_sem  [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]
set clk_fast [get_clocks -of_objects [get_pins u_mmcm/CLKOUT1]]
set_max_delay 5.0 -datapath_only -from $clk_sem -to $clk_fast
set_max_delay 5.0 -datapath_only -from $clk_fast -to $clk_sem
