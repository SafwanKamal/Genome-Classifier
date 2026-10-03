# User-run build; uses V3 memories without replacing the working V2 export.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $repo_dir build ethernet_classifier_v3]
set memory_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v3_h256_seed_7 fpga_export memory]
# Routing threshold frozen from V3 validation (reports/checkpoint_v3/routing_policy.json).
set model_generics {HIDDEN_NUMBER=256 HIDDEN_MAC_LANES=32 REQUANT_SHIFT=4 ROUTING_THRESHOLD=-1914}
source [file join $repo_dir scripts build_variant_triage_ethernet_only.tcl]
