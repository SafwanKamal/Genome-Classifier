from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from software.variantgate.backends import (
    ComparisonBackend,
    InferenceBackend,
    NumPyBackend,
    UARTBackend,
)
from software.variantgate.manifest import (
    ModelManifest,
    load_model_manifest,
    sha256_file,
)
from software.variantgate.schemas import ScoreResult, VariantRecord
from software.variantgate.routing import DEEP_REVIEW, load_routing_policy


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def automatic_run_directory(output_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return output_root / timestamp


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_records(
    input_path: Path,
    manifest: ModelManifest,
    split: str | None,
    limit: int | None,
) -> list[VariantRecord]:
    frame = pd.read_parquet(input_path)
    required_columns = {
        "variant_key",
        "gene",
        *manifest.feature_order,
    }

    if split is not None:
        required_columns.add("split")

    missing_columns = sorted(required_columns - set(frame.columns))

    if missing_columns:
        raise ValueError(
            "Input is missing columns: " + ", ".join(missing_columns)
        )

    if split is not None:
        frame = frame.loc[frame["split"].eq(split)].copy()

    if frame.empty:
        raise ValueError(
            "No input variants remain after applying the requested filters"
        )

    duplicate_number = int(frame["variant_key"].duplicated().sum())

    if duplicate_number:
        raise ValueError(
            f"Input contains {duplicate_number} duplicate variant keys"
        )

    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")

        frame = frame.head(limit).copy()

    feature_frame = frame.loc[
        :,
        list(manifest.feature_order),
    ].apply(pd.to_numeric, errors="raise")

    missing_value_number = int(feature_frame.isna().sum().sum())

    if missing_value_number:
        raise ValueError(
            f"Input contains {missing_value_number} missing feature values"
        )

    feature_values = feature_frame.to_numpy(dtype=np.float64)

    if not np.equal(feature_values, np.floor(feature_values)).all():
        raise ValueError("Input contains non-integer feature values")

    if (feature_values < -128).any() or (feature_values > 127).any():
        raise ValueError(
            "Input contains a feature outside signed INT8 range"
        )

    records: list[VariantRecord] = []

    for row_index in range(len(frame)):
        variant_key = str(frame.iloc[row_index]["variant_key"])
        gene_value = frame.iloc[row_index]["gene"]
        gene = None if pd.isna(gene_value) else str(gene_value)
        features = tuple(
            int(value) for value in feature_values[row_index]
        )

        records.append(
            VariantRecord(
                variant_key=variant_key,
                gene=gene,
                features=features,
            )
        )

    return records


def create_backend(
    args: argparse.Namespace,
    manifest: ModelManifest,
) -> InferenceBackend:
    numpy_backend = NumPyBackend(manifest)

    if args.backend == "numpy":
        return numpy_backend

    if not args.port:
        raise ValueError(
            f"--port is required for the {args.backend} backend"
        )

    uart_backend = UARTBackend(
        port=args.port,
        baud_rate=args.baud_rate,
        timeout=args.timeout,
    )

    if args.backend == "uart":
        return uart_backend

    if args.backend == "compare":
        return ComparisonBackend(
            candidate=uart_backend,
            reference=numpy_backend,
        )

    raise ValueError(f"Unsupported backend: {args.backend}")


def score_records(
    backend: InferenceBackend,
    records: list[VariantRecord],
    progress_every: int,
) -> list[ScoreResult]:
    results: list[ScoreResult] = []

    with backend:
        for record_index, record in enumerate(records, start=1):
            result = backend.score_one(record)
            results.append(result)

            if (
                record_index == 1
                or (
                    progress_every > 0
                    and record_index % progress_every == 0
                )
                or record_index == len(records)
            ):
                print(f"Scored {record_index:,}/{len(records):,}")

    return results


def run_triage(args: argparse.Namespace) -> None:
    input_path = args.input.resolve()
    manifest = load_model_manifest(args.manifest)
    records = load_records(
        input_path=input_path,
        manifest=manifest,
        split=args.split,
        limit=args.limit,
    )

    output_directory = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else automatic_run_directory(args.output_root.resolve())
    )

    output_directory.mkdir(parents=True, exist_ok=False)

    started_at = utc_now()
    start_ns = time.perf_counter_ns()
    backend = create_backend(args=args, manifest=manifest)
    results = score_records(
        backend=backend,
        records=records,
        progress_every=args.progress_every,
    )
    end_ns = time.perf_counter_ns()
    completed_at = utc_now()

    scores_frame = pd.DataFrame(result.to_dict() for result in results)

    routing_policy = None

    if args.routing_policy is not None:
        routing_policy = load_routing_policy(args.routing_policy)

        if routing_policy.model_manifest_sha256 != manifest.sha256:
            raise ValueError(
                "Routing policy and triage export manifest identify "
                "different models"
            )

        scores_frame["route"] = routing_policy.route_scores(
            scores_frame["score"].to_numpy()
        )
        scores_frame["deep_review_required"] = scores_frame["route"].eq(
            DEEP_REVIEW
        )

    scores_path = output_directory / "scores.parquet"
    scores_frame.to_parquet(scores_path, index=False)

    score_values = scores_frame["score"]
    prediction_counts = {
        str(int(prediction)): int(count)
        for prediction, count in scores_frame["prediction"].value_counts().items()
    }
    total_latency_ns = int(scores_frame["latency_ns"].sum())

    summary = {
        "status": "pass",
        "variant_number": len(results),
        "backend": backend.name,
        "architecture": manifest.architecture,
        "classification_rule": manifest.classification_rule,
        "score_min": int(score_values.min()),
        "score_max": int(score_values.max()),
        "prediction_counts": prediction_counts,
        "total_backend_latency_seconds": total_latency_ns / 1_000_000_000,
        "mean_backend_latency_us": float(scores_frame["latency_us"].mean()),
        "complete_run_time_seconds": (end_ns - start_ns) / 1_000_000_000,
        "bit_exact_comparison": args.backend == "compare",
        "bit_exact_mismatches": 0 if args.backend == "compare" else None,
    }

    if routing_policy is not None:
        route_counts = {
            str(route): int(count)
            for route, count in scores_frame["route"].value_counts().items()
        }
        summary["routing"] = {
            "policy_name": routing_policy.policy_name,
            "threshold": routing_policy.threshold,
            "route_counts": route_counts,
            "deep_review_fraction": (
                route_counts.get(DEEP_REVIEW, 0) / len(scores_frame)
            ),
        }

    summary_path = output_directory / "summary.json"
    write_json(summary_path, summary)

    run_manifest = {
        "format_version": 1,
        "tool": "variantgate",
        "started_at": started_at,
        "completed_at": completed_at,
        "input": {
            "path": str(input_path),
            "sha256": sha256_file(input_path),
            "split": args.split,
            "limit": args.limit,
            "variant_number": len(records),
        },
        "model": {
            "export_manifest_path": str(manifest.path),
            "export_manifest_sha256": manifest.sha256,
            "source_model_sha256": manifest.source_model_sha256,
            "architecture": manifest.architecture,
            "feature_order": list(manifest.feature_order),
            "qshift": manifest.qshift,
            "folded_output_bias": manifest.folded_output_bias,
            "original_validation_threshold": (
                manifest.original_validation_threshold
            ),
            "classification_rule": manifest.classification_rule,
        },
        "backend": {
            "name": backend.name,
            "serial_port": (
                args.port if args.backend in {"uart", "compare"} else None
            ),
            "baud_rate": (
                args.baud_rate
                if args.backend in {"uart", "compare"}
                else None
            ),
            "timeout_seconds": (
                args.timeout
                if args.backend in {"uart", "compare"}
                else None
            ),
        },
        "routing_policy": (
            {
                "path": str(routing_policy.path),
                "sha256": sha256_file(routing_policy.path),
                "policy_name": routing_policy.policy_name,
                "threshold": routing_policy.threshold,
                "selection_split": routing_policy.selection_split,
                "target_pathogenic_recall": (
                    routing_policy.target_pathogenic_recall
                ),
                "achieved_pathogenic_recall": (
                    routing_policy.achieved_pathogenic_recall
                ),
            }
            if routing_policy is not None
            else None
        ),
        "outputs": {
            "scores": {
                "path": scores_path.name,
                "sha256": sha256_file(scores_path),
            },
            "summary": {
                "path": summary_path.name,
                "sha256": sha256_file(summary_path),
            },
        },
    }

    run_manifest_path = output_directory / "run_manifest.json"
    write_json(run_manifest_path, run_manifest)

    print(
        json.dumps(
            {
                **summary,
                "output_directory": str(output_directory),
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate",
        description="FPGA-assisted genomic variant triage tool",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    triage_parser = subparsers.add_parser(
        "triage",
        help="Score a batch of genomic variants",
    )

    triage_parser.add_argument("--input", type=Path, required=True)
    triage_parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "genomic-dataset-pipeline/"
            "artifacts/model_v2_h8_seed_7/"
            "fpga_export/export_manifest.json"
        ),
    )
    triage_parser.add_argument(
        "--backend",
        choices=["numpy", "uart", "compare"],
        default="numpy",
    )
    triage_parser.add_argument("--port")
    triage_parser.add_argument("--baud-rate", type=int, default=115_200)
    triage_parser.add_argument("--timeout", type=float, default=2.0)
    triage_parser.add_argument("--split", default=None)
    triage_parser.add_argument("--limit", type=int, default=None)
    triage_parser.add_argument("--progress-every", type=int, default=100)
    triage_parser.add_argument(
        "--routing-policy",
        type=Path,
        default=None,
        help="Apply a frozen validation-derived routing policy",
    )
    triage_parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("runs"),
    )
    triage_parser.add_argument("--output-dir", type=Path, default=None)
    triage_parser.set_defaults(function=run_triage)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
