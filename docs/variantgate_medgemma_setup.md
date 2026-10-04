# MedGemma 1.5 local setup

The user has accepted Google's MedGemma license on Hugging Face. The local CLI
must also authenticate as that account to verify access to the gated original
repository. Browser login and CLI login are separate.

Local setup and live inference are now verified. See
[the completed pilot](variantgate_medgemma_pilot.md): the three complete-pipeline
cases failed quotation checks; an isolated literature extraction passed, but
its claim-organization step failed. No complete accepted brief was produced.
Those observations describe the original pilot. The current pipeline renders
ClinVar statements and groups validated claims in code, leaving only literature
extraction to the model; see the pipeline guide for the subsequent full run.

## Authenticate

In PowerShell, run:

```powershell
uvx hf auth login
```

Follow the browser authorization instructions and sign in with the account that
accepted the model terms. There is no need to paste a token into this chat.
The CLI stores credentials locally; our scripts do not print or copy them.
See [Hugging Face's login documentation](https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login).

## Download and import

From the project root:

```powershell
./scripts/setup_variantgate_medgemma.ps1
```

This first verifies authenticated access to
[`google/medgemma-1.5-4b-it`](https://huggingface.co/google/medgemma-1.5-4b-it)
by downloading its configuration. It then downloads the pinned Q4_K_M text
weights from
[`unsloth/medgemma-1.5-4b-it-GGUF`](https://huggingface.co/unsloth/medgemma-1.5-4b-it-GGUF),
checks their SHA-256 and imports them into Ollama. Unsloth publishes this
quantization; it is not an official Google weight distribution. The vision
projector is unnecessary for VariantGate text extraction and is not downloaded.

The weight file is 2,489,894,976 bytes (about 2.49 GB). Files stay under
`%LOCALAPPDATA%/VariantGate/models/medgemma-1.5-4b`, outside the OneDrive checkout.
The imported name is `variantgate-medgemma:1.5-4b-q4km`.
A receipt records source revisions, weight hash, Ollama model digest and runtime
version at `runs/variantgate_llm/medgemma_setup/model_receipt.json`.
The profile uses 4,096 context tokens, temperature zero, seed 42 and 1,024 output
tokens. It supplies an explicit Gemma 3 chat template because Ollama 0.6.2
otherwise imports these weights with a generic prompt template. The system
instruction is prepended to the first user turn, matching Google's text chat
format. The HTTP downloader avoids Xet's large memory buffers on this PC.
Runtime compatibility and actual inference must pass before interpreting
the setup as complete. Ollama's generic `medgemma` registry tag links the older
v1 model card; it must not be substituted and labeled as MedGemma 1.5.

## Smoke test

Use a new output directory for each configuration. Start with the same three
short synthetic cases used for BioMistral:

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 3 --llm-backend local --llm-base-url http://127.0.0.1:11434/v1 --llm-model variantgate-medgemma:1.5-4b-q4km --llm-timeout 300 --max-output-tokens 1024 --max-calls 12 --output-dir runs/variantgate_llm/medgemma_smoke_v2
```

Inspect `briefs/summary.json`, `briefs/failures.jsonl`, and the verbatim quotations
in `briefs/report.md`. Passing JSON syntax is insufficient. Grounding checks must
pass, and a reviewer must still assess the meaning of every claim. If the smoke
test succeeds, run the full 24-case corpus in a fresh directory using `--limit 24`
and `--max-calls 60`, then use the evaluator in the
[pipeline guide](variantgate_llm_pipeline.md).

After the run, release RAM while retaining the downloaded model:

```powershell
ollama stop variantgate-medgemma:1.5-4b-q4km
```

Correctly identified real-variant testing remains dependent on repairing the
legacy cohort against the original GRCh38 ClinVar VCF. Model acquisition does
not resolve that separate input-data problem.
