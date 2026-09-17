from __future__ import annotations

import unittest

from software.variantgate.reports import (
    BENIGN,
    CONFLICTING,
    OTHER,
    PATHOGENIC,
    UNCERTAIN,
    aggregate_reports,
    build_variant_report,
    comparison_group,
    significance_group,
)


def evidence_record(
    variant_key: str,
    gene: str,
    score: int,
    classification: str,
) -> dict[str, object]:
    return {
        "variant": {
            "variant_key": variant_key,
            "gene": gene,
            "score": score,
            "route": "deep_review",
            "clinvar_variation_id": 1,
        },
        "provider": {
            "name": "ncbi_clinvar_esummary",
            "database": "ClinVar",
            "retrieved_at": "2026-09-06T00:00:00+00:00",
            "request_url": "https://example.test",
            "cache_hit": False,
            "raw_summary_sha256": "a" * 64,
        },
        "evidence": {
            "status": "found",
            "accession": "VCV000000001",
            "accession_version": "VCV000000001.1",
            "title": "Example",
            "germline_classification": {
                "description": classification,
                "review_status": "criteria provided",
                "last_evaluated": "2026/01/01 00:00",
                "traits": [],
            },
            "genes": [],
            "molecular_consequences": [],
            "supporting_submission_counts": {"scv": 1, "rcv": 1},
            "clinvar_url": "https://example.test/variation/1",
        },
    }


class SignificanceTest(unittest.TestCase):
    def test_groups_common_clinvar_classifications(self) -> None:
        self.assertEqual(significance_group("Likely pathogenic"), PATHOGENIC)
        self.assertEqual(significance_group("Likely benign"), BENIGN)
        self.assertEqual(
            significance_group("Uncertain significance"),
            UNCERTAIN,
        )
        self.assertEqual(
            significance_group("Conflicting classifications"),
            CONFLICTING,
        )
        self.assertEqual(significance_group("risk factor"), OTHER)

    def test_binary_direction_comparison(self) -> None:
        self.assertEqual(
            comparison_group(1, PATHOGENIC),
            "same_binary_direction",
        )
        self.assertEqual(
            comparison_group(0, PATHOGENIC),
            "different_binary_direction",
        )
        self.assertEqual(
            comparison_group(1, UNCERTAIN),
            "not_binary_comparable",
        )


class ReportTest(unittest.TestCase):
    def test_builds_auditable_variant_report(self) -> None:
        report = build_variant_report(
            evidence_record("1:100:A:G", "GENE1", 25, "Pathogenic")
        )

        self.assertEqual(report["fpga_gate"]["binary_prediction"], 1)
        self.assertEqual(
            report["comparison"]["category"],
            "same_binary_direction",
        )
        self.assertEqual(
            report["provenance"]["provider"],
            "ncbi_clinvar_esummary",
        )
        self.assertTrue(report["warnings"])

    def test_aggregates_reports(self) -> None:
        reports = [
            build_variant_report(
                evidence_record("1:100:A:G", "GENE1", 25, "Pathogenic")
            ),
            build_variant_report(
                evidence_record("1:200:C:T", "GENE2", -25, "Benign")
            ),
        ]

        aggregate = aggregate_reports(reports)

        self.assertEqual(aggregate.variant_number, 2)
        self.assertEqual(aggregate.unique_gene_number, 2)
        self.assertEqual(
            aggregate.classification_group_counts,
            {"benign": 1, "pathogenic": 1},
        )
        self.assertEqual(
            aggregate.comparison_counts,
            {"same_binary_direction": 2},
        )


if __name__ == "__main__":
    unittest.main()
