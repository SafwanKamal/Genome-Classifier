# Genomic variant dataset pipeline

This package creates the labeled and quantized dataset for the FPGA `16 -> 4 -> 1` missense
variant triage network. It implements the version 1.0 feature contract and keeps preprocessing
bit-exact with the signed INT8 FPGA input order.

The human-readable contract is included at `docs/Genomic_Variant_16_Feature_Contract.xlsx`;
`config/features.yaml` is its machine-readable counterpart.

## What is automated

1. Download the current ClinVar GRCh38 VCF.
2. Retain PASS/unfiltered, single-nucleotide, missense alleles with unambiguous benign/pathogenic labels.
3. Require at least one ClinVar review star by default.
4. Join standardized external annotation tables by normalized allele.
5. Assign entire genes to train, validation, or test.
6. Fit percentile bounds and medians using the training genes only.
7. Normalize all 16 features so higher means more pathogenic.
8. Encode each value with `clip(round(254*x - 127), -127, 127)`.
9. Validate feature order, range, class presence, uniqueness, and gene separation.

External biological annotations are deliberately not downloaded automatically because several
sources require registration, acceptance of terms, or release-specific preprocessing. See
`ANNOTATION_SCHEMA.md` for the required standardized columns.

## Setup with uv

From the repository root in PowerShell:

```powershell
uv sync --python 3.12
```

`uv sync` creates or updates `.venv` automatically from `pyproject.toml` and `uv.lock`.
You do not need to activate the environment when commands are run through `uv run`.

Run the built-in tests first:

```powershell
uv run pytest -q
```

## Pipeline

Download ClinVar:

```powershell
uv run python -m software.dataset.download_clinvar
```

Create the labeled missense cohort:

```powershell
uv run python -m software.dataset.parse_clinvar `
  --input data/raw/clinvar_grch38.vcf.gz `
  --output data/interim/clinvar_labeled.parquet `
  --min-review-stars 1
```

Extract indexed dbNSFP annotations (run this command through the WSL `uv` environment):

```bash
uv run python -m software.dataset.extract_dbnsfp \
  --limit 100 \
  --variant-output data/interim/dbnsfp_variant_scores_smoke.parquet \
  --gene-output data/interim/dbnsfp_gene_scores_smoke.parquet \
  --report data/interim/dbnsfp_extraction_report_smoke.json
```

Review `data/interim/dbnsfp_extraction_report_smoke.json`, then run the complete extraction:

```bash
uv run python -m software.dataset.extract_dbnsfp
```

The extractor uses MANE Select when available, then Ensembl canonical, APPRIS principal,
and finally the first transcript. It computes Grantham distance and BLOSUM62 locally from
the selected amino-acid substitution. dbNSFP does not include SpliceAI delta scores, so those
remain a separate annotation input.

Join one or more annotation tables:

```powershell
uv run python -m software.dataset.annotate_variants `
  --variants data/interim/clinvar_labeled.parquet `
  --annotations data/interim/variant_scores.parquet data/interim/gene_scores.csv `
  --output data/interim/variants_annotated.parquet
```

Split by gene before fitting any preprocessing values:

```powershell
uv run python -m software.dataset.split_by_gene `
  --input data/interim/variants_annotated.parquet `
  --output data/interim/splits.parquet `
  --seed 42
```

Create the float and INT8 datasets:

```powershell
uv run python -m software.dataset.build_features `
  --input data/interim/variants_annotated.parquet `
  --splits data/interim/splits.parquet `
  --contract config/features.yaml `
  --output-dir data/processed
```

Validate the FPGA-facing table:

```powershell
uv run python -m software.dataset.validate_dataset `
  --input data/processed/variants_model_int8.parquet `
  --contract config/features.yaml `
  --report data/processed/validation_report.json
```

## Principal outputs

- `variants_model_float.parquet`: normalized floating-point features in `[0,1]`.
- `variants_model_int8.parquet`: signed INT8 features in FPGA index order.
- `variants_model.csv`: portable CSV version of the INT8 table.
- `preprocessing.json`: train-fitted percentile bounds and imputation medians.
- `missingness_audit.parquet`: one missingness flag per feature and variant.
- `validation_report.json`: dataset integrity and gene-leakage checks.

## Label policy

- Positive: `Pathogenic`, `Likely pathogenic`, or the combined pathogenic/likely-pathogenic class.
- Negative: `Benign`, `Likely benign`, or the combined benign/likely-benign class.
- Excluded: VUS, conflicting classifications, risk factors, drug response, and all other classes.

ClinVar is an archive of submitted interpretations, not ground truth. Keep the ClinVar release
date and checksum with every experiment, and do not use this model for clinical decisions.
