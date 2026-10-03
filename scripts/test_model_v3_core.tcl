# Simulation only: requires exported V3 memories and generated validation vectors.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set export_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v3_h256_seed_7 fpga_export]
set build_dir [file join $repo_dir build model_v3_core_sim]
create_project -force model_v3_core_sim $build_dir -part xc7a100tcsg324-1
foreach name {variant_triage_core dense_engine ReLU_quantizer} {
    add_files [list [file join $repo_dir rtl $name.sv]]
}
add_files [glob [file join $export_dir memory dense_*.mem]]
set_property include_dirs [list $export_dir] [get_filesets sources_1]
add_files -fileset sim_1 [list [file join $repo_dir simulation variant_triage_core_tb.sv]]
add_files -fileset sim_1 [list [file join $repo_dir build model_v3_validation.mem]]
set_property top variant_triage_core_tb [get_filesets sim_1]
set_property generic {HIDDEN_NUMBER=256 HIDDEN_MAC_LANES=32 TEST_NUMBER=1000 MAXIMUM_CYCLES=1500 VECTOR_FILE="model_v3_validation.mem"} [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set handle [open [file join $build_dir model_v3_core_sim.sim sim_1 behav xsim simulate.log] r]
set result [read $handle]
close $handle
if {[string first "PASS: all 1000 core tests matched" $result] < 0} {error "V3 core simulation failed"}
