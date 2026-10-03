# Run with Vivado batch. Uses the real classifier and behavioral clock models.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
if {![info exists build_dir]} {set build_dir [file join $repo_dir build ethernet_classifier_only_sim]}
if {![info exists memory_dir]} {set memory_dir [file join $repo_dir memory]}
if {![info exists vector_file]} {set vector_file [file join $repo_dir simulation model_v2_h8_core_vectors.mem]}
create_project -force ethernet_classifier_only_sim $build_dir -part xc7a100tcsg324-1
foreach name {variant_triage_top_ethernet variant_triage_core dense_engine ReLU_quantizer score_clock_domain_crosser ethernet_RX rmii_RX ethernet_request_RX ethernet_CRC32 ethernet_TX ethernet_TX_buffer rmii_TX} {
    add_files [list [file join $repo_dir rtl $name.sv]]
}
set_property include_dirs [list [file join $repo_dir rtl]] [get_filesets sources_1]
add_files [glob [file join $memory_dir dense_*.mem]]
add_files -fileset sim_1 [list [file join $repo_dir simulation variant_triage_ethernet_only_tb.sv]]
add_files -fileset sim_1 [list $vector_file]
set_property top variant_triage_ethernet_only_tb [get_filesets sim_1]
if {[info exists model_generics]} {set_property generic $model_generics [get_filesets sim_1]}
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set result_file [file join $build_dir ethernet_classifier_only_sim.sim sim_1 behav xsim simulate.log]
set result_handle [open $result_file r]
set result_text [read $result_handle]
close $result_handle
if {[string first "PASS: Ethernet RX -> parser" $result_text] < 0} {
    error "End-to-end simulation did not pass; inspect $result_file"
}
