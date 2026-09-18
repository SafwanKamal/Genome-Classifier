# nexys 4 ddr rev c; use only this xdc for the standalone ethernet test
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports clk_100MHz]
create_clock -name board_clk -period 10.000 [get_ports clk_100MHz]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports reset]
set_property -dict {PACKAGE_PIN D5 IOSTANDARD LVCMOS33} [get_ports PHY_ref_clk]
set_property -dict {PACKAGE_PIN B3 IOSTANDARD LVCMOS33} [get_ports PHY_reset_n]
set_property -dict {PACKAGE_PIN C9 IOSTANDARD LVCMOS33} [get_ports PHY_MDC]
set_property -dict {PACKAGE_PIN A9 IOSTANDARD LVCMOS33} [get_ports PHY_MDIO]
set_property -dict {PACKAGE_PIN A10 IOSTANDARD LVCMOS33} [get_ports {rmii_txd[0]}]
set_property -dict {PACKAGE_PIN A8 IOSTANDARD LVCMOS33} [get_ports {rmii_txd[1]}]
set_property -dict {PACKAGE_PIN B9 IOSTANDARD LVCMOS33} [get_ports rmii_tx_en]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {LED[0]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVCMOS33} [get_ports {LED[1]}]
set_property -dict {PACKAGE_PIN J13 IOSTANDARD LVCMOS33} [get_ports {LED[2]}]
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33} [get_ports {LED[3]}]

# do not let unused fpga inputs pull down the phy's mode straps
set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]

# d1=0, d2=1 forwards an inverted 50 mhz clock
create_generated_clock -name PHY_clk -source [get_pins PHY_clock_forward/C] \
    -divide_by 1 -invert [get_ports PHY_ref_clk]

# lan8720a ref_clk IN: setup 4 ns, hold 1.5 ns
# add a provisional 1 ns allowance for pcb clock/data skew
set_output_delay -clock PHY_clk -max 5.000 [get_ports {rmii_txd[*] rmii_tx_en}]
set_output_delay -clock PHY_clk -min -2.500 [get_ports {rmii_txd[*] rmii_tx_en}]

# asynchronous button reset; indicators and static management outputs
set_false_path -from [get_ports reset]
set_false_path -to [get_ports {LED[*] PHY_reset_n PHY_MDC PHY_MDIO}]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]