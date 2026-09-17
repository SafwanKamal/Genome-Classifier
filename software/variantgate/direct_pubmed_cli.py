from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from software.variantgate.evidence.direct_pubmed import (
    assess_retrieval_relevance,
    build_search_plans,
    extract_identifiers,
    linked_pmids_by_variant,
)
from software.variantgate.evidence.pubmed import PubMedClient
from software.variantgate.manifest import sha256_file
from software.variantgate.report_cli import (
    load_jsonl,
    validate_evidence_manifest,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        for value in values:
            output_file.write(json.dumps(value, sort_keys=True) + "\n")


def record_index(
    records: list[dict[str, object]],
    source_name: str,
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}

    for record in records:
        variant = record.get("variant")

        if not isinstance(variant, dict):
            raise ValueError(f"{source_name} record has no variant object")

        key = str(variant.get("variant_key", ""))

        if not key or key in indexed:
            raise ValueError(f"{source_name} requires unique variant keys")

        indexed[key] = record

    return indexed


def run_collect(args: argparse.Namespace) -> None:
    clinvar_path = args.clinvar_evidence.resolve()
    clinvar_manifest_path = (
        args.clinvar_manifest.resolve()
        if args.clinvar_manifest is not None
        else clinvar_path.parent / "run_manifest.json"
    )
    validate_evidence_manifest(clinvar_path, clinvar_manifest_path)
    clinvar_records = load_jsonl(clinvar_path)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be positive")

        clinvar_records = clinvar_records[: args.limit]

    linked_path: Path | None = None
    linked_manifest_path: Path | None = None
    linked_by_variant: dict[str, set[str]] = {}

    if args.linked_pubmed_evidence is not None:
        linked_path = args.linked_pubmed_evidence.resolve()
        linked_manifest_path = (
            args.linked_pubmed_manifest.resolve()
            if args.linked_pubmed_manifest is not None
            else linked_path.parent / "run_manifest.json"
        )
        validate_evidence_manifest(linked_path, linked_manifest_path)
        linked_records = load_jsonl(linked_path)
        linked_by_variant = linked_pmids_by_variant(linked_records)
        selected_keys = {
            str(record["variant"]["variant_key"])
            for record in clinvar_records
        }
        missing_keys = selected_keys - set(linked_by_variant)

        if missing_keys:
            raise ValueError(
                "Linked PubMed evidence is missing "
                f"{len(missing_keys)} selected variants"
            )

    client = PubMedClient(
        cache_directory=args.cache_dir,
        email=args.email,
        api_key_environment=args.api_key_environment,
        summary_batch_size=args.summary_batch_size,
        timeout_seconds=args.timeout,
        retry_number=args.retries,
        refresh=args.refresh,
    )
    work_items: list[dict[str, object]] = []
    all_pmids: list[str] = []

    for record in clinvar_records:
        identifiers = extract_identifiers(record)
        plans = build_search_plans(
            identifiers,
            maximum_query_number=args.max_queries_per_variant,
            include_context_query=not args.no_context_query,
        )
        query_records: list[dict[str, object]] = []
        strategies_by_pmid: dict[str, list[str]] = defaultdict(list)

        for plan in plans:
            result = client.search(
                plan.query,
                retmax=args.max_results_per_query,
            )
            pmids = [str(value) for value in result["pmids"]]
            query_records.append(
                {
                    **plan.to_dict(),
                    "result_count": int(result["count"]),
                    "returned_pmid_number": len(pmids),
                    "pmids": pmids,
                    "query_translation": result["query_translation"],
                    "retrieved_at": result["retrieved_at"],
                    "request_url": result["request_url"],
                    "cache_hit": result["cache_hit"],
                    "raw_response_sha256": result[
                        "raw_response_sha256"
                    ],
                }
            )

            for pmid in pmids:
                if plan.strategy not in strategies_by_pmid[pmid]:
                    strategies_by_pmid[pmid].append(plan.strategy)

                if pmid not in all_pmids:
                    all_pmids.append(pmid)

        work_items.append(
            {
                "clinvar_record": record,
                "identifiers": identifiers,
                "plans": plans,
                "query_records": query_records,
                "strategies_by_pmid": dict(strategies_by_pmid),
            }
        )
        completed = len(work_items)

        if (
            completed == 1
            or (
                args.progress_every > 0
                and completed % args.progress_every == 0
            )
            or completed == len(clinvar_records)
        ):
            print(
                f"Searched literature for {completed:,}/"
                f"{len(clinvar_records):,} variants"
            )

    articles = client.fetch_articles(all_pmids)
    abstracts = client.fetch_abstracts(all_pmids)
    evidence_records: list[dict[str, object]] = []

    for item in work_items:
        record = item["clinvar_record"]
        variant = record["variant"]
        variant_key = str(variant["variant_key"])
        linked_pmids = linked_by_variant.get(variant_key, set())
        article_values: list[dict[str, object]] = []

        for pmid, strategies in item["strategies_by_pmid"].items():
            article_record = articles[pmid]
            abstract_record = abstracts[pmid]
            combined = {
                **article_record["article"],
                **abstract_record["content"],
            }
            relevance = assess_retrieval_relevance(
                combined,
                item["identifiers"],
                strategies,
            )
            article_values.append(
                {
                    **combined,
                    "source_membership": (
                        "both" if pmid in linked_pmids else "direct_search"
                    ),
                    "retrieval_relevance": relevance,
                    "summary_provenance": {
                        "retrieved_at": article_record["retrieved_at"],
                        "request_url": article_record["request_url"],
                        "cache_hit": article_record["cache_hit"],
                        "raw_summary_sha256": article_record[
                            "raw_summary_sha256"
                        ],
                    },
                    "abstract_provenance": {
                        "retrieved_at": abstract_record["retrieved_at"],
                        "request_url": abstract_record["request_url"],
                        "cache_hit": abstract_record["cache_hit"],
                        "raw_record_sha256": abstract_record[
                            "raw_record_sha256"
                        ],
                    },
                }
            )

        article_values.sort(
            key=lambda value: (
                -int(value["retrieval_relevance"]["score"]),
                int(value["pmid"]),
            )
        )
        direct_pmids = [str(value["pmid"]) for value in article_values]
        overlap_number = sum(pmid in linked_pmids for pmid in direct_pmids)
        evidence_records.append(
            {
                "format_version": 1,
                "variant": variant,
                "provider": {
                    "name": "ncbi_pubmed_direct_search",
                    "database": "PubMed",
                    "search_api": "NCBI ESearch",
                    "summary_api": "NCBI ESummary",
                    "abstract_api": "NCBI EFetch",
                    "retrieved_at": utc_now(),
                },
                "identifier_audit": item["identifiers"],
                "search": {
                    "query_number": len(item["plans"]),
                    "queries": item["query_records"],
                },
                "evidence": {
                    "status": (
                        "found"
                        if article_values
                        else (
                            "no_direct_articles"
                            if item["plans"]
                            else "no_searchable_identifiers"
                        )
                    ),
                    "direct_article_number": len(article_values),
                    "direct_pmids": direct_pmids,
                    "also_clinvar_linked_number": overlap_number,
                    "direct_only_number": len(article_values) - overlap_number,
                    "articles": article_values,
                },
                "interpretation_boundary": (
                    "Direct retrieval and deterministic text matching only; "
                    "relevance scores are not evidence-strength or clinical "
                    "classification scores."
                ),
            }
        )

    tier_counts: Counter[str] = Counter(
        article["retrieval_relevance"]["tier"]
        for record in evidence_records
        for article in record["evidence"]["articles"]
    )
    direct_instances = sum(
        record["evidence"]["direct_article_number"]
        for record in evidence_records
    )
    overlap_instances = sum(
        record["evidence"]["also_clinvar_linked_number"]
        for record in evidence_records
    )
    search_cache_hits = sum(
        query["cache_hit"]
        for record in evidence_records
        for query in record["search"]["queries"]
    )
    summary = {
        "status": "pass",
        "provider": "ncbi_pubmed_direct_search",
        "variant_number": len(evidence_records),
        "searchable_variant_number": sum(
            record["search"]["query_number"] > 0
            for record in evidence_records
        ),
        "variants_with_direct_articles": sum(
            record["evidence"]["direct_article_number"] > 0
            for record in evidence_records
        ),
        "variants_without_direct_articles": sum(
            record["evidence"]["direct_article_number"] == 0
            for record in evidence_records
        ),
        "unique_direct_article_number": len(all_pmids),
        "direct_article_instance_number": direct_instances,
        "clinvar_link_overlap_instance_number": overlap_instances,
        "direct_only_article_instance_number": (
            direct_instances - overlap_instances
        ),
        "retrieval_relevance_tier_counts": dict(sorted(tier_counts.items())),
        "search_cache_hit_query_number": search_cache_hits,
        "article_cache_hit_number": sum(
            article["cache_hit"] for article in articles.values()
        ),
        "abstract_cache_hit_number": sum(
            abstract["cache_hit"] for abstract in abstracts.values()
        ),
        "network_request_attempt_number": client.request_attempt_number,
        "successful_network_request_number": client.successful_request_number,
        "interpretation_boundary": (
            "Direct literature discovery; not independent model validation "
            "and not clinical interpretation."
        ),
    }
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    evidence_path = output_directory / "direct_pubmed_evidence.jsonl"
    summary_path = output_directory / "summary.json"
    write_jsonl(evidence_path, evidence_records)
    write_json(summary_path, summary)
    inputs: dict[str, object] = {
        "clinvar_evidence": {
            "path": str(clinvar_path),
            "sha256": sha256_file(clinvar_path),
        },
        "clinvar_manifest": {
            "path": str(clinvar_manifest_path),
            "sha256": sha256_file(clinvar_manifest_path),
        },
    }

    if linked_path is not None and linked_manifest_path is not None:
        inputs["linked_pubmed_evidence"] = {
            "path": str(linked_path),
            "sha256": sha256_file(linked_path),
        }
        inputs["linked_pubmed_manifest"] = {
            "path": str(linked_manifest_path),
            "sha256": sha256_file(linked_manifest_path),
        }

    run_manifest = {
        "format_version": 1,
        "tool": "variantgate_direct_pubmed",
        "created_at": utc_now(),
        "inputs": inputs,
        "configuration": {
            "limit": args.limit,
            "max_queries_per_variant": args.max_queries_per_variant,
            "max_results_per_query": args.max_results_per_query,
            "include_context_query": not args.no_context_query,
            "refresh": args.refresh,
        },
        "provider": {
            "name": "ncbi_pubmed_direct_search",
            "api_key_used": client.api_key_used,
            "cache_directory": str(client.cache_directory),
            "request_attempt_number": client.request_attempt_number,
            "successful_request_number": client.successful_request_number,
        },
        "outputs": {
            "evidence": {
                "path": evidence_path.name,
                "sha256": sha256_file(evidence_path),
            },
            "summary": {
                "path": summary_path.name,
                "sha256": sha256_file(summary_path),
            },
        },
    }
    write_json(output_directory / "run_manifest.json", run_manifest)
    print(json.dumps({**summary, "output_directory": str(output_directory)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate-direct-pubmed",
        description="Discover PubMed literature independently of ClinVar links",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser(
        "collect",
        help="Run deterministic variant-specific PubMed searches",
    )
    collect_parser.add_argument("--clinvar-evidence", type=Path, required=True)
    collect_parser.add_argument("--clinvar-manifest", type=Path, default=None)
    collect_parser.add_argument(
        "--linked-pubmed-evidence",
        type=Path,
        default=None,
    )
    collect_parser.add_argument(
        "--linked-pubmed-manifest",
        type=Path,
        default=None,
    )
    collect_parser.add_argument("--output-dir", type=Path, required=True)
    collect_parser.add_argument("--limit", type=int, default=None)
    collect_parser.add_argument("--max-queries-per-variant", type=int, default=4)
    collect_parser.add_argument("--max-results-per-query", type=int, default=20)
    collect_parser.add_argument("--no-context-query", action="store_true")
    collect_parser.add_argument("--email", default=None)
    collect_parser.add_argument(
        "--api-key-environment",
        default="NCBI_API_KEY",
    )
    collect_parser.add_argument("--summary-batch-size", type=int, default=200)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--retries", type=int, default=4)
    collect_parser.add_argument("--refresh", action="store_true")
    collect_parser.add_argument("--progress-every", type=int, default=10)
    collect_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/variantgate/pubmed"),
    )
    collect_parser.set_defaults(function=run_collect)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
