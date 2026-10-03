# Build the Ethernet-only classifier without altering the RX ILA project.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
if {![info exists build_dir]} {set build_dir [file join $repo_dir build ethernet_classifier_only]}
if {![info exists memory_dir]} {set memory_dir [file join $repo_dir memory]}
create_project -force ethernet_classifier_only $build_dir -part xc7a100tcsg324-1
foreach name {variant_triage_top_ethernet variant_triage_core dense_engine ReLU_quantizer score_clock_domain_crosser ethernet_RX rmii_RX ethernet_request_RX ethernet_CRC32 ethernet_TX ethernet_TX_buffer rmii_TX} {
    add_files -norecurse [list [file join $repo_dir rtl $name.sv]]
}
if {[info exists batch_memory_dir]} {
    foreach name {batch_model_core ethernet_batch_request_RX ethernet_batch_result} {
        add_files -norecurse [list [file join $repo_dir rtl $name.sv]]
    }
    add_files -norecurse [glob [file join $batch_memory_dir batch_*.mem]]
}
add_files -norecurse [glob [file join $memory_dir dense_*.mem]]
set_property include_dirs [list [file join $repo_dir rtl]] [get_filesets sources_1]
add_files -fileset constrs_1 [list [file join $repo_dir constraints variant_triage_ethernet_only.xdc]]
set_property PROCESSING_ORDER LATE [get_files -of_objects [get_filesets constrs_1]]
set_property top variant_triage_top_ethernet [get_filesets sources_1]
if {[info exists model_generics]} {set_property generic $model_generics [get_filesets sources_1]}
create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_ethernet
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {50.000} CONFIG.CLKOUT2_REQUESTED_PHASE {180.000} \
    CONFIG.CLKOUT3_USED {true} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {50.000} CONFIG.CLKOUT3_REQUESTED_PHASE {36.000} \
    CONFIG.USE_RESET {true} CONFIG.RESET_TYPE {ACTIVE_HIGH} CONFIG.USE_LOCKED {true}] [get_ips clk_wiz_ethernet]
generate_target all [get_ips clk_wiz_ethernet]
create_ip_run [get_ips clk_wiz_ethernet]
launch_runs clk_wiz_ethernet_synth_1 -jobs 4
wait_on_run clk_wiz_ethernet_synth_1
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Classifier synthesis failed"}
open_run synth_1
report_utilization -file [file join $build_dir synthesis_utilization.rpt]
launch_runs impl_1 -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Classifier implementation failed"}
open_run impl_1
report_timing_summary -file [file join $build_dir timing_summary.rpt]
foreach pin [all_registers -clock_pins] {
    if {[llength [get_clocks -quiet -of_objects $pin]] == 0} {
        error "Register clock pin $pin lacks a clock constraint"
    }
}
report_cdc -file [file join $build_dir cdc.rpt]
report_drc -file [file join $build_dir drc.rpt]
foreach delay {max min} {
    if {[get_property SLACK [get_timing_paths -delay_type $delay -max_paths 1]] < 0} {
        error "Timing failed; inspect timing_summary.rpt before programming"
    }
}
write_bitstream -force [file join $build_dir ethernet_classifier_only.bit]
puts "ETHERNET_CLASSIFIER_BUILD_COMPLETE: $build_dir"
