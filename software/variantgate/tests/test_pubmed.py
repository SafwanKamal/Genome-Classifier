from __future__ import annotations

import unittest

from software.variantgate.evidence.pubmed import (
    normalize_pubmed_summary,
    parse_elink_response,
)


class PubMedLinkTest(unittest.TestCase):
    def test_preserves_variant_to_pubmed_links(self) -> None:
        parsed = parse_elink_response(
            {
                "linksets": [
                    {
                        "ids": ["10"],
                        "linksetdbs": [
                            {
                                "dbto": "pubmed",
                                "linkname": "clinvar_pubmed",
                                "links": ["101", "102"],
                            }
                        ],
                    },
                    {
                        "ids": ["20"],
                        "linksetdbs": [],
                    },
                ]
            }
        )

        self.assertEqual(parsed, {10: ["101", "102"], 20: []})

    def test_rejects_combined_linkset(self) -> None:
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            parse_elink_response(
                {
                    "linksets": [
                        {
                            "ids": ["10", "20"],
                            "linksetdbs": [],
                        }
                    ]
                }
            )


class PubMedSummaryTest(unittest.TestCase):
    def test_normalizes_article_metadata(self) -> None:
        normalized = normalize_pubmed_summary(
            "12345",
            {
                "title": "Example article",
                "authors": [{"name": "A Author"}, {"name": "B Author"}],
                "lastauthor": "B Author",
                "fulljournalname": "Example Journal",
                "pubdate": "2026 Jan",
                "epubdate": "2025 Dec 10",
                "pubtype": ["Journal Article"],
                "articleids": [
                    {"idtype": "doi", "value": "10.1000/example"},
                    {"idtype": "pmc", "value": "PMC123"},
                ],
            },
        )

        self.assertEqual(normalized["status"], "found")
        self.assertEqual(normalized["doi"], "10.1000/example")
        self.assertEqual(normalized["pmc_id"], "PMC123")
        self.assertEqual(normalized["authors"], ["A Author", "B Author"])

    def test_normalizes_missing_article(self) -> None:
        self.assertEqual(
            normalize_pubmed_summary("12345", None),
            {"status": "not_found", "pmid": "12345"},
        )


if __name__ == "__main__":
    unittest.main()
