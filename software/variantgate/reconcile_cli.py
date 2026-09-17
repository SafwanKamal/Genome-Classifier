from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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


def records_by_variant_key(
    records: list[dict[str, object]],
    source_name: str,
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}

    for record in records:
        variant = record.get("variant")

        if not isinstance(variant, dict):
            raise ValueError(f"{source_name} record has no variant object")

        variant_key = str(variant.get("variant_key", ""))

        if not variant_key:
            raise ValueError(f"{source_name} record has no variant_key")

        if variant_key in indexed:
            raise ValueError(
                f"{source_name} contains duplicate variant key {variant_key}"
            )

        indexed[variant_key] = record

    return indexed


def reconcile_record(
    clinvar_record: dict[str, object],
    pubmed_record: dict[str, object],
    direct_pubmed_record: dict[str, object] | None = None,
) -> dict[str, object]:
    clinvar_variant = clinvar_record["variant"]
    pubmed_variant = pubmed_record["variant"]

    for field in (
        "variant_key",
        "gene",
        "score",
        "route",
        "clinvar_variation_id",
    ):
        if clinvar_variant.get(field) != pubmed_variant.get(field):
            raise ValueError(
                f"Evidence sources disagree on variant field {field!r}"
            )

    clinvar_evidence = clinvar_record["evidence"]
    pubmed_evidence = pubmed_record["evidence"]
    issues: list[str] = []

    if clinvar_evidence.get("status") != "found":
        issues.append("clinvar_record_not_found")

    germline = clinvar_evidence.get("germline_classification") or {}

    if not germline.get("description"):
        issues.append("clinvar_germline_classification_missing")

    linked_article_number = int(
        pubmed_evidence.get("linked_article_number", 0)
    )

    if linked_article_number == 0:
        issues.append("no_clinvar_linked_pubmed_articles")

    direct_evidence: dict[str, object] | None = None
    direct_provider: dict[str, object] | None = None
    direct_article_number = 0
    direct_only_number = 0
    overlap_number = 0

    if direct_pubmed_record is not None:
        direct_variant = direct_pubmed_record["variant"]

        for field in (
            "variant_key",
            "gene",
            "score",
            "route",
            "clinvar_variation_id",
        ):
            if clinvar_variant.get(field) != direct_variant.get(field):
                raise ValueError(
                    "Direct PubMed evidence disagrees on variant field "
                    f"{field!r}"
                )

        direct_evidence = direct_pubmed_record["evidence"]
        direct_provider = direct_pubmed_record["provider"]
        direct_article_number = int(
            direct_evidence.get("direct_article_number", 0)
        )
        direct_only_number = int(direct_evidence.get("direct_only_number", 0))
        overlap_number = int(
            direct_evidence.get("also_clinvar_linked_number", 0)
        )

        if direct_article_number == 0:
            issues.append("no_direct_pubmed_articles")

    evidence_sources: dict[str, object] = {
        "clinvar": clinvar_evidence,
        "pubmed": pubmed_evidence,
    }
    provenance_sources: dict[str, object] = {
        "clinvar": clinvar_record["provider"],
        "pubmed": pubmed_record["provider"],
    }

    if direct_evidence is not None and direct_provider is not None:
        evidence_sources["direct_pubmed"] = direct_evidence
        provenance_sources["direct_pubmed"] = direct_provider

    return {
        "format_version": 1,
        "subject": {
            "variant_key": clinvar_variant["variant_key"],
            "gene": clinvar_variant.get("gene"),
            "clinvar_variation_id": clinvar_variant[
                "clinvar_variation_id"
            ],
        },
        "fpga_gate": {
            "score": int(clinvar_variant["score"]),
            "prediction": int(int(clinvar_variant["score"]) >= 0),
            "classification_rule": "score >= 0",
            "route": clinvar_variant["route"],
        },
        "evidence": {
            **evidence_sources,
        },
        "provenance": {
            **provenance_sources,
        },
        "verification": {
            "variant_identity_match": True,
            "provider_number": 3 if direct_pubmed_record is not None else 2,
            "linked_article_number": linked_article_number,
            "direct_article_number": direct_article_number,
            "direct_only_article_number": direct_only_number,
            "linked_direct_overlap_number": overlap_number,
            "issues": issues,
            "requires_human_review": True,
        },
        "interpretation_boundary": (
            "Deterministic evidence envelope only; no clinical conclusion "
            "and no LLM-generated factual claims."
        ),
    }


