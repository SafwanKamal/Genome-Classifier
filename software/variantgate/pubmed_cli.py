from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

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


def run_collect(args: argparse.Namespace) -> None:
    clinvar_evidence_path = args.clinvar_evidence.resolve()
    clinvar_manifest_path = (
        args.clinvar_manifest.resolve()
        if args.clinvar_manifest is not None
        else clinvar_evidence_path.parent / "run_manifest.json"
    )
    validate_evidence_manifest(
        evidence_path=clinvar_evidence_path,
        evidence_manifest_path=clinvar_manifest_path,
    )
    clinvar_records = load_jsonl(clinvar_evidence_path)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be positive")

        clinvar_records = clinvar_records[: args.limit]

    variation_ids: list[int] = []

    for record in clinvar_records:
        variant = record.get("variant")

        if not isinstance(variant, dict):
            raise ValueError("ClinVar evidence record has no variant object")

        variation_id = int(variant["clinvar_variation_id"])

        if variation_id <= 0:
            raise ValueError("ClinVar Variation IDs must be positive")

        variation_ids.append(variation_id)

    client = PubMedClient(
        cache_directory=args.cache_dir,
        email=args.email,
        api_key_environment=args.api_key_environment,
        link_batch_size=args.link_batch_size,
        summary_batch_size=args.summary_batch_size,
        timeout_seconds=args.timeout,
        retry_number=args.retries,
        refresh=args.refresh,
    )
    links = client.fetch_links(variation_ids)
    unique_pmids = list(
        dict.fromkeys(
            pmid
            for variation_id in variation_ids
            for pmid in links[variation_id]["pmids"]
        )
    )
    articles = client.fetch_articles(unique_pmids)
    evidence_records: list[dict[str, object]] = []

    for clinvar_record, variation_id in zip(
        clinvar_records,
        variation_ids,
        strict=True,
    ):
        link_record = links[variation_id]
        pmids = [str(value) for value in link_record["pmids"]]
        article_records = [articles[pmid] for pmid in pmids]
        evidence_records.append(
            {
                "format_version": 1,
                "variant": clinvar_record["variant"],
                "provider": {
                    "name": "ncbi_clinvar_pubmed",
                    "database": "PubMed",
                    "retrieved_at": link_record["retrieved_at"],
                    "link_request_url": link_record["request_url"],
                    "link_cache_hit": link_record["cache_hit"],
                    "link_raw_response_sha256": link_record[
                        "raw_response_sha256"
                    ],
                },
                "evidence": {
                    "status": "found" if pmids else "no_linked_articles",
                    "clinvar_variation_id": variation_id,
                    "linked_article_number": len(pmids),
                    "pmids": pmids,
                    "articles": [
                        {
                            **article_record["article"],
                            "retrieved_at": article_record["retrieved_at"],
                            "request_url": article_record["request_url"],
                            "cache_hit": article_record["cache_hit"],
                            "raw_summary_sha256": article_record[
                                "raw_summary_sha256"
                            ],
                        }
                        for article_record in article_records
                    ],
                },
            }
        )
        completed_number = len(evidence_records)

        if (
            completed_number == 1
            or (
                args.progress_every > 0
                and completed_number % args.progress_every == 0
            )
            or completed_number == len(clinvar_records)
        ):
            print(
                f"Linked literature for {completed_number:,}/"
                f"{len(clinvar_records):,} variants"
            )

    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    evidence_path = output_directory / "pubmed_evidence.jsonl"
    write_jsonl(evidence_path, evidence_records)
    variants_with_articles = sum(
        bool(record["evidence"]["pmids"])
        for record in evidence_records
    )
    linked_article_instances = sum(
        int(record["evidence"]["linked_article_number"])
        for record in evidence_records
    )
    link_cache_hits = sum(
        bool(record["provider"]["link_cache_hit"])
        for record in evidence_records
    )
    article_cache_hits = sum(
        bool(article["cache_hit"])
        for article in articles.values()
    )
    summary = {
        "status": "pass",
        "provider": "ncbi_clinvar_pubmed",
        "variant_number": len(evidence_records),
        "variants_with_linked_articles": variants_with_articles,
        "variants_without_linked_articles": (
            len(evidence_records) - variants_with_articles
        ),
        "unique_linked_article_number": len(unique_pmids),
        "linked_article_instance_number": linked_article_instances,
        "link_cache_hit_record_number": link_cache_hits,
        "article_cache_hit_number": article_cache_hits,
        "network_request_attempt_number": client.request_attempt_number,
        "successful_network_request_number": (
            client.successful_request_number
        ),
    }
    summary_path = output_directory / "summary.json"
    write_json(summary_path, summary)
    run_manifest = {
        "format_version": 1,
        "tool": "variantgate_pubmed_evidence",
        "created_at": utc_now(),
        "input": {
            "clinvar_evidence": {
                "path": str(clinvar_evidence_path),
                "sha256": sha256_file(clinvar_evidence_path),
            },
            "clinvar_manifest": {
                "path": str(clinvar_manifest_path),
                "sha256": sha256_file(clinvar_manifest_path),
            },
        },
        "provider": {
            "name": "ncbi_clinvar_pubmed",
            "link_api": "NCBI ELink ClinVar to PubMed",
            "summary_api": "NCBI PubMed ESummary",
            "api_key_used": client.api_key_used,
            "cache_directory": str(client.cache_directory),
            "link_batch_size": args.link_batch_size,
            "summary_batch_size": args.summary_batch_size,
            "refresh": args.refresh,
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
    manifest_path = output_directory / "run_manifest.json"
    write_json(manifest_path, run_manifest)
    print(json.dumps({**summary, "output_directory": str(output_directory)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate-pubmed",
        description="Collect PubMed metadata linked from ClinVar records",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser(
        "collect",
        help="Link ClinVar variants to PubMed and fetch article metadata",
    )
    collect_parser.add_argument("--clinvar-evidence", type=Path, required=True)
    collect_parser.add_argument("--clinvar-manifest", type=Path, default=None)
    collect_parser.add_argument("--output-dir", type=Path, required=True)
    collect_parser.add_argument("--limit", type=int, default=None)
    collect_parser.add_argument("--email", default=None)
    collect_parser.add_argument(
        "--api-key-environment",
        default="NCBI_API_KEY",
    )
    collect_parser.add_argument("--link-batch-size", type=int, default=100)
    collect_parser.add_argument("--summary-batch-size", type=int, default=200)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--retries", type=int, default=4)
    collect_parser.add_argument("--refresh", action="store_true")
    collect_parser.add_argument("--progress-every", type=int, default=100)
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
