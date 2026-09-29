# NetFirm-4E40-C (XC7K325T-FFG900-2) pins used by the fault-injection platform.

# 100 MHz LVDS system clock, bank 13
set_property -dict {PACKAGE_PIN AB27 IOSTANDARD LVDS_25 DIFF_TERM FALSE} [get_ports sysclk_p]
set_property -dict {PACKAGE_PIN AC27 IOSTANDARD LVDS_25 DIFF_TERM FALSE} [get_ports sysclk_n]
create_clock -name sysclk -period 10.000 [get_ports sysclk_p]

# LEDs, active high
set_property -dict {PACKAGE_PIN N27 IOSTANDARD LVCMOS25} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN M27 IOSTANDARD LVCMOS25} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN N29 IOSTANDARD LVCMOS25} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN N30 IOSTANDARD LVCMOS25} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN J28 IOSTANDARD LVCMOS25} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN K30 IOSTANDARD LVCMOS25} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN L30 IOSTANDARD LVCMOS25} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN K26 IOSTANDARD LVCMOS25} [get_ports {led[7]}]
set_false_path -to [get_ports {led[*]}]

# Board control pins (U27, BPI flash controls): inputs held high by weak pull-ups
set_property -dict {PACKAGE_PIN U27 IOSTANDARD LVCMOS25 PULLUP true} [get_ports keep_u27]
set_property -dict {PACKAGE_PIN U19 IOSTANDARD LVCMOS25 PULLUP true} [get_ports {keep_flash[0]}]
set_property -dict {PACKAGE_PIN M24 IOSTANDARD LVCMOS25 PULLUP true} [get_ports {keep_flash[1]}]
set_property -dict {PACKAGE_PIN M25 IOSTANDARD LVCMOS25 PULLUP true} [get_ports {keep_flash[2]}]
set_property -dict {PACKAGE_PIN M30 IOSTANDARD LVCMOS25 PULLUP true} [get_ports {keep_flash[3]}]
set_false_path -from [get_ports {keep_u27 keep_flash[*]}]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 2.5 [current_design]
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLNONE [current_design]

# JTAG user registers: TCK from the BSCANE2 primitives, asynchronous to sysclk
create_clock -name tck1 -period 30.000 [get_pins u_jtag/u_bscan1/TCK]
create_clock -name tck2 -period 30.000 [get_pins u_jtag/u_bscan2/TCK]
create_clock -name tck3 -period 30.000 [get_pins u_jtag/u_bscan3/TCK]
create_clock -name tck4 -period 30.000 [get_pins u_jtag/u_bscan4/TCK]
set_clock_groups -asynchronous -group sysclk -group tck1 -group tck2 -group tck3 -group tck4

# SEM controller: FRAME_ECC outputs (from the SEM v4.1 example design constraints)
set_max_delay 9.0 -from [get_pins u_cfg/example_frame_ecc/*] -datapath_only -quiet
