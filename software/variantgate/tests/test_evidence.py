from __future__ import annotations

import unittest

from software.variantgate.evidence.clinvar import (
    ClinVarSummary,
    normalize_summary,
)
from software.variantgate.evidence.identifiers import (
    parse_variation_id,
    resolve_variation_id_column,
)


class IdentifierTest(unittest.TestCase):
    def test_parses_supported_clinvar_identifiers(self) -> None:
        self.assertEqual(parse_variation_id(65533), 65533)
        self.assertEqual(parse_variation_id("65533"), 65533)
        self.assertEqual(parse_variation_id("VCV000065533.11"), 65533)
        self.assertEqual(parse_variation_id("variation_id=65533"), 65533)
        self.assertEqual(parse_variation_id("clinvar:65533"), 65533)

    def test_rejects_coordinates_and_invalid_values(self) -> None:
        self.assertIsNone(parse_variation_id("1:235809278:G:A"))
        self.assertIsNone(parse_variation_id(0))
        self.assertIsNone(parse_variation_id(-1))
        self.assertIsNone(parse_variation_id(None))

    def test_resolves_known_column_names(self) -> None:
        self.assertEqual(
            resolve_variation_id_column(
                ["variant_key", "gene", "VariationID"],
                None,
            ),
            "VariationID",
        )

    def test_requested_column_must_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "is not present"):
            resolve_variation_id_column(
                ["variant_key", "gene"],
                "VariationID",
            )


class ClinVarNormalizationTest(unittest.TestCase):
    def test_normalizes_clinvar_summary(self) -> None:
        summary = ClinVarSummary(
            variation_id=65533,
            retrieved_at="2026-09-06T00:00:00+00:00",
            request_url="https://example.test/esummary",
            cached=False,
            raw_summary={
                "uid": "65533",
                "accession": "VCV000065533",
                "accession_version": "VCV000065533.11",
                "title": "Example variant",
                "obj_type": "single nucleotide variant",
                "variation_set": [
                    {
                        "canonical_spdi": "NC_000001.11:235809277:G:A",
                        "variation_xrefs": [
                            {"db_source": "dbSNP", "db_id": "80338645"}
                        ],
                    }
                ],
                "supporting_submissions": {
                    "scv": ["SCV1", "SCV2"],
                    "rcv": ["RCV1"],
                },
                "germline_classification": {
                    "description": "Pathogenic",
                    "review_status": "criteria provided",
                    "last_evaluated": "2025/10/18 00:00",
                    "trait_set": [
                        {
                            "trait_name": "Example condition",
                            "trait_xrefs": [
                                {
                                    "db_source": "MONDO",
                                    "db_id": "MONDO:1",
                                }
                            ],
                        }
                    ],
                },
                "genes": [
                    {"symbol": "GENE1", "geneid": "1", "source": "submitted"}
                ],
                "molecular_consequence_list": ["missense variant"],
                "protein_change": "R1W",
            },
        )

        normalized = normalize_summary(summary)

        self.assertEqual(normalized["status"], "found")
        self.assertEqual(normalized["accession"], "VCV000065533")
        self.assertEqual(
            normalized["germline_classification"]["description"],
            "Pathogenic",
        )
        self.assertEqual(
            normalized["supporting_submission_counts"],
            {"scv": 2, "rcv": 1},
        )

    def test_normalizes_missing_record(self) -> None:
        normalized = normalize_summary(
            ClinVarSummary(
                variation_id=999,
                retrieved_at="2026-09-06T00:00:00+00:00",
                request_url="https://example.test/esummary",
                cached=False,
                raw_summary=None,
            )
        )

        self.assertEqual(
            normalized,
            {"status": "not_found", "variation_id": 999},
        )


if __name__ == "__main__":
    unittest.main()
