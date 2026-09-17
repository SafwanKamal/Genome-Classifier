from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


PATHOGENIC = "pathogenic"
BENIGN = "benign"
UNCERTAIN = "uncertain"
CONFLICTING = "conflicting"
OTHER = "other"
MISSING = "missing"


def significance_group(description: object) -> str:
    if description is None:
        return MISSING

    normalized = str(description).strip().lower()

    if not normalized:
        return MISSING

    if "conflict" in normalized:
        return CONFLICTING

    if "uncertain" in normalized or "vus" in normalized:
        return UNCERTAIN

    if "benign" in normalized:
        return BENIGN

    if "pathogenic" in normalized:
        return PATHOGENIC

    return OTHER


def comparison_group(model_prediction: int, clinvar_group: str) -> str:
    if clinvar_group == PATHOGENIC:
        return (
            "same_binary_direction"
            if model_prediction == 1
            else "different_binary_direction"
        )

    if clinvar_group == BENIGN:
        return (
            "same_binary_direction"
            if model_prediction == 0
            else "different_binary_direction"
        )

    return "not_binary_comparable"


def build_variant_report(record: dict[str, object]) -> dict[str, object]:
    variant = record.get("variant")
    provider = record.get("provider")
    evidence = record.get("evidence")

    if not isinstance(variant, dict):
        raise ValueError("Evidence record has no valid variant object")

    if not isinstance(provider, dict):
        raise ValueError("Evidence record has no valid provider object")

    if not isinstance(evidence, dict):
        raise ValueError("Evidence record has no valid evidence object")

    variant_key = str(variant.get("variant_key", ""))

    if not variant_key:
        raise ValueError("Evidence record has no variant_key")

    score = int(variant["score"])
    model_prediction = int(score >= 0)
    evidence_status = str(evidence.get("status", "missing"))
    germline = evidence.get("germline_classification") or {}

    if not isinstance(germline, dict):
        raise ValueError("Invalid ClinVar germline classification object")

    description = germline.get("description")
    clinvar_group = (
        significance_group(description)
        if evidence_status == "found"
        else MISSING
    )
    comparison = comparison_group(model_prediction, clinvar_group)
    warnings = [
        "Research triage output; not a clinical diagnosis.",
        (
            "ClinVar evidence is not independent of the current model's "
            "ClinVar-derived labels."
        ),
    ]

    if evidence_status != "found":
        warnings.append("No ClinVar summary was found for this Variation ID.")

    if clinvar_group in {UNCERTAIN, CONFLICTING, OTHER, MISSING}:
        warnings.append(
            "ClinVar evidence is not directly comparable to a binary model "
            "prediction."
        )

    return {
        "format_version": 1,
        "subject": {
            "variant_key": variant_key,
            "gene": variant.get("gene"),
            "clinvar_variation_id": variant.get("clinvar_variation_id"),
        },
        "fpga_gate": {
            "score": score,
            "binary_prediction": model_prediction,
            "classification_rule": "score >= 0",
            "route": variant.get("route"),
        },
        "clinvar_statement": {
            "status": evidence_status,
            "accession": evidence.get("accession"),
            "accession_version": evidence.get("accession_version"),
            "title": evidence.get("title"),
            "classification": description,
            "classification_group": clinvar_group,
            "review_status": germline.get("review_status"),
            "last_evaluated": germline.get("last_evaluated"),
            "traits": germline.get("traits") or [],
            "genes": evidence.get("genes") or [],
            "molecular_consequences": (
                evidence.get("molecular_consequences") or []
            ),
            "protein_change": evidence.get("protein_change"),
            "supporting_submission_counts": (
                evidence.get("supporting_submission_counts") or {}
            ),
        },
        "comparison": {
            "category": comparison,
            "interpretation": (
                "Direction-only comparison for audit; not an independent "
                "accuracy measurement."
            ),
        },
        "provenance": {
            "provider": provider.get("name"),
            "database": provider.get("database"),
            "retrieved_at": provider.get("retrieved_at"),
            "request_url": provider.get("request_url"),
            "clinvar_url": evidence.get("clinvar_url"),
            "cache_hit": provider.get("cache_hit"),
            "raw_summary_sha256": provider.get("raw_summary_sha256"),
        },
        "warnings": warnings,
    }


@dataclass(frozen=True, slots=True)
class AggregateReport:
    variant_number: int
    unique_gene_number: int
    evidence_status_counts: dict[str, int]
    route_counts: dict[str, int]
    classification_group_counts: dict[str, int]
    comparison_counts: dict[str, int]
    review_status_counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "variant_number": self.variant_number,
            "unique_gene_number": self.unique_gene_number,
            "evidence_status_counts": self.evidence_status_counts,
            "route_counts": self.route_counts,
            "classification_group_counts": self.classification_group_counts,
            "comparison_counts": self.comparison_counts,
            "review_status_counts": self.review_status_counts,
        }


def aggregate_reports(
    reports: Iterable[dict[str, object]],
) -> AggregateReport:
    report_list = list(reports)

    if not report_list:
        raise ValueError("At least one variant report is required")

    genes = {
        report["subject"]["gene"]
        for report in report_list
        if report["subject"].get("gene")
    }
    evidence_status_counts = Counter(
        str(report["clinvar_statement"]["status"])
        for report in report_list
    )
    route_counts = Counter(
        str(report["fpga_gate"]["route"])
        for report in report_list
    )
    classification_group_counts = Counter(
        str(report["clinvar_statement"]["classification_group"])
        for report in report_list
    )
    comparison_counts = Counter(
        str(report["comparison"]["category"])
        for report in report_list
    )
    review_status_counts = Counter(
        str(report["clinvar_statement"]["review_status"] or "missing")
        for report in report_list
    )

    return AggregateReport(
        variant_number=len(report_list),
        unique_gene_number=len(genes),
        evidence_status_counts=dict(sorted(evidence_status_counts.items())),
        route_counts=dict(sorted(route_counts.items())),
        classification_group_counts=dict(
            sorted(classification_group_counts.items())
        ),
        comparison_counts=dict(sorted(comparison_counts.items())),
        review_status_counts=dict(sorted(review_status_counts.items())),
    )
