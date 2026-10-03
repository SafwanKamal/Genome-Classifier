# User-run build. The working single-variant V2/V3 projects stay separate.
set repo_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $repo_dir build ethernet_classifier_v4_batch]
set batch_memory_dir [file join $repo_dir genomic-dataset-pipeline artifacts model_v4_batch_seed_7 memory]
set model_generics {BATCH_MODEL=1 ROUTING_THRESHOLD=-1833}
source [file join $repo_dir scripts build_variant_triage_ethernet_only.tcl]
