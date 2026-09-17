from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from software.variantgate.evidence.direct_pubmed import (
    build_search_plans,
    extract_identifiers,
)
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


def run_audit(args: argparse.Namespace) -> None:
    evidence_path = args.clinvar_evidence.resolve()
    manifest_path = (
        args.clinvar_manifest.resolve()
        if args.clinvar_manifest is not None
        else evidence_path.parent / "run_manifest.json"
    )
    validate_evidence_manifest(evidence_path, manifest_path)
    records = load_jsonl(evidence_path)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be positive")

        records = records[: args.limit]

    audit_records: list[dict[str, object]] = []
    strategy_counts: Counter[str] = Counter()

    for record in records:
        identifiers = extract_identifiers(record)
        plans = build_search_plans(
            identifiers,
            maximum_query_number=args.max_queries_per_variant,
            include_context_query=not args.no_context_query,
        )
        strategy_counts.update(plan.strategy for plan in plans)
        audit_records.append(
            {
                "format_version": 1,
                "variant": record["variant"],
                "identifiers": identifiers,
                "search_plan": [plan.to_dict() for plan in plans],
                "searchable": bool(plans),
            }
        )

    searchable_number = sum(record["searchable"] for record in audit_records)
    summary = {
        "status": "pass",
        "variant_number": len(audit_records),
        "searchable_variant_number": searchable_number,
        "unsearchable_variant_number": len(audit_records) - searchable_number,
        "variants_with_exact_identifier": sum(
            bool(record["identifiers"]["exact_identifier_number"])
            for record in audit_records
        ),
        "variants_with_rsid": sum(
            bool(record["identifiers"]["rsids"])
            for record in audit_records
        ),
        "variants_with_hgvs": sum(
            bool(record["identifiers"]["hgvs"])
            for record in audit_records
        ),
        "variants_with_protein_change": sum(
            bool(record["identifiers"]["protein_changes"])
            for record in audit_records
        ),
        "variants_with_canonical_spdi": sum(
            bool(record["identifiers"]["canonical_spdi"])
            for record in audit_records
        ),
        "variants_with_known_assembly": sum(
            bool(record["identifiers"]["assembly_known"])
            for record in audit_records
        ),
        "coordinate_hgvs_generated_number": 0,
        "search_strategy_counts": dict(sorted(strategy_counts.items())),
        "interpretation": (
            "Identifier availability audit only. Coordinates are not converted "
            "to HGVS without an explicit assembly and transcript."
        ),
    }
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    audit_path = output_directory / "identifier_audit.jsonl"
    summary_path = output_directory / "summary.json"
    write_jsonl(audit_path, audit_records)
    write_json(summary_path, summary)
    run_manifest = {
        "format_version": 1,
        "tool": "variantgate_identifier_audit",
        "created_at": utc_now(),
        "input": {
            "clinvar_evidence": {
                "path": str(evidence_path),
                "sha256": sha256_file(evidence_path),
            },
            "clinvar_manifest": {
                "path": str(manifest_path),
                "sha256": sha256_file(manifest_path),
            },
        },
        "configuration": {
            "limit": args.limit,
            "max_queries_per_variant": args.max_queries_per_variant,
            "include_context_query": not args.no_context_query,
        },
        "outputs": {
            "audit": {
                "path": audit_path.name,
                "sha256": sha256_file(audit_path),
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
        prog="variantgate-identifier-audit",
        description="Audit identifiers available for direct literature search",
    )
    parser.add_argument("--clinvar-evidence", type=Path, required=True)
    parser.add_argument("--clinvar-manifest", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-queries-per-variant", type=int, default=4)
    parser.add_argument("--no-context-query", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_audit(args)


if __name__ == "__main__":
    main()
