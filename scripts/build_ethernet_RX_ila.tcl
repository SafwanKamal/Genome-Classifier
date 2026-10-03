# Build or rebuild the dedicated RX ILA project in this workspace.
# Run: vivado -mode batch -source scripts/build_ethernet_RX_ila.tcl
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $repo_dir build ethernet_RX_ila]
set project_file [file join $build_dir ethernet_RX_ila.xpr]
if {[file exists $project_file]} {
    open_project $project_file
} else {
    create_project ethernet_RX_ila $build_dir -part xc7a100tcsg324-1
    foreach source {rmii_RX.sv ethernet_CRC32.sv ethernet_RX.sv ethernet_RX_test_top.sv} {
        add_files -norecurse [list [file join $repo_dir rtl $source]]
    }
    add_files -fileset constrs_1 -norecurse [list [file join $repo_dir constraints ethernet_RX_test_top.xdc]]
    set_property top ethernet_RX_test_top [get_filesets sources_1]
    create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_ethernet
}
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.CLKOUT2_REQUESTED_PHASE {36.000} CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_HIGH} CONFIG.USE_LOCKED {true}] [get_ips clk_wiz_ethernet]
generate_target all [get_ips clk_wiz_ethernet]
# Rebuild the IP checkpoint too; regenerating HDL alone can leave stale clocks.
create_ip_run [get_ips clk_wiz_ethernet]
reset_run clk_wiz_ethernet_synth_1
launch_runs clk_wiz_ethernet_synth_1 -jobs 4
wait_on_run clk_wiz_ethernet_synth_1
reset_run synth_1
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "RX synthesis failed."}
cd $build_dir
open_run synth_1
source [file join $repo_dir scripts insert_ethernet_RX_ila.tcl]
opt_design
place_design
route_design
report_timing_summary -file [file join $build_dir timing_summary.rpt]
report_drc -file [file join $build_dir drc.rpt]
write_checkpoint -force [file join $build_dir ethernet_RX_ila_routed.dcp]
foreach delay {max min} {
    if {[get_property SLACK [get_timing_paths -delay_type $delay -max_paths 1]] < 0} {
        error "RX timing failed; inspect timing_summary.rpt before programming."
    }
}
write_debug_probes -force [file join $build_dir ethernet_RX_ila.ltx]
write_bitstream -force [file join $build_dir ethernet_RX_ila.bit]
puts "ILA_BUILD_COMPLETE: $build_dir"