def run_reconcile(args: argparse.Namespace) -> None:
    clinvar_path = args.clinvar_evidence.resolve()
    pubmed_path = args.pubmed_evidence.resolve()
    clinvar_manifest_path = (
        args.clinvar_manifest.resolve()
        if args.clinvar_manifest is not None
        else clinvar_path.parent / "run_manifest.json"
    )
    pubmed_manifest_path = (
        args.pubmed_manifest.resolve()
        if args.pubmed_manifest is not None
        else pubmed_path.parent / "run_manifest.json"
    )
    validate_evidence_manifest(clinvar_path, clinvar_manifest_path)
    validate_evidence_manifest(pubmed_path, pubmed_manifest_path)
    clinvar_records = records_by_variant_key(
        load_jsonl(clinvar_path),
        "ClinVar evidence",
    )
    pubmed_records = records_by_variant_key(
        load_jsonl(pubmed_path),
        "PubMed evidence",
    )

    if set(clinvar_records) != set(pubmed_records):
        clinvar_only = sorted(set(clinvar_records) - set(pubmed_records))
        pubmed_only = sorted(set(pubmed_records) - set(clinvar_records))
        raise ValueError(
            "Evidence sources contain different variant sets: "
            f"ClinVar-only={len(clinvar_only)}, "
            f"PubMed-only={len(pubmed_only)}"
        )

    direct_path: Path | None = None
    direct_manifest_path: Path | None = None
    direct_records: dict[str, dict[str, object]] | None = None

    if args.direct_pubmed_evidence is not None:
        direct_path = args.direct_pubmed_evidence.resolve()
        direct_manifest_path = (
            args.direct_pubmed_manifest.resolve()
            if args.direct_pubmed_manifest is not None
            else direct_path.parent / "run_manifest.json"
        )
        validate_evidence_manifest(direct_path, direct_manifest_path)
        direct_records = records_by_variant_key(
            load_jsonl(direct_path),
            "Direct PubMed evidence",
        )

        if set(clinvar_records) != set(direct_records):
            clinvar_only = sorted(set(clinvar_records) - set(direct_records))
            direct_only = sorted(set(direct_records) - set(clinvar_records))
            raise ValueError(
                "Evidence sources contain different variant sets: "
                f"ClinVar-only={len(clinvar_only)}, "
                f"direct-PubMed-only={len(direct_only)}"
            )

    reconciled_records = [
        reconcile_record(
            clinvar_records[key],
            pubmed_records[key],
            direct_records[key] if direct_records is not None else None,
        )
        for key in clinvar_records
    ]
    issue_counts = Counter(
        issue
        for record in reconciled_records
        for issue in record["verification"]["issues"]
    )
    variants_with_literature = sum(
        int(record["verification"]["linked_article_number"]) > 0
        for record in reconciled_records
    )
    linked_article_instances = sum(
        int(record["verification"]["linked_article_number"])
        for record in reconciled_records
    )
    direct_article_instances = sum(
        int(record["verification"]["direct_article_number"])
        for record in reconciled_records
    )
    direct_only_article_instances = sum(
        int(record["verification"]["direct_only_article_number"])
        for record in reconciled_records
    )
    variants_with_direct_literature = sum(
        int(record["verification"]["direct_article_number"]) > 0
        for record in reconciled_records
    )
    summary = {
        "status": "pass",
        "variant_number": len(reconciled_records),
        "provider_number": 3 if direct_records is not None else 2,
        "variants_with_linked_literature": variants_with_literature,
        "variants_without_linked_literature": (
            len(reconciled_records) - variants_with_literature
        ),
        "linked_article_instance_number": linked_article_instances,
        "variants_with_direct_literature": variants_with_direct_literature,
        "variants_without_direct_literature": (
            len(reconciled_records) - variants_with_direct_literature
            if direct_records is not None
            else None
        ),
        "direct_article_instance_number": direct_article_instances,
        "direct_only_article_instance_number": direct_only_article_instances,
        "issue_counts": dict(sorted(issue_counts.items())),
        "requires_human_review": True,
        "interpretation_boundary": (
            "Evidence reconciliation only; not a clinical interpretation."
        ),
    }
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    evidence_path = output_directory / "reconciled_evidence.jsonl"
    summary_path = output_directory / "summary.json"
    write_jsonl(evidence_path, reconciled_records)
    write_json(summary_path, summary)
    run_manifest = {
        "format_version": 1,
        "tool": "variantgate_reconcile",
        "created_at": utc_now(),
        "inputs": {
            "clinvar_evidence": {
                "path": str(clinvar_path),
                "sha256": sha256_file(clinvar_path),
            },
            "clinvar_manifest": {
                "path": str(clinvar_manifest_path),
                "sha256": sha256_file(clinvar_manifest_path),
            },
            "pubmed_evidence": {
                "path": str(pubmed_path),
                "sha256": sha256_file(pubmed_path),
            },
            "pubmed_manifest": {
                "path": str(pubmed_manifest_path),
                "sha256": sha256_file(pubmed_manifest_path),
            },
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

    if direct_path is not None and direct_manifest_path is not None:
        run_manifest["inputs"]["direct_pubmed_evidence"] = {
            "path": str(direct_path),
            "sha256": sha256_file(direct_path),
        }
        run_manifest["inputs"]["direct_pubmed_manifest"] = {
            "path": str(direct_manifest_path),
            "sha256": sha256_file(direct_manifest_path),
        }
    manifest_path = output_directory / "run_manifest.json"
    write_json(manifest_path, run_manifest)
    print(json.dumps({**summary, "output_directory": str(output_directory)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate-reconcile",
        description="Deterministically reconcile VariantGate evidence sources",
    )
    parser.add_argument("--clinvar-evidence", type=Path, required=True)
    parser.add_argument("--clinvar-manifest", type=Path, default=None)
    parser.add_argument("--pubmed-evidence", type=Path, required=True)
    parser.add_argument("--pubmed-manifest", type=Path, default=None)
    parser.add_argument("--direct-pubmed-evidence", type=Path, default=None)
    parser.add_argument("--direct-pubmed-manifest", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_reconcile(args)


if __name__ == "__main__":
    main()
