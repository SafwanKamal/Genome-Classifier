# Create an isolated 200 MHz target project; do not run synthesis or generate a bitstream.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set thread_hook [file join $repo_dir scripts vivado_build_threads.tcl]
source $thread_hook
if {![info exists build_dir]} {set build_dir [file join $repo_dir build ethernet_classifier_v4_200]}
if {![info exists memory_dir]} {set memory_dir [file join $repo_dir memory]}
create_project -force ethernet_classifier_v4_200 $build_dir -part xc7a100tcsg324-1
add_files -fileset utils_1 -norecurse [list $thread_hook]
foreach name {variant_triage_top_ethernet_200 variant_triage_core dense_engine ReLU_quantizer score_clock_domain_crosser ethernet_RX rmii_RX ethernet_request_RX ethernet_CRC32 ethernet_TX ethernet_TX_buffer rmii_TX} {
    set rtl_dir [file join $repo_dir rtl]
    if {$name in {variant_triage_top_ethernet_200 batch_model_200_core}} {set rtl_dir [file join $rtl_dir mhz200]}
    add_files -norecurse [list [file join $rtl_dir $name.sv]]
}
set batch_memory_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v4_batch_seed_7 memory]
if {[info exists batch_memory_dir]} {
    foreach name {batch_model_200_core ethernet_RX_overlap ethernet_batch_request_overlap ethernet_batch_result_overlap} {
        set rtl_dir [file join $repo_dir rtl]
        if {$name in {variant_triage_top_ethernet_200 batch_model_200_core}} {set rtl_dir [file join $rtl_dir mhz200]}
        add_files -norecurse [list [file join $rtl_dir $name.sv]]
    }
    add_files -norecurse [glob [file join $batch_memory_dir batch_*.mem]]
}
add_files -norecurse [glob [file join $memory_dir dense_*.mem]]
set_property include_dirs [list [file join $repo_dir rtl]] [get_filesets sources_1]
add_files -fileset constrs_1 [list [file join $repo_dir constraints variant_triage_ethernet_only.xdc]]
set_property PROCESSING_ORDER LATE [get_files -of_objects [get_filesets constrs_1]]
set_property top variant_triage_top_ethernet_200 [get_filesets sources_1]
set_property generic {BATCH_MODEL=1 ROUTING_THRESHOLD=-1833} [get_filesets sources_1]
create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_ethernet_200
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {50.000} CONFIG.CLKOUT2_REQUESTED_PHASE {180.000} \
    CONFIG.CLKOUT3_USED {true} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {50.000} CONFIG.CLKOUT3_REQUESTED_PHASE {36.000} \
    CONFIG.CLKOUT4_USED {true} CONFIG.CLKOUT4_REQUESTED_OUT_FREQ {200.000} \
    CONFIG.USE_RESET {true} CONFIG.RESET_TYPE {ACTIVE_HIGH} CONFIG.USE_LOCKED {true}] [get_ips clk_wiz_ethernet_200]
generate_target all [get_ips clk_wiz_ethernet_200]
create_ip_run [get_ips clk_wiz_ethernet_200]
set_property STEPS.SYNTH_DESIGN.TCL.PRE $thread_hook [get_runs -filter {IS_SYNTHESIS == 1}]
set_property STEPS.OPT_DESIGN.TCL.PRE $thread_hook [get_runs impl_1]
update_compile_order -fileset sources_1
puts "200 MHz project ready. Run synthesis and implementation in Vivado; check routed timing before Generate Bitstream."
