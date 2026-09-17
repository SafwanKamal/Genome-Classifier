# VariantGate Evidence Report

Research triage output only. This report is not a clinical diagnosis.

## Aggregate summary

- Variants: 10
- Unique genes: 4
- ClinVar evidence status: {"found": 10}
- ClinVar classification groups: {"benign": 3, "missing": 3, "pathogenic": 2, "uncertain": 2}
- Direction-only comparisons: {"not_binary_comparable": 5, "same_binary_direction": 5}

## Variant preview

| Variant | Gene | Score | Route | ClinVar | Review status | Comparison |
| --- | --- | --- | --- | --- | --- | --- |
| 1:944719:T:G | NOC2L | -35 | deep_review | Uncertain significance | criteria provided, single submitter | not_binary_comparable |
| 1:1043440:C:T | AGRN | -266 | deep_review | Likely benign | criteria provided, multiple submitters, no conflicts | same_binary_direction |
| 1:1049795:G:A | AGRN | 242 | deep_review | Pathogenic | criteria provided, single submitter | same_binary_direction |
| 1:1050473:G:A | AGRN | 124 | deep_review |  |  | not_binary_comparable |
| 1:1051275:T:C | AGRN | 208 | deep_review |  |  | not_binary_comparable |
| 1:1051618:G:C | AGRN | -253 | deep_review | Likely benign | criteria provided, single submitter | same_binary_direction |
| 1:1053823:T:C | AGRN | 196 | deep_review |  |  | not_binary_comparable |
| 1:1312089:C:T | INTS11 | -260 | deep_review | Likely benign | criteria provided, single submitter | same_binary_direction |
| 1:1321072:C:A | INTS11 | 281 | deep_review | Likely pathogenic | criteria provided, single submitter | same_binary_direction |
| 1:1327303:G:A | CPTP | -151 | deep_review | Uncertain significance | criteria provided, single submitter | not_binary_comparable |

Preview shows 10 of 10 variants. Complete structured reports are stored in `variant_evidence_reports.jsonl`.

## Interpretation boundary

The FPGA output is a deterministic prioritization signal. The comparison category only checks binary direction against the retrieved ClinVar classification and is not an independent accuracy estimate.

The current model was trained from ClinVar-derived labels. Current ClinVar evidence for the same variants is therefore circular and should be used to verify retrieval, provenance, and reporting—not predictive validity.
