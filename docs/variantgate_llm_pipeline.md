# VariantGate evidence-to-brief pipeline

October 3, 2026. The research prototype now supports CPU triage → ClinVar → linked PubMed with abstracts → direct PubMed → reconciliation → structured extraction → mechanical grounding checks → claim organization → JSON/Markdown briefs.

There are three execution backends: an offline verbatim control, OpenAI Responses, and a loopback Chat Completions-compatible server. A small [local BioMistral pilot](variantgate_biomistral_pilot.md) verified live connectivity but failed extraction grounding checks. Real-world model quality has not been established. No paid LLM requests were made during implementation.

The subsequent [MedGemma 1.5 pilot](variantgate_medgemma_pilot.md) verified
authenticated acquisition and local inference. Complete-pipeline smoke cases
failed ClinVar quotation checks. An isolated literature extraction passed, but
model-based claim organization failed. The current version now renders ClinVar
statements directly from source fields and groups validated claims in code.
Only literature extraction calls the selected backend. Each claim records its
generation method; the pipeline version is `deterministic-database-and-grouping-v2`.
The [full MedGemma evaluation](variantgate_deterministic_evaluation.md) produced
eight reviewable briefs, six intentional identity blocks, and ten source-ID
rejections across all 24 fixtures. The deterministic stages pass their tests;
the live model configuration has not passed the full evaluation.

## Important finding: legacy ClinVar IDs

The dataset parser previously populated `variation_id` with INFO/`ALLELEID`. ClinVar's Variation ID is VCF column 3; Allele ID is a different identifier namespace. This can retrieve a valid ClinVar record for an entirely different variant. [NCBI identifier documentation](https://www.ncbi.nlm.nih.gov/clinvar/docs/identifiers/)

Future parsing now preserves both fields correctly. Existing datasets, checkpoints, caches and archived outputs were not rewritten. A coordinate/gene-based repair command writes a new Parquet and an audit manifest, preserving every other column—including features, labels and splits:

```powershell
uv run --with pandas --with pyarrow python -m software.variantgate.repair_identifiers_cli --input genomic-dataset-pipeline/data/processed/variants_model_int8.parquet --clinvar-vcf "C:/path/to/original/clinvar_GRCh38.vcf.gz" --assembly GRCh38 --output runs/variantgate_llm/variants_corrected_ids.parquet
```

Use the original GRCh38 cohort VCF if available. The checked-out workspace does not contain it. The tool rejects missing or duplicate source coordinates, mismatched genes, unrelated old IDs, explicitly contradictory GRCh37 references and existing output files. Correct metadata changes input hashes, so generate a new scoring/evidence run; do not combine it with old score manifests. Model arithmetic, trained parameters and feature values do not need alteration for an identifier-only repair.

All ten frozen archived smoke records are blocked by the new source-level identity gate. Earlier `variant_identity_match` flags only established agreement between copied pipeline fields; they did not validate the input against the actual ClinVar variant. Those records are useful failure examples, not evidence briefs for their claimed input variants.

## Frozen evaluation assets

`evaluation/variantgate/v2/` contains:

- `synthetic_cases.jsonl`: 24 authored fixtures across exact-variant evidence, gene-only evidence, conflicting findings, missing abstracts, absent papers, wrong genes, wrong coordinates and embedded instructions. Each case has expected identity behavior and reference passages/scopes/finding types.
- `synthetic_evidence.jsonl` and `run_manifest.json`: runnable reconciled envelopes and frozen hashes.
- `archived_evidence.jsonl`, `archived_manifest.json`, `archived_identity_audit.json`: ten existing real source envelopes preserved for identity auditing; expert finding annotations remain pending.

The synthetic genes, identifiers and experiments are invented test data. They verify software behavior, not biological performance. The separate real cohort needs correct identifiers and human annotation before an LLM-quality study. Do not describe these assets as a clinically validated or expert-adjudicated benchmark.

The freeze generator, `python -m scripts.freeze_variantgate_eval`, only creates a new directory; it refuses to overwrite a frozen corpus.

## Run an offline demonstration

