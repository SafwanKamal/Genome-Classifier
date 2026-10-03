# Nexys 4 DDR / Nexys A7-100T combined UART / raw Ethernet classifier.
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports clk]
# Unlike the RX-only top, the classifier also uses the board clock directly.
create_clock -name clk -period 10.000 [get_ports clk]
# The OOC clock-IP checkpoint adds a primary clock at its internal input.
# Relate that input back to the board clock rather than timing two roots.
# Its module input exists in both synthesis and the linked implementation.
create_generated_clock -name clock_generator/inst/clk_in1 \
    -source [get_ports clk] -divide_by 1 [get_pins clock_generator/clk_in1]
set_property -dict {PACKAGE_PIN N17 IOSTANDARD LVCMOS33} [get_ports reset]
set_property -dict {PACKAGE_PIN D5 IOSTANDARD LVCMOS33} [get_ports PHY_ref_clk]
set_property -dict {PACKAGE_PIN B3 IOSTANDARD LVCMOS33} [get_ports PHY_reset_n]
set_property -dict {PACKAGE_PIN C9 IOSTANDARD LVCMOS33} [get_ports PHY_MDC]
set_property -dict {PACKAGE_PIN A9 IOSTANDARD LVCMOS33} [get_ports PHY_MDIO]
set_property -dict {PACKAGE_PIN D9 IOSTANDARD LVCMOS33} [get_ports rmii_crs_dv]
set_property -dict {PACKAGE_PIN C10 IOSTANDARD LVCMOS33} [get_ports rmii_rx_er]
set_property -dict {PACKAGE_PIN C11 IOSTANDARD LVCMOS33} [get_ports {rmii_rxd[0]}]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports {rmii_rxd[1]}]
set_property -dict {PACKAGE_PIN A10 IOSTANDARD LVCMOS33} [get_ports {rmii_txd[0]}]
set_property -dict {PACKAGE_PIN A8 IOSTANDARD LVCMOS33} [get_ports {rmii_txd[1]}]
set_property -dict {PACKAGE_PIN B9 IOSTANDARD LVCMOS33} [get_ports rmii_tx_en]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {LED[0]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVCMOS33} [get_ports {LED[1]}]
set_property -dict {PACKAGE_PIN J13 IOSTANDARD LVCMOS33} [get_ports {LED[2]}]
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33} [get_ports {LED[3]}]

set_property BITSTREAM.CONFIG.UNUSEDPIN Pullnone [current_design]
create_generated_clock -name PHY_clk -source [get_pins PHY_clock_forward/C] \
    -divide_by 1 [get_ports PHY_ref_clk]
# PHY output is clocked by REF_CLK; allow up to 14 ns plus 1 ns board skew.
set_input_delay -clock PHY_clk -max 15.000 \
    [get_ports {rmii_crs_dv rmii_rx_er rmii_rxd[*]}]
set_input_delay -clock PHY_clk -min 2.000 \
    [get_ports {rmii_crs_dv rmii_rx_er rmii_rxd[*]}]
# REF_CLK launches symbol n at 0 ns; RX captures it at 22 ns, not at 2 ns
# before the PHY's 15 ns maximum output delay. Both clocks have a 20 ns period.
# Select that capture edge for setup. For this positively shifted clock, leave
# hold at capture 22 ns against the NEXT symbol launched at 20 ns. Applying the
# usual same-phase N-1 hold adjustment would incorrectly move capture to 2 ns.
# No internal paths are relaxed.
set_multicycle_path 2 -setup -end -from [get_ports {rmii_crs_dv rmii_rx_er rmii_rxd[*]}]
set_multicycle_path 0 -hold -end -from [get_ports {rmii_crs_dv rmii_rx_er rmii_rxd[*]}]
set_false_path -from [get_ports reset]
set_false_path -to [get_ports {LED[*] PHY_reset_n PHY_MDC PHY_MDIO tx}]
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# UART uses the board's existing USB-UART pin assignment.
set_property -dict {PACKAGE_PIN C4 IOSTANDARD LVCMOS33} [get_ports rx]
set_property -dict {PACKAGE_PIN D4 IOSTANDARD LVCMOS33} [get_ports tx]
set_false_path -from [get_ports rx]
# TX retains the related 180-degree clock and PHY setup/hold requirements.
set_output_delay -clock PHY_clk -max 5.000 [get_ports {rmii_txd[*] rmii_tx_en}]
set_output_delay -clock PHY_clk -min -2.500 [get_ports {rmii_txd[*] rmii_tx_en}]
