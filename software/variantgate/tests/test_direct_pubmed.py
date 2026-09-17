from __future__ import annotations

import unittest

from software.variantgate.evidence.direct_pubmed import (
    assess_retrieval_relevance,
    build_search_plans,
    extract_identifiers,
    linked_pmids_by_variant,
)
from software.variantgate.evidence.pubmed import (
    parse_esearch_response,
    parse_pubmed_abstracts,
)


def clinvar_record() -> dict[str, object]:
    return {
        "variant": {
            "variant_key": "1:100:A:G",
            "gene": "GENE1",
            "score": 25,
            "route": "deep_review",
            "clinvar_variation_id": 10,
        },
        "evidence": {
            "title": "NM_000001.2(GENE1):c.123A>G (p.Arg41Gly)",
            "protein_change": "R41G",
            "canonical_spdi": ["NC_000001.11:99:A:G"],
            "database_cross_references": [
                {"database": "dbSNP", "identifier": "rs123"}
            ],
            "germline_classification": {
                "traits": [{"name": "Example syndrome"}]
            },
        },
    }


class IdentifierExtractionTest(unittest.TestCase):
    def test_extracts_identifiers_without_inventing_assembly(self) -> None:
        result = extract_identifiers(clinvar_record())

        self.assertEqual(result["rsids"], ["rs123"])
        self.assertIn("c.123A>G", result["hgvs"])
        self.assertIn("p.Arg41Gly", result["hgvs"])
        self.assertEqual(result["protein_changes"], ["R41G"])
        self.assertEqual(result["coordinates"]["position"], "100")
        self.assertFalse(result["assembly_known"])
        self.assertFalse(result["coordinate_hgvs_generated"])

    def test_builds_bounded_specific_queries(self) -> None:
        identifiers = extract_identifiers(clinvar_record())
        plans = build_search_plans(identifiers, maximum_query_number=2)

        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0].strategy, "rsid_exact")
        self.assertEqual(plans[0].query, '"rs123"[Title/Abstract]')
        self.assertIn('"GENE1"[Title/Abstract]', plans[1].query)


class DirectSearchParsingTest(unittest.TestCase):
    def test_parses_esearch_json(self) -> None:
        result = parse_esearch_response(
            {
                "esearchresult": {
                    "count": "3",
                    "idlist": ["11", "12", "11"],
                    "querytranslation": "translated query",
                }
            }
        )

        self.assertEqual(result["count"], 3)
        self.assertEqual(result["pmids"], ["11", "12"])
        self.assertEqual(result["query_translation"], "translated query")

    def test_parses_structured_abstract(self) -> None:
        result = parse_pubmed_abstracts(
            b"""<?xml version='1.0'?>
            <PubmedArticleSet>
              <PubmedArticle>
                <MedlineCitation>
                  <PMID>123</PMID>
                  <Article><Abstract>
                    <AbstractText Label='BACKGROUND'>First section.</AbstractText>
                    <AbstractText>Second section.</AbstractText>
                  </Abstract></Article>
                  <MeshHeadingList><MeshHeading>
                    <DescriptorName>Genetic Testing</DescriptorName>
                  </MeshHeading></MeshHeadingList>
                  <KeywordList><Keyword>missense</Keyword></KeywordList>
                </MedlineCitation>
              </PubmedArticle>
            </PubmedArticleSet>"""
        )

        self.assertIn("BACKGROUND: First section.", result["123"]["abstract"])
        self.assertEqual(result["123"]["mesh_terms"], ["Genetic Testing"])
        self.assertEqual(result["123"]["keywords"], ["missense"])


class RelevanceAssessmentTest(unittest.TestCase):
    def test_exact_variant_outranks_gene_context(self) -> None:
        identifiers = extract_identifiers(clinvar_record())
        exact = assess_retrieval_relevance(
            {
                "title": "Functional analysis of GENE1 rs123",
                "abstract": "A patient cohort and activity assay.",
            },
            identifiers,
            ["rsid_exact"],
        )
        context = assess_retrieval_relevance(
            {
                "title": "GENE1 variants in Example syndrome",
                "abstract": "A patient cohort.",
            },
            identifiers,
            ["gene_trait_context"],
        )

        self.assertEqual(exact["tier"], "exact_variant")
        self.assertEqual(context["tier"], "gene_variant_context")
        self.assertGreater(exact["score"], context["score"])
        self.assertIn("not evidence strength", exact["score_meaning"])

    def test_flags_unverified_search_hit(self) -> None:
        result = assess_retrieval_relevance(
            {"title": "Unrelated article", "abstract": None},
            extract_identifiers(clinvar_record()),
            ["gene_trait_context"],
        )

        self.assertEqual(result["tier"], "unverified_search_hit")

    def test_short_protein_change_requires_gene_support(self) -> None:
        result = assess_retrieval_relevance(
            {"title": "R41G in an unrelated protein", "abstract": None},
            extract_identifiers(clinvar_record()),
            ["protein_change_exact"],
        )

        self.assertEqual(result["tier"], "unverified_search_hit")
        self.assertEqual(result["matched_exact_identifiers"], [])
        self.assertEqual(result["unsupported_variant_mentions"], ["R41G"])


class LinkedMembershipTest(unittest.TestCase):
    def test_indexes_linked_pmids(self) -> None:
        indexed = linked_pmids_by_variant(
            [
                {
                    "variant": {"variant_key": "1:100:A:G"},
                    "evidence": {"pmids": ["1", "2"]},
                }
            ]
        )

        self.assertEqual(indexed, {"1:100:A:G": {"1", "2"}})


if __name__ == "__main__":
    unittest.main()
