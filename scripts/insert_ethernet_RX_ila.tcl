# Source after opening a freshly synthesized ethernet_RX_test_top design.
# AMD UG908 netlist insertion flow: create_debug_core / connect_debug_port.
if {![llength [get_ports -quiet rmii_crs_dv]] || ![llength [get_nets -quiet receiver/rx_start]]} {
    error "Open the synthesized ethernet_RX_test_top design first."
}
if {[llength [get_debug_cores -quiet u_ila_rx]]} {
    error "u_ila_rx already exists; reopen the original synthesized design."
}
proc rx_ila_nets {names} {
    set result {}
    foreach name $names {
        set match [get_nets -quiet $name]
        if {[llength $match] != 1} {
            error "Expected one synthesized net for $name; found [llength $match]"
        }
        lappend result [lindex $match 0]
    }
    return $result
}
# Resolve every signal before creating the core, preserving low-to-high bus order.
# Observe the registered input bundle; pre-IOB-register nets are not routable
# to an ILA. These three probes share the same one-cycle sampling latency.
set rx_probe_names [list {receiver/decoder/dv_sample} \
    {{receiver/decoder/rxd_sample[0]} {receiver/decoder/rxd_sample[1]}} \
    {receiver/decoder/er_sample} {rx_reset} {receiver/rx_start} {receiver/rx_end} \
    {receiver/rx_error} {receiver/rx_valid} \
    {{receiver/rx_data[0]} {receiver/rx_data[1]} {receiver/rx_data[2]} {receiver/rx_data[3]} {receiver/rx_data[4]} {receiver/rx_data[5]} {receiver/rx_data[6]} {receiver/rx_data[7]}} \
    {good_frame} {bad_frame} {overflow} {valid_out} {last_out} \
    {{data_out[0]} {data_out[1]} {data_out[2]} {data_out[3]} {data_out[4]} {data_out[5]} {data_out[6]} {data_out[7]}}]
set rx_probe_nets {}
foreach names $rx_probe_names {lappend rx_probe_nets [rx_ila_nets $names]}
set rx_clock_net [rx_ila_nets {clk_phy_50MHz}]
create_debug_core u_ila_rx ila
set_property -dict [list C_DATA_DEPTH 1024 C_TRIGIN_EN false C_TRIGOUT_EN false \
    C_ADV_TRIGGER false C_INPUT_PIPE_STAGES 0 C_EN_STRG_QUAL false \
    ALL_PROBE_SAME_MU true ALL_PROBE_SAME_MU_CNT 1] [get_debug_cores u_ila_rx]
set_property port_width 1 [get_debug_ports u_ila_rx/clk]
connect_debug_port u_ila_rx/clk $rx_clock_net
set rx_probe_index 0
foreach nets $rx_probe_nets {
    if {$rx_probe_index > 0} {create_debug_port u_ila_rx probe}
    set port [get_debug_ports u_ila_rx/probe$rx_probe_index]
    set_property port_width [llength $nets] $port
    connect_debug_port $port $nets
    incr rx_probe_index
}
puts "Inserted u_ila_rx: 50 MHz, 1024 samples, $rx_probe_index probes."
