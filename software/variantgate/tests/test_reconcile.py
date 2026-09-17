from __future__ import annotations

import unittest

from software.variantgate.reconcile_cli import reconcile_record


def clinvar_record() -> dict[str, object]:
    return {
        "variant": {
            "variant_key": "1:100:A:G",
            "gene": "GENE1",
            "score": 25,
            "route": "deep_review",
            "clinvar_variation_id": 10,
        },
        "provider": {"name": "ncbi_clinvar_esummary"},
        "evidence": {
            "status": "found",
            "germline_classification": {
                "description": "Pathogenic",
            },
        },
    }


def pubmed_record(article_number: int = 1) -> dict[str, object]:
    return {
        "variant": {
            "variant_key": "1:100:A:G",
            "gene": "GENE1",
            "score": 25,
            "route": "deep_review",
            "clinvar_variation_id": 10,
        },
        "provider": {"name": "ncbi_clinvar_pubmed"},
        "evidence": {
            "status": "found" if article_number else "no_linked_articles",
            "linked_article_number": article_number,
            "pmids": ["123"] if article_number else [],
            "articles": [{"pmid": "123"}] if article_number else [],
        },
    }


def direct_pubmed_record(article_number: int = 2) -> dict[str, object]:
    return {
        "variant": {
            "variant_key": "1:100:A:G",
            "gene": "GENE1",
            "score": 25,
            "route": "deep_review",
            "clinvar_variation_id": 10,
        },
        "provider": {"name": "ncbi_pubmed_direct_search"},
        "evidence": {
            "status": "found" if article_number else "no_direct_articles",
            "direct_article_number": article_number,
            "also_clinvar_linked_number": 1 if article_number else 0,
            "direct_only_number": max(article_number - 1, 0),
            "direct_pmids": ["123", "456"][:article_number],
            "articles": [],
        },
    }


class ReconciliationTest(unittest.TestCase):
    def test_reconciles_matching_evidence(self) -> None:
        reconciled = reconcile_record(clinvar_record(), pubmed_record())

        self.assertTrue(
            reconciled["verification"]["variant_identity_match"]
        )
        self.assertEqual(
            reconciled["verification"]["linked_article_number"],
            1,
        )
        self.assertEqual(reconciled["verification"]["issues"], [])

    def test_flags_missing_literature(self) -> None:
        reconciled = reconcile_record(clinvar_record(), pubmed_record(0))

        self.assertIn(
            "no_clinvar_linked_pubmed_articles",
            reconciled["verification"]["issues"],
        )

    def test_rejects_variant_identity_mismatch(self) -> None:
        mismatched = pubmed_record()
        mismatched["variant"]["score"] = 24

        with self.assertRaisesRegex(ValueError, "score"):
            reconcile_record(clinvar_record(), mismatched)

    def test_reconciles_direct_and_linked_literature(self) -> None:
        reconciled = reconcile_record(
            clinvar_record(),
            pubmed_record(),
            direct_pubmed_record(),
        )

        self.assertEqual(reconciled["verification"]["provider_number"], 3)
        self.assertEqual(
            reconciled["verification"]["direct_only_article_number"],
            1,
        )
        self.assertIn("direct_pubmed", reconciled["evidence"])

    def test_flags_no_direct_literature(self) -> None:
        reconciled = reconcile_record(
            clinvar_record(),
            pubmed_record(),
            direct_pubmed_record(0),
        )

        self.assertIn(
            "no_direct_pubmed_articles",
            reconciled["verification"]["issues"],
        )


if __name__ == "__main__":
    unittest.main()