From the repository root:

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 24 --output-dir runs/variantgate_llm/offline_v2
```

The cached-evidence path uses only Python's standard library. Alternatively, use the repository's Python at `genomic-dataset-pipeline/.venv/Scripts/python.exe`. It never contacts NCBI or an LLM provider. It copies source passages as an explicit plumbing control; it is not an imitation of model extraction.

Expected outcome: 24 briefs, six identity-blocked cases, no failed extraction stages. Exit code 2 means attention is required because of those intentional identity failures. `briefs/report.md` labels synthetic evidence and offline outputs and avoids external links for invented identifiers.

Evaluate it:

```powershell
python -m software.variantgate.evaluation_cli --cases evaluation/variantgate/v2/synthetic_cases.jsonl --briefs runs/variantgate_llm/offline_v2/briefs/variant_briefs.jsonl --summary runs/variantgate_llm/offline_v2/briefs/summary.json --output reports/variantgate_llm_offline_evaluation.json
```

The control passes all 24 identity and scope checks. Its typed reference-finding coverage is deliberately limited (20%): copying an abstract does not extract and categorize its experimental findings. Scripted reference responses in tests cover all authored findings; this establishes test wiring, not LLM capability. Semantic-support accuracy and reviewer usefulness remain explicitly unknown.

The evaluator verifies corpus, brief and summary hashes. Its pass/fail status is for identity/scope guards; reference quote/type coverage is reported separately. It is not a semantic correctness score. A harmful model can still produce a paraphrase unsupported by an otherwise real quotation, so expert adjudication remains necessary.

## Run a live extraction

For this PC's Ollama/BioMistral setup, weight verification and exact local test
commands, see [the local LLM experiment guide](variantgate_local_llm.md).

Choose a model explicitly. The pipeline does not select a provider model or read/print credentials on your behalf.

OpenAI backend: set `OPENAI_API_KEY` in the environment through your normal credential setup, then use:

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 24 --llm-backend openai --llm-model YOUR_MODEL_ID --max-calls 60 --max-output-tokens 4096 --output-dir runs/variantgate_llm/openai_eval_v1
```

Local backend, with a running loopback server supporting strict JSON schemas:

```powershell
python -m software.variantgate.pipeline_cli --reconciled evaluation/variantgate/v2/synthetic_evidence.jsonl --assembly GRCh38 --limit 24 --llm-backend local --llm-base-url http://localhost:8000/v1 --llm-model YOUR_LOCAL_MODEL_ID --max-calls 60 --output-dir runs/variantgate_llm/local_eval_v1
```

An LLM run sends the supplied source passages and variant identifiers to the chosen endpoint. The OpenAI adapter uses strict `text.format` JSON Schema with Responses and `store=false`, following [official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs). A refusal, truncated response, unknown fields or unsupported schema output produces a failed variant, not a partial accepted brief. Local strict-schema compatibility is server-dependent. Live Ollama 0.6.2 connectivity and JSON output were verified in the BioMistral pilot, whose extractions failed grounding validation. Local mode never forwards an OpenAI key.

No separate SDK is required. Remote arbitrary endpoints are intentionally unsupported by the local adapter. HTTP redirects are disabled to avoid forwarding credentials to another host. Failed HTTP bodies are omitted from errors. Transient HTTP retries default to zero; uncertain transport delivery is not automatically retried.

## Full workflow after metadata repair

```powershell
uv run --with numpy --with pandas --with pyarrow --with pyserial python -m software.variantgate.pipeline_cli --input runs/variantgate_llm/variants_corrected_ids.parquet --manifest reports/checkpoint_v2/export_manifest.json --routing-policy reports/checkpoint_v2/routing_policy.json --assembly GRCh38 --split test --limit 10 --llm-backend openai --llm-model YOUR_MODEL_ID --output-dir runs/variantgate_llm/complete_v1
```

Use the matching frozen model/routing pair. The existing general scoring backend supports two-layer models such as V2/V3; the separate three-layer V4 experiment is not silently substituted. CPU scoring runs over the selected split; the record limit is applied after routing to evidence collection. Otherwise a small prefix might contain no requested-route variants. NCBI stages use their existing caches, limits, retries and optional `NCBI_API_KEY`; linked articles now include abstracts when requested with `--with-abstracts`.

