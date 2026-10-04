# Deterministic stages and full MedGemma evaluation

October 3, 2026. The pipeline now renders ClinVar statements from verified source
fields and groups validated claims in code. MedGemma is used only for literature
extraction. The pipeline version is `deterministic-database-and-grouping-v2`.

Database statements preserve the reported classification and review status,
retain the original JSON as their supporting quote, and are labeled
`structured_database_fields`. Model literature claims are labeled
`model_literature_extraction`. Identity, quotation, database/literature separation,
and claim-retention/scope checks remain enforced. A failed literature stage does
not publish a partial brief containing only the preceding database statement.

## Verification

All 81 VariantGate tests pass. Two new regressions verify that the backend only
receives literature extraction requests and that a database-only brief succeeds
with zero generation budget. Ruff and `git diff --check` pass.

The updated offline control processes all 24 cases with six intentional identity
blocks and no failed stages. All 24 identity/scope checks pass; typed reference
coverage remains 3/15 (20%) because the control copies passages rather than
classifying findings. Resume makes zero new generation calls. The ten archived
records still fail source identity checks and make zero backend calls.

## Live 24-case run

The installed model was verified against the saved setup digest before execution:
`variantgate-medgemma:1.5-4b-q4km`, Ollama 0.6.2, Unsloth Q4_K_M text weights.
The run used the frozen v2 corpus, 4,096 model context tokens, temperature zero,
seed 42, 1,024 output tokens, and unchanged extraction prompts/grounding checks.

| Outcome | Cases |
| --- | ---: |
| Reviewable briefs containing literature claims | 2 |
| Reviewable database-only briefs with explicit literature gaps | 6 |
| Intentionally blocked wrong-identity cases | 6 |
| Rejected literature responses: source ID mismatch | 10 |
| Total evaluated | 24 |

The first run took 296.4 seconds. It made 12 generation calls, all literature
extractions, using 12,104 prompt and 1,677 completion tokens. No database or
organization generation request was made. The previous architecture's offline
execution required 48 generation calls for this corpus; the new architecture
requires 12. This is a call-count comparison, not a controlled speed benchmark.
The model was unloaded after testing to release RAM.

The frozen evaluator reports **fail**: 14/24 identity checks and 24/24 scope
checks, with 1/15 typed reference findings covered (6.7%). Its identity metric
requires a published brief to exist; the ten failures are missing briefs after
source-ID rejection, not ten additional coordinate/gene identity mismatches.
Scope checks also treat missing literature claims as empty, so that metric must
not be read as extraction completeness or overall model accuracy.

A separate diagnostic mapped each raw response back to its request via the
content-addressed cache key. All 12 responses contained verbatim quotations, but
only two returned the complete source ID. The others returned a shorter PMID
identifier instead of the required content-addressed source ID. The diagnostic
does not rewrite or accept rejected outputs. Several raw findings also differed
from the authored finding-type labels, including negative assay results labeled
functional and gene background labeled functional.

These invented fixtures establish software behavior and reveal model/protocol
failures. They do not establish clinical performance. Semantic-support accuracy
and reviewer usefulness remain unmeasured; every published brief requires review.

## Artifacts

- Live briefs and failures: `runs/variantgate_llm/medgemma_deterministic_v2/briefs/`.
- Live evaluation: `reports/variantgate_medgemma_deterministic_evaluation.json`.
- Raw-response diagnostic: `reports/variantgate_medgemma_response_diagnostics.json`.
- Frozen model receipt: `runs/variantgate_llm/medgemma_deterministic_v2_model_receipt.json`.
- Updated offline control: `runs/variantgate_llm/offline_deterministic_v3/`.
- Offline evaluation: `reports/variantgate_llm_offline_deterministic_evaluation.json`.
- Archived identity control: `runs/variantgate_llm/archived_deterministic_v3/`.

## Next requirements

Constrain source identifiers in the per-request output schema or bind source
metadata directly to the request, while retaining quotation checks against that
specific passage. This avoids asking a model to reproduce an arbitrary hash.
Evaluate finding-type labels and preservation of negative/conflicting findings
separately. Those follow-up changes have not been implemented in this run.

After protocol and extraction quality improve, repair the real cohort using the
original GRCh38 ClinVar VCF, then obtain a domain review of support, omissions and
usefulness. The original-source VCF remains unavailable in this checkout.
