# Annotation input schema

`annotate_variants.py` joins one or more CSV, TSV, or Parquet tables to the labeled ClinVar
cohort. Every annotation table must contain a unique `variant_key`, or the four columns
`chrom`, `pos`, `ref`, and `alt` from which the key can be constructed.
`data/annotation_template.csv` supplies the complete header for a single-table workflow.

Before feature construction, the merged table must contain these raw columns:

| Raw column | Meaning |
|---|---|
| `gnomad_popmax_af` | Maximum ancestry-specific gnomAD AF; use `0` only for a confidently absent, adequately covered allele |
| `cadd_phred` | CADD PHRED score |
| `revel_score` | REVEL score on the selected transcript |
| `alphamissense_score` | AlphaMissense pathogenicity score |
| `spliceai_ds_ag`, `spliceai_ds_al`, `spliceai_ds_dg`, `spliceai_ds_dl` | Four SpliceAI delta scores |
| `sift_score` | Raw SIFT score; the pipeline reverses its direction |
| `polyphen2_hvar_score` | PolyPhen-2 HVAR probability |
| `mpc_score` | MPC score |
| `phylop_100way` | hg38 phyloP 100-way value |
| `phastcons_100way` | hg38 phastCons 100-way value |
| `gerp_rs` | GERP++ rejected-substitution value |
| `grantham_distance` | Grantham distance for the MANE Select amino-acid change |
| `blosum62_score` | BLOSUM62 score for the same amino-acid change |
| `gnomad_mis_z` | gnomAD gene missense-constraint Z score |
| `gnomad_loeuf` | gnomAD gene LOEUF |
| `interpro_domain_status` | `1` overlap, `0` verified no overlap, or `unknown` |

Transcript-dependent annotations must use MANE Select. If MANE Select is unavailable, apply
one documented fallback consistently and retain the selected transcript ID in the annotation
table. Never silently combine scores from different transcripts.

This package does not redistribute CADD, REVEL, AlphaMissense, MPC, dbNSFP, or other licensed
annotation data. Obtain each source under its applicable terms and convert it to this schema.
