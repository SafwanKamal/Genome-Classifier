# Resource check only; no implementation, programming, or bitstream generation.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set model_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v4_batch_seed_7 memory]
create_project -in_memory -part xc7a100tcsg324-1
read_verilog -sv [list [file join $repo_dir rtl mhz200 batch_model_200_core.sv]]
set clock_xdc [file join $repo_dir build batch_model_200_clock.xdc]
set handle [open $clock_xdc w]
puts $handle {create_clock -period 5 [get_ports clk]}
close $handle
read_xdc [list $clock_xdc]
cd $model_dir
synth_design -top batch_model_200_core -part xc7a100tcsg324-1
report_utilization -file [file join $repo_dir reports model_v4_200_core_utilization.rpt]
report_timing_summary -file [file join $repo_dir reports model_v4_200_core_synthesis_timing.rpt]
