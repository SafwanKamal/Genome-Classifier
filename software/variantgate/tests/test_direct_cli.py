from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from software.variantgate.direct_pubmed_cli import run_collect
from software.variantgate.manifest import sha256_file
from software.variantgate.reconcile_cli import run_reconcile


VARIANT = {
    "variant_key": "1:100:A:G",
    "gene": "GENE1",
    "score": 25,
    "route": "deep_review",
    "clinvar_variation_id": 10,
}


class FakePubMedClient:
    def __init__(self, cache_directory: Path, **_: object) -> None:
        self.cache_directory = cache_directory.resolve()
        self.api_key_used = False
        self.request_attempt_number = 3
        self.successful_request_number = 3

    def search(self, query: str, retmax: int) -> dict[str, object]:
        return {
            "count": 1,
            "pmids": ["123"],
            "query_translation": query,
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "request_url": "https://example.invalid/search",
            "cache_hit": False,
            "raw_response_sha256": "search-hash",
        }

    def fetch_articles(
        self,
        pmids: list[str],
    ) -> dict[str, dict[str, object]]:
        return {
            pmid: {
                "article": {
                    "status": "found",
                    "pmid": pmid,
                    "title": "Functional analysis of GENE1 rs123",
                    "authors": [],
                },
                "retrieved_at": "2026-01-01T00:00:00+00:00",
                "request_url": "https://example.invalid/summary",
                "cache_hit": False,
                "raw_summary_sha256": "summary-hash",
            }
            for pmid in pmids
        }

    def fetch_abstracts(
        self,
        pmids: list[str],
    ) -> dict[str, dict[str, object]]:
        return {
            pmid: {
                "content": {
                    "status": "found",
                    "pmid": pmid,
                    "abstract": "A patient cohort and activity assay.",
                    "abstract_sections": [],
                    "mesh_terms": [],
                    "keywords": ["missense"],
                },
                "retrieved_at": "2026-01-01T00:00:00+00:00",
                "request_url": "https://example.invalid/abstract",
                "cache_hit": False,
                "raw_record_sha256": "abstract-hash",
            }
            for pmid in pmids
        }


def write_evidence_run(
    directory: Path,
    filename: str,
    record: dict[str, object],
) -> Path:
    directory.mkdir()
    evidence_path = directory / filename
    evidence_path.write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "run_manifest.json").write_text(
        json.dumps(
            {
                "outputs": {
                    "evidence": {"sha256": sha256_file(evidence_path)}
                }
            }
        ),
        encoding="utf-8",
    )
    return evidence_path


class DirectCliTest(unittest.TestCase):
    def test_direct_search_and_three_source_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            clinvar_path = write_evidence_run(
                root / "clinvar",
                "clinvar_evidence.jsonl",
                {
                    "variant": VARIANT,
                    "provider": {"name": "ncbi_clinvar_esummary"},
                    "evidence": {
                        "status": "found",
                        "title": "GENE1 c.123A>G rs123",
                        "protein_change": "R41G",
                        "canonical_spdi": ["NC_000001.11:99:A:G"],
                        "database_cross_references": [
                            {"database": "dbSNP", "identifier": "rs123"}
                        ],
                        "germline_classification": {
                            "description": "Pathogenic",
                            "traits": [{"name": "Example syndrome"}],
                        },
                    },
                },
            )
            linked_path = write_evidence_run(
                root / "linked",
                "pubmed_evidence.jsonl",
                {
                    "variant": VARIANT,
                    "provider": {"name": "ncbi_clinvar_pubmed"},
                    "evidence": {
                        "status": "found",
                        "linked_article_number": 1,
                        "pmids": ["123"],
                        "articles": [{"pmid": "123"}],
                    },
                },
            )
            direct_output = root / "direct"
            direct_args = argparse.Namespace(
                clinvar_evidence=clinvar_path,
                clinvar_manifest=None,
                linked_pubmed_evidence=linked_path,
                linked_pubmed_manifest=None,
                output_dir=direct_output,
                limit=None,
                max_queries_per_variant=2,
                max_results_per_query=5,
                no_context_query=False,
                email=None,
                api_key_environment="NCBI_API_KEY",
                summary_batch_size=100,
                timeout=30.0,
                retries=0,
                refresh=False,
                progress_every=0,
                cache_dir=root / "cache",
            )

            with patch(
                "software.variantgate.direct_pubmed_cli.PubMedClient",
                FakePubMedClient,
            ):
                run_collect(direct_args)

            direct_summary = json.loads(
                (direct_output / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(direct_summary["variant_number"], 1)
            self.assertEqual(
                direct_summary["clinvar_link_overlap_instance_number"],
                1,
            )

            reconcile_output = root / "reconciled"
            run_reconcile(
                argparse.Namespace(
                    clinvar_evidence=clinvar_path,
                    clinvar_manifest=None,
                    pubmed_evidence=linked_path,
                    pubmed_manifest=None,
                    direct_pubmed_evidence=(
                        direct_output / "direct_pubmed_evidence.jsonl"
                    ),
                    direct_pubmed_manifest=None,
                    output_dir=reconcile_output,
                )
            )
            reconcile_summary = json.loads(
                (reconcile_output / "summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(reconcile_summary["provider_number"], 3)
            self.assertEqual(
                reconcile_summary["direct_article_instance_number"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
