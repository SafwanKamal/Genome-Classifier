# Local biomedical LLM experiment

This machine has an AMD Ryzen 7 8845HS, Radeon 780M integrated graphics, and
14,806,552,576 bytes of usable RAM. Ollama 0.6.2 is already installed. Its server
reports no compatible GPU here, so this first experiment uses CPU inference.
Keep several GB of RAM available; disk capacity is sufficient.

## First model

Use the authors' [BioMistral-7B Q4_K_M GGUF](https://huggingface.co/BioMistral/BioMistral-7B-GGUF).
The 4,368,439,424-byte weight file has SHA-256
`0fc1397c3eb2ba46904540accce468479c17aaabe4c51f06665fddc1babef75d`.
It is Apache-2.0 and was further pretrained on PubMed Central. This is an
accessible biomedical research baseline, not an established best model for
VariantGate. Medical QA benchmark results do not establish faithful extraction
from supplied passages.

The authors report a 2,048-token training sequence length. Our model profile uses
that context length, temperature zero, seed 42, and 768 output tokens. Start with
short synthetic fixtures. Ollama can truncate oversized prompts, so this profile
must not be used for arbitrary production abstracts or full papers without a
token-budget check and a suitable context strategy. Increasing `num_ctx` alone
does not prove long-context accuracy.

## Setup

From the repository root in PowerShell:

```powershell
./scripts/setup_variantgate_local.ps1
```

The script pulls the official model, verifies the complete weight hash, creates
`variantgate-biomistral:7b-q4km`, and writes a model/version/digest receipt to
`runs/variantgate_llm/local_setup/model_receipt.json`. Weights stay in Ollama's
user model cache, outside the OneDrive project checkout. Ollama's
[compatible API](https://docs.ollama.com/api/openai-compatibility) is served at
`http://127.0.0.1:11434/v1`; the existing VariantGate adapter can use it.

If the Ollama pull stalls, download the pinned file directly and import it:

```powershell
$localWeights = Join-Path $env:LOCALAPPDATA 'VariantGate/models'
New-Item -ItemType Directory -Force $localWeights | Out-Null
curl.exe --fail --location --retry 2 --connect-timeout 20 --speed-time 30 --speed-limit 1024 --output "$localWeights/biomistral-7b-q4km.gguf" 'https://huggingface.co/BioMistral/BioMistral-7B-GGUF/resolve/de8c2dfcead24fd23ccb33f6ca5ff015e9ecdb4b/ggml-model-Q4_K_M.gguf'
./scripts/setup_variantgate_local.ps1 -WeightPath "$localWeights/biomistral-7b-q4km.gguf"
```

The importer verifies the same hash before creating a local profile. Never import
a partial download. Files in this fallback directory also remain outside
OneDrive. Re-running setup does not remove the existing DeepSeek model.

## Test extraction

No API key or paid request is needed. Run three cases first:

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 3 --llm-backend local --llm-base-url http://127.0.0.1:11434/v1 --llm-model variantgate-biomistral:7b-q4km --llm-timeout 300 --max-output-tokens 768 --max-calls 12 --output-dir runs/variantgate_llm/biomistral_smoke_v2
```

Inspect `briefs/report.md`, `briefs/failures.jsonl`, and the cached response
receipts. These first cases test protocol/extraction; they do not exercise every
failure category. If that works, use a fresh directory and all 24 cases:

The local adapter supplies the schema in the system prompt as well as
`response_format`, and explicitly sets temperature zero, following
[Ollama's structured-output guidance](https://docs.ollama.com/capabilities/structured-outputs).
Grammar-constrained JSON alone does not ensure factual or quoted-text accuracy.

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 24 --llm-backend local --llm-base-url http://127.0.0.1:11434/v1 --llm-model variantgate-biomistral:7b-q4km --llm-timeout 300 --max-output-tokens 768 --max-calls 60 --output-dir runs/variantgate_llm/biomistral_eval_v1
python -m software.variantgate.evaluation_cli --cases evaluation/variantgate/v2/synthetic_cases.jsonl --briefs runs/variantgate_llm/biomistral_eval_v1/briefs/variant_briefs.jsonl --summary runs/variantgate_llm/biomistral_eval_v1/briefs/summary.json --output reports/variantgate_biomistral_evaluation.json
```

Exit code 2 can mean intentional blocked identities or actual extraction failures;
read the summary. Coverage and identity/scope checks are software proxies. Human
review of unsupported paraphrases, omissions, contradictions, and usefulness is
still required. Keep model receipts with each experiment; changing an Ollama tag
requires a new run directory because a tag name alone does not freeze weights.

After testing, release the model's RAM while retaining the installed weights:

```powershell
ollama stop variantgate-biomistral:7b-q4km
```

The next request automatically loads it again.

## Better comparisons and real data

After accepting MedGemma's terms, follow
[the MedGemma setup guide](variantgate_medgemma_setup.md) for local authentication,
a pinned quantized download and the matching smoke-test command.

The completed pilot is documented in
[the local model results](variantgate_biomistral_pilot.md). The initial three
cases all failed quotation checks. A one-case follow-up with the schema included
in the prompt copied a genuine quote but failed database/literature separation.
No accepted brief was published. This configuration has not passed the smoke
test; do not start the full evaluation merely because the server responds.

[MedGemma 1.5 4B instruction tuned](https://huggingface.co/google/medgemma-1.5-4b-it)
is a second medical candidate worth evaluating. Its download requires the user
to log in to Hugging Face and accept the Health AI Developer Foundations terms.
Do not use a public mirror to bypass that requirement. A compatible quantization
and runtime must then be verified. Compare it and a general instruction model on
the same frozen corpus; biomedical specialization may help terminology but may
also introduce unsupported prior knowledge.

Real-cohort testing additionally requires the original GRCh38 ClinVar VCF to
repair the legacy Allele-ID/Variation-ID mix-up. The repair command and complete
CPU → evidence → brief command are in [the pipeline guide](variantgate_llm_pipeline.md).
The current frozen real records are blocked by identity checks. They cannot be
used to claim successful variant interpretation.
