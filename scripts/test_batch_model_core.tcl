set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set model_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v4_batch_seed_7]
set build_dir [file join $repo_dir build batch_model_core_sim]
create_project -force batch_model_core_sim $build_dir -part xc7a100tcsg324-1
add_files [list [file join $repo_dir rtl batch_model_core.sv]]
add_files [glob [file join $model_dir memory batch_*.mem]]
add_files -fileset sim_1 [list [file join $repo_dir simulation batch_model_core_tb.sv]]
add_files -fileset sim_1 [list [file join $repo_dir build model_v4_test.mem]]
set_property top batch_model_core_tb [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
launch_simulation
close_sim
set handle [open [file join $build_dir batch_model_core_sim.sim sim_1 behav xsim simulate.log] r]
set result [read $handle]
close $handle
if {[string first "PASS: 1000 batch core vectors" $result] < 0} {error "Batch model core simulation failed"}
