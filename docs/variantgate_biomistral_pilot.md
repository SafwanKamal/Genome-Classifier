# BioMistral local pilot — October 3, 2026

The official BioMistral-7B Q4_K_M weights were acquired, hash-verified and imported
into the installed Ollama 0.6.2 runtime as `variantgate-biomistral:7b-q4km`.
The receipt is at `runs/variantgate_llm/local_setup/model_receipt.json`.
The profile uses 2,048 context tokens, temperature zero, seed 42 and at most 768
generated tokens. Inference used the Ryzen 7 CPU; this runtime did not support
the integrated Radeon 780M. The loaded model occupied about 6.3 GB according to
Ollama. It was unloaded after testing; the downloaded model remains installed.

## Observed results

| Experiment | Cases | Accepted briefs | Failure | Request latency |
| --- | ---: | ---: | --- | --- |
| Initial compatible-API adapter | 3 | 0 | All three invented or altered supporting quotes | 47.3–84.9 seconds |
| Schema explicitly included in prompt | 1 repeated case | 0 | Genuine quote, but ClinVar statement incorrectly classified as literature association/gene context | 67.8 seconds |

The initial test also generated unsupported frequency/patient/pathogenicity
claims. The grounding checks rejected them before publication. The follow-up
used 1,095 prompt and 155 completion tokens, inside the configured context size.
No truncation warning appeared in the server log for these requests.

The local adapter now includes the response schema in the prompt and sets
temperature zero, following
[Ollama's documentation](https://docs.ollama.com/capabilities/structured-outputs).
It also changes the backend identity so responses from the prior local prompt
format cannot be silently reused. The 79 VariantGate tests pass after this
adapter change; Ruff and the PowerShell setup-script syntax check pass.

## Artifacts

- Initial run: `runs/variantgate_llm/biomistral_smoke_v1/`.
- Follow-up: `runs/variantgate_llm/biomistral_schema_smoke_v1/`.
- Each contains manifests, `briefs/summary.json`, `briefs/failures.jsonl`, and
  complete model response receipts under `briefs/llm_responses/`.
- Repeatable setup: `scripts/setup_variantgate_local.ps1` and
  `configs/variantgate/biomistral.Modelfile`.

These are tiny synthetic protocol/grounding pilots, with one repeated case,
not an independent four-case benchmark. They do not establish general biomedical
quality. They show that this model and prompt configuration has not met the
requirements for the current pipeline. The 24-case sweep was not started because
the smoke test failed. Real-world and clinical-quality claims remain unsupported.

## Next experiment

Evaluate a stronger instruction-tuned biomedical candidate, such as MedGemma
1.5 4B after the user accepts its download terms, alongside a general instruction
model. Preserve identical evidence, prompts, grounding checks and model receipts.
Inspect semantic support and omissions manually in addition to machine checks.
Then repair the original ClinVar cohort's identifiers and evaluate correctly
identified real variants with a domain reviewer.