Source identity is checked before literature searches. The first version verifies GRCh38 SNVs by chromosome accession version, 0-based SPDI position, reference and alternate alleles, source Variation ID, and gene agreement. Explicit Variation-ID/VCV input keys are also supported. Other assemblies, indels, gene aliases and transcript equivalence require additional normalization; uncertain cases are blocked for review.

## Grounding and report contract

Each frozen evidence package records subject identity, identifiers/transcripts, triage separately, bounded source passages, content hashes, source URLs/provenance and explicit gaps. PubMed duplicates are merged by PMID; conflicting abstract texts are excluded and listed. Missing abstracts are never replaced with invented findings from titles. Article and passage limits are reported.

Extraction produces at most eight claims per passage, each with a statement, verbatim quote, evidence scope and finding type. Validation checks strict shape, real source ID, substring/character offsets, sufficient quote length, and identifier/gene/transcript presence. Database statements remain separate from literature. Validation means **mechanical checks passed**, not semantic or clinical verification.

ClinVar statements are rendered from the verified source passage's structured
fields, preserving the reported classification and review status without model
inference. A truncated structured database passage fails explicitly rather than
being reconstructed. Its original JSON passage remains the supporting quote.
Database statements are labeled `structured_database_fields`; literature claims
are labeled `model_literature_extraction` or `offline_verbatim_control`.

Code groups accepted claim IDs into database statements, variant findings, gene
context and unresolved findings using the validated finding type and scope.
The existing retention/scope validator still checks that every claim appears
exactly once in its correct section. No synthesis request is made. The renderer
produces the readable brief with supporting passages. Source/model strings are
escaped in Markdown; external citation links are restricted to expected NCBI
forms. Every brief requires human review.

## Resume, provenance and cost

Repeat the same command with `--resume`. Input/configuration/prompt/code changes require a new run directory. Completed retrieval outputs are hash-checked before reuse; failed attempts are preserved in separate stage directories. Individual model responses are content-keyed by input, prompt, schema and provider/model and are revalidated on reuse. A partially completed variant does not publish an accepted partial brief.

Each run writes `pipeline_state.json`, `run_manifest.json`, and `briefs/` containing packages, briefs, failures, report, summary and an output manifest. Model response receipts record response ID, returned model, usage and latency. State never stores API-key values.

`--max-calls` bounds new generation calls per invocation, with a per-response output-token limit. It is not a dollar cap, and HTTP retries are separate. Optional `--input-usd-per-million` and `--output-usd-per-million` record a simple user-supplied cost estimate. Without pricing/usage, cost is unknown; cached responses are not newly billed. Uncertain failed delivery and retried HTTP requests may incur unreported charges. This estimate does not handle every provider discount/tier.

Resume may increase `--max-calls` to finish a budget-limited run. A cached invalid model output remains invalid on resume; change the prompt/backend configuration and start a new run rather than silently retrying until a desired answer appears.

## Verification and remaining work

The new tests cover source/quote/identity mistakes, transcript ambiguity, conflicting abstracts, schema violations, claim invention/omission/scope changes, cache tampering, budget exhaustion, source metadata repair, API request formats/refusals, and complete CPU/retrieval/report orchestration with mocked NCBI responses. The local pilot documents response timings and failed grounding checks; no clinical usefulness is claimed.

Original verification: **79 VariantGate tests and six dataset-pipeline tests passed**.
The deterministic-stage change adds two regression tests for literature-only
backend calls and database-only completion with zero model budget; the current
VariantGate suite contains 81 tests.
The new implementation passes Ruff, and `git diff --check` passes. The finalized
offline run produces 24 briefs with six intentional identity blocks and no failed
variants; a resumed run makes zero new generation calls. The archived ten-record
run blocks all ten without invoking a backend. The current offline control is
`runs/variantgate_llm/offline_deterministic_v3/`, with its evaluation in
`reports/variantgate_llm_offline_deterministic_evaluation.json`; its unchanged 20%
typed-finding coverage remains a plumbing control. Earlier development artifacts
remain in their original directories; use the `v2` corpus/run examples above.

Next: improve source-ID output reliability and finding-type accuracy, obtain the
original ClinVar VCF path, repair identifiers into a new cohort, and have a domain
reviewer adjudicate claim support, omissions and usefulness before scaling.
Full-paper retrieval, patient phenotype/inheritance context and clinical
classification are outside this first evidence-brief implementation.
