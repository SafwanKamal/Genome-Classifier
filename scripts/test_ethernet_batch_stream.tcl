set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $repo_dir build ethernet_batch_stream_sim]
create_project -force ethernet_batch_stream_sim $build_dir -part xc7a100tcsg324-1
foreach name {variant_triage_top_ethernet_stream batch_model_stream_core ethernet_RX_overlap ethernet_batch_request_overlap ethernet_batch_result_overlap score_clock_domain_crosser ethernet_RX rmii_RX ethernet_CRC32 ethernet_TX ethernet_TX_buffer rmii_TX} {
    add_files [list [file join $repo_dir rtl $name.sv]]
}
add_files [glob [file join $repo_dir genomic-dataset-pipeline artifacts model_v4_batch_seed_7 memory batch_*.mem]]
add_files -fileset sim_1 [list [file join $repo_dir simulation ethernet_batch_stream_tb.sv]]
add_files -fileset sim_1 [list [file join $repo_dir build model_v4_test.mem]]
set_property top ethernet_batch_stream_tb [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set handle [open [file join $build_dir ethernet_batch_stream_sim.sim sim_1 behav xsim simulate.log] r]
set result [read $handle]
close $handle
if {[string first "PASS: Streamed Ethernet batches" $result] < 0} {error "Ethernet batch simulation failed"}
