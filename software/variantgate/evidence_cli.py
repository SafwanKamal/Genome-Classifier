from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from software.variantgate.evidence.clinvar import (
    ClinVarClient,
    normalize_summary,
    sha256_json,
)
from software.variantgate.evidence.identifiers import (
    parse_variation_id,
    resolve_variation_id_column,
)
from software.variantgate.manifest import sha256_file
from software.variantgate.routing import (
    DEEP_REVIEW,
    LIGHT_REVIEW,
    load_routing_policy,
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


def automatic_run_directory(output_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return output_root / timestamp


def prepare_requests(
    scores_path: Path,
    variants_path: Path,
    policy_path: Path,
    requested_id_column: str | None,
    route: str,
    limit: int | None,
) -> tuple[pd.DataFrame, str]:
    scores = pd.read_parquet(scores_path)
    variants = pd.read_parquet(variants_path)
    policy = load_routing_policy(policy_path)
    scores_run_manifest_path = scores_path.parent / "run_manifest.json"

    if not scores_run_manifest_path.exists():
        raise ValueError(
            "The scores directory has no run_manifest.json provenance record"
        )

    scores_run_manifest = json.loads(
        scores_run_manifest_path.read_text(encoding="utf-8")
    )
    recorded_policy = scores_run_manifest.get("routing_policy")

    if not isinstance(recorded_policy, dict):
        raise ValueError(
            "The scores run manifest does not identify a routing policy"
        )

    if recorded_policy.get("sha256") != sha256_file(policy_path):
        raise ValueError(
            "The scores run and supplied routing policy do not match"
        )

    recorded_scores = scores_run_manifest.get("outputs", {}).get("scores", {})

    if recorded_scores.get("sha256") != sha256_file(scores_path):
        raise ValueError(
            "The scores file does not match its run-manifest hash"
        )

    required_score_columns = {"variant_key", "gene", "score", "route"}
    missing_score_columns = sorted(required_score_columns - set(scores.columns))

    if missing_score_columns:
        raise ValueError(
            "Scores file is missing columns: "
            + ", ".join(missing_score_columns)
        )

    if "variant_key" not in variants.columns:
        raise ValueError("Variants file is missing variant_key")

    if scores["variant_key"].duplicated().any():
        raise ValueError("Scores file contains duplicate variant keys")

    if variants["variant_key"].duplicated().any():
        raise ValueError("Variants file contains duplicate variant keys")

    expected_routes = policy.route_scores(scores["score"].to_numpy())

    if not (scores["route"].astype(str).to_numpy() == expected_routes).all():
        raise ValueError(
            "Scores contain routes that do not match the supplied policy"
        )

    selected_scores = scores.loc[scores["route"].eq(route)].copy()

    if selected_scores.empty:
        raise ValueError(f"No variants use route {route!r}")

    id_column = resolve_variation_id_column(
        variants.columns,
        requested_id_column,
    )
    merge_columns = ["variant_key"]

    if id_column is not None:
        merge_columns.append(id_column)

    merged = selected_scores.merge(
        variants[merge_columns],
        on="variant_key",
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    if merged["_merge"].ne("both").any():
        missing_number = int(merged["_merge"].ne("both").sum())
        raise ValueError(
            f"Variants file is missing {missing_number} routed variant keys"
        )

    if id_column is not None:
        merged["clinvar_variation_id"] = merged[id_column].map(
            parse_variation_id
        )
        id_source = id_column
    else:
        merged["clinvar_variation_id"] = merged["variant_key"].map(
            parse_variation_id
        )
        id_source = "variant_key"

    unresolved_number = int(merged["clinvar_variation_id"].isna().sum())

    if unresolved_number:
        raise ValueError(
            f"Could not resolve a ClinVar Variation ID for "
            f"{unresolved_number:,} of {len(merged):,} routed variants. "
            "Specify --variation-id-column using a column containing "
            "Variation IDs or VCV accessions."
        )

    merged["clinvar_variation_id"] = merged[
        "clinvar_variation_id"
    ].astype("int64")
    merged = merged.drop(columns="_merge")

    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")

        merged = merged.head(limit).copy()

    return merged, id_source


def run_collect(args: argparse.Namespace) -> None:
    scores_path = args.scores.resolve()
    variants_path = args.variants.resolve()
    policy_path = args.routing_policy.resolve()
    requests, id_source = prepare_requests(
        scores_path=scores_path,
        variants_path=variants_path,
        policy_path=policy_path,
        requested_id_column=args.variation_id_column,
        route=args.route,
        limit=args.limit,
    )
    output_directory = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else automatic_run_directory(args.output_root.resolve())
    )
    output_directory.mkdir(parents=True, exist_ok=False)

    client = ClinVarClient(
        cache_directory=args.cache_dir,
        email=args.email,
        api_key_environment=args.api_key_environment,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout,
        retry_number=args.retries,
        refresh=args.refresh,
    )
    variation_ids = requests["clinvar_variation_id"].tolist()
    summaries = client.fetch_many(variation_ids)
    evidence_records: list[dict[str, object]] = []

    for record_index, row in requests.iterrows():
        variation_id = int(row["clinvar_variation_id"])
        summary = summaries[variation_id]
        evidence = normalize_summary(summary)
        evidence_records.append(
            {
                "format_version": 1,
                "variant": {
                    "variant_key": str(row["variant_key"]),
                    "gene": None if pd.isna(row["gene"]) else str(row["gene"]),
                    "score": int(row["score"]),
                    "route": str(row["route"]),
                    "clinvar_variation_id": variation_id,
                },
                "provider": {
                    "name": "ncbi_clinvar_esummary",
                    "database": "ClinVar",
                    "retrieved_at": summary.retrieved_at,
                    "request_url": summary.request_url,
                    "cache_hit": summary.cached,
                    "raw_summary_sha256": (
                        sha256_json(summary.raw_summary)
                        if summary.raw_summary is not None
                        else None
                    ),
                },
                "evidence": evidence,
            }
        )

        completed_number = len(evidence_records)

        if (
            completed_number == 1
            or (
                args.progress_every > 0
                and completed_number % args.progress_every == 0
            )
            or completed_number == len(requests)
        ):
            print(
                f"Collected {completed_number:,}/{len(requests):,} "
                "ClinVar summaries"
            )

    evidence_path = output_directory / "clinvar_evidence.jsonl"
    write_jsonl(evidence_path, evidence_records)
    found_number = sum(
        record["evidence"]["status"] == "found"
        for record in evidence_records
    )
    cache_hit_number = sum(
        bool(record["provider"]["cache_hit"])
        for record in evidence_records
    )
    summary = {
        "status": "pass",
        "provider": "ncbi_clinvar_esummary",
        "route": args.route,
        "requested_variant_number": len(evidence_records),
        "found_number": found_number,
        "not_found_number": len(evidence_records) - found_number,
        "cache_hit_record_number": cache_hit_number,
        "network_retrieved_record_number": (
            len(evidence_records) - cache_hit_number
        ),
        "network_request_attempt_number": client.request_attempt_number,
        "successful_network_request_number": (
            client.successful_request_number
        ),
        "variation_id_source": id_source,
    }
    summary_path = output_directory / "summary.json"
    write_json(summary_path, summary)
    run_manifest = {
        "format_version": 1,
        "tool": "variantgate_evidence",
        "created_at": utc_now(),
        "inputs": {
            "scores": {
                "path": str(scores_path),
                "sha256": sha256_file(scores_path),
            },
            "variants": {
                "path": str(variants_path),
                "sha256": sha256_file(variants_path),
            },
            "routing_policy": {
                "path": str(policy_path),
                "sha256": sha256_file(policy_path),
            },
        },
        "selection": {
            "route": args.route,
            "limit": args.limit,
            "variation_id_source": id_source,
        },
        "provider": {
            "name": "ncbi_clinvar_esummary",
            "api": "NCBI E-utilities ESummary",
            "batch_size": args.batch_size,
            "api_key_used": client.api_key_used,
            "cache_directory": str(client.cache_directory),
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
    run_manifest_path = output_directory / "run_manifest.json"
    write_json(run_manifest_path, run_manifest)
    print(json.dumps({**summary, "output_directory": str(output_directory)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate-evidence",
        description="Collect auditable evidence for routed variants",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser(
        "collect-clinvar",
        help="Collect ClinVar ESummary evidence",
    )
    collect_parser.add_argument("--scores", type=Path, required=True)
    collect_parser.add_argument("--variants", type=Path, required=True)
    collect_parser.add_argument(
        "--routing-policy",
        type=Path,
        required=True,
    )
    collect_parser.add_argument(
        "--variation-id-column",
        default=None,
    )
    collect_parser.add_argument(
        "--route",
        choices=[DEEP_REVIEW, LIGHT_REVIEW],
        default=DEEP_REVIEW,
    )
    collect_parser.add_argument("--limit", type=int, default=None)
    collect_parser.add_argument("--email", default=None)
    collect_parser.add_argument(
        "--api-key-environment",
        default="NCBI_API_KEY",
    )
    collect_parser.add_argument("--batch-size", type=int, default=100)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--retries", type=int, default=4)
    collect_parser.add_argument("--refresh", action="store_true")
    collect_parser.add_argument("--progress-every", type=int, default=100)
    collect_parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache/variantgate/clinvar"),
    )
    collect_parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs/evidence"),
    )
    collect_parser.add_argument("--output-dir", type=Path, default=None)
    collect_parser.set_defaults(function=run_collect)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
