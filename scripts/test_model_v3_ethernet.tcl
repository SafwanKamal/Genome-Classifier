# Simulation only: real Ethernet RX/TX, parser, clock mailboxes, and V3 core.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $repo_dir build ethernet_classifier_v3_sim]
set memory_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v3_h256_seed_7 fpga_export memory]
set vector_file [file join $repo_dir build model_v3_validation.mem]
set model_generics {HIDDEN_NUMBER=256 HIDDEN_MAC_LANES=32 ROUTING_THRESHOLD=-1914 BRINGUP_SCORE=-775 VECTOR_FILE="model_v3_validation.mem"}
source [file join $repo_dir scripts test_variant_triage_ethernet_only.tcl]
