# MedGemma 1.5 local pilot — October 3, 2026

Setup completed: local Hugging Face browser authentication succeeded, access to
Google's gated original configuration was verified, and Unsloth's pinned
MedGemma 1.5 4B Q4_K_M text weights were downloaded and hash-verified. The model
is installed as `variantgate-medgemma:1.5-4b-q4km` in Ollama 0.6.2.
The 2.49 GB weight file is a third-party quantization, not Google's original
BF16 distribution. Source revisions, hash, model digest and explicit chat
template are in `runs/variantgate_llm/medgemma_setup/model_receipt.json`.

The installed Ollama imported the GGUF with a generic `{{ .Prompt }}` template.
Before any inference, we added Gemma 3 turn markers and prepended the system
instruction to the first user turn, matching Google's text chat format.
The profile uses 4,096 context tokens, temperature zero, seed 42, and up to
1,024 generated tokens. Inference used the CPU; no paid requests were made.

## Results

| Test | Observation | Mechanical outcome |
| --- | --- | --- |
| Three synthetic complete-pipeline cases | ClinVar outputs used paraphrased sentences as quotations and misclassified database evidence | All three failed verbatim-quote validation; zero accepted briefs |
| One isolated synthetic PubMed extraction | Copied the supplied assay passage verbatim | Extraction validation passed |
| Organization of that extraction | Returned sentences in several sections instead of existing claim IDs | Failed claim-retention validation; no accepted brief |

The complete-pipeline smoke took 75.6 seconds total. Its three generation calls
took 18.8, 19.6 and 36.9 seconds, including initial model loading where applicable.
The isolated literature extraction took 26.4 seconds; organization took 17.6
seconds. These small CPU timings are not a throughput benchmark. BioMistral used
a different output limit and earlier adapter settings, so the timings are not a
controlled model-to-model comparison.

## Artifacts and interpretation

- `runs/variantgate_llm/medgemma_smoke_v1/`: full smoke manifests, summaries,
  failure records, frozen evidence packages and model-response receipts.
- `runs/variantgate_llm/medgemma_literature_probe_v1.json`: source package hash,
  exact source passage, raw responses and mechanically validated extraction.
- `scripts/setup_variantgate_medgemma.ps1`: repeatable authenticated setup.
- `configs/variantgate/medgemma.Modelfile`: explicit runtime profile/template.

The isolated probe intentionally skipped ClinVar extraction to diagnose where
the complete pipeline was stopping. It is not a complete brief or an additional
independent benchmark case. It used the first smoke case's invented passage.
Mechanical quote validation does not establish semantic-support accuracy,
coverage, usefulness, real-variant performance or clinical validity.

The model is installed and usable, but this pipeline configuration has not passed
the smoke test. A full 24-case model-quality sweep was not started. The model was
unloaded after testing to release RAM; the weights remain available.

## Recommended next implementation

Generate database statements directly from verified ClinVar fields. Group
accepted claims by their already-validated finding types and scopes using code.
Both are deterministic tasks, so routing them through a model adds failure
opportunities without adding information. Preserve the separate database and
literature sections, citations, identity checks, and review boundary.

Reserve model calls for literature extraction, then run all 24 frozen cases and
review quote support, type/scope accuracy, omissions and contradictions. This
architecture change has now been implemented as
`deterministic-database-and-grouping-v2`; see the pipeline guide for its
evaluation results. The observations above describe the original pilot.
Real-cohort evaluation still requires identifier repair against
the original GRCh38 ClinVar VCF and domain review.
