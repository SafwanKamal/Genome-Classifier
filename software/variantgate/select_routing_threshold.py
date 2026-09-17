from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from software.variantgate.manifest import load_model_manifest, sha256_file
from software.variantgate.routing import select_routing_threshold


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")

    return value


def validated_selection_frame(
    scores_path: Path,
    dataset_path: Path,
    split: str,
) -> pd.DataFrame:
    if split.lower() == "test":
        raise ValueError(
            "Routing-threshold selection on the test split is prohibited"
        )

    scores = pd.read_parquet(scores_path)
    dataset = pd.read_parquet(dataset_path)

    score_columns = {"variant_key", "score"}
    dataset_columns = {"variant_key", "split", "label"}
    missing_score_columns = sorted(score_columns - set(scores.columns))
    missing_dataset_columns = sorted(dataset_columns - set(dataset.columns))

    if missing_score_columns:
        raise ValueError(
            "Scores file is missing columns: "
            + ", ".join(missing_score_columns)
        )

    if missing_dataset_columns:
        raise ValueError(
            "Dataset is missing columns: "
            + ", ".join(missing_dataset_columns)
        )

    if scores["variant_key"].duplicated().any():
        raise ValueError("Scores file contains duplicate variant keys")

    selected_dataset = dataset.loc[
        dataset["split"].eq(split),
        ["variant_key", "label"],
    ].copy()

    if selected_dataset.empty:
        raise ValueError(f"Dataset contains no rows for split {split!r}")

    if selected_dataset["variant_key"].duplicated().any():
        raise ValueError(
            f"Dataset split {split!r} contains duplicate variant keys"
        )

    if len(scores) != len(selected_dataset):
        raise ValueError(
            "Threshold selection requires the complete split: "
            f"scores contain {len(scores):,} variants while split {split!r} "
            f"contains {len(selected_dataset):,}"
        )

    merged = selected_dataset.merge(
        scores[["variant_key", "score"]],
        on="variant_key",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    unmatched_number = int(merged["_merge"].ne("both").sum())

    if unmatched_number:
        raise ValueError(
            f"Scores and {split!r} split have {unmatched_number} unmatched keys"
        )

    return merged.drop(columns="_merge")


def validate_run_manifest(
    run_manifest_path: Path,
    split: str,
    model_manifest_sha256: str,
) -> dict[str, object]:
    run_manifest = load_json(run_manifest_path)
    input_section = run_manifest.get("input")
    model_section = run_manifest.get("model")

    if not isinstance(input_section, dict):
        raise ValueError("Run manifest has no valid input section")

    if not isinstance(model_section, dict):
        raise ValueError("Run manifest has no valid model section")

    if input_section.get("split") != split:
        raise ValueError(
            "Run-manifest split does not match the requested selection split"
        )

    if input_section.get("limit") is not None:
        raise ValueError(
            "Routing threshold cannot be selected from a limited triage run"
        )

    if model_section.get("export_manifest_sha256") != model_manifest_sha256:
        raise ValueError(
            "Run manifest and export manifest identify different models"
        )

    return run_manifest


def build_policy(args: argparse.Namespace) -> dict[str, object]:
    scores_path = args.scores.resolve()
    dataset_path = args.dataset.resolve()
    model_manifest = load_model_manifest(args.manifest)
    run_manifest_path = (
        args.run_manifest.resolve()
        if args.run_manifest is not None
        else scores_path.parent / "run_manifest.json"
    )

    run_manifest = validate_run_manifest(
        run_manifest_path=run_manifest_path,
        split=args.split,
        model_manifest_sha256=model_manifest.sha256,
    )
    selection_frame = validated_selection_frame(
        scores_path=scores_path,
        dataset_path=dataset_path,
        split=args.split,
    )
    statistics = select_routing_threshold(
        scores=selection_frame["score"].to_numpy(),
        labels=selection_frame["label"].to_numpy(),
        target_pathogenic_recall=args.target_recall,
    )

    return {
        "format_version": 1,
        "policy_name": "validation_pathogenic_recall_gate",
        "created_at": utc_now(),
        "purpose": (
            "Allocate deep evidence review while preserving a validation-set "
            "pathogenic-recall target. This policy does not change the model's "
            "score >= 0 classification rule."
        ),
        "rule": {
            "deep_review": "score >= threshold",
            "light_review": "score < threshold",
            "threshold": statistics.threshold,
        },
        "selection": {
            "split": args.split,
            **{
                key: value
                for key, value in statistics.to_dict().items()
                if key != "threshold"
            },
        },
        "model": {
            "export_manifest_path": str(model_manifest.path),
            "export_manifest_sha256": model_manifest.sha256,
            "source_model_sha256": model_manifest.source_model_sha256,
            "architecture": model_manifest.architecture,
            "classification_rule": model_manifest.classification_rule,
        },
        "inputs": {
            "scores": {
                "path": str(scores_path),
                "sha256": sha256_file(scores_path),
            },
            "dataset": {
                "path": str(dataset_path),
                "sha256": sha256_file(dataset_path),
            },
            "run_manifest": {
                "path": str(run_manifest_path),
                "sha256": sha256_file(run_manifest_path),
                "backend": run_manifest.get("backend", {}).get("name"),
            },
        },
        "selection_command": sys.argv,
        "locked_test_policy": (
            "Test results may be reported after this policy is frozen but must "
            "not be used to modify the threshold or routing design."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select a validation-only VariantGate routing threshold"
    )
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "genomic-dataset-pipeline/"
            "artifacts/model_v2_h8_seed_7/"
            "fpga_export/export_manifest.json"
        ),
    )
    parser.add_argument("--run-manifest", type=Path, default=None)
    parser.add_argument("--split", default="validation")
    parser.add_argument("--target-recall", type=float, default=0.995)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/checkpoint_v2/routing_policy.json"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    policy = build_policy(args)
    output_path = args.output.resolve()
    write_json(output_path, policy)
    print(json.dumps(policy, indent=2))
    print(f"Saved routing policy: {output_path}")


if __name__ == "__main__":
    main()
