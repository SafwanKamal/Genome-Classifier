from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .build_features import load_contract
from .common import read_table, require_columns


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(frame: pd.DataFrame, feature_ids: list[str]) -> dict[str, Any]:
    require_columns(frame, ["variant_key", "gene", "label", "split", *feature_ids], "model dataset")
    errors: list[str] = []

    if frame["variant_key"].duplicated().any():
        errors.append("variant_key is not unique")
    if not set(frame["label"].dropna().unique()).issubset({0, 1}):
        errors.append("label contains values outside {0,1}")
    if set(frame["split"].dropna().unique()) != {"train", "validation", "test"}:
        errors.append("split must contain train, validation, and test")
    if frame[feature_ids].isna().any().any():
        errors.append("model features contain missing values")
    if not all(pd.api.types.is_integer_dtype(frame[column]) for column in feature_ids):
        errors.append("one or more model features are not integer-valued")
    if ((frame[feature_ids] < -127) | (frame[feature_ids] > 127)).any().any():
        errors.append("one or more model features fall outside [-127,127]")

    genes_by_split = {
        split: set(group["gene"].dropna().astype(str))
        for split, group in frame.groupby("split")
    }
    for left, right in [("train", "validation"), ("train", "test"), ("validation", "test")]:
        overlap = genes_by_split.get(left, set()) & genes_by_split.get(right, set())
        if overlap:
            errors.append(f"gene leakage between {left} and {right}: {sorted(overlap)[:5]}")

    split_summary = {}
    for split, group in frame.groupby("split"):
        split_summary[split] = {
            "variants": int(len(group)),
            "genes": int(group["gene"].nunique()),
            "positive": int(group["label"].sum()),
            "negative": int((group["label"] == 0).sum()),
            "positive_fraction": float(group["label"].mean()),
        }
        if group["label"].nunique() < 2:
            errors.append(f"{split} split contains only one class")

    report = {
        "status": "pass" if not errors else "fail",
        "rows": int(len(frame)),
        "unique_genes": int(frame["gene"].nunique()),
        "feature_order": feature_ids,
        "splits": split_summary,
        "errors": errors,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an INT8 model dataset and gene-separated splits.")
    parser.add_argument("--input", type=Path, default=Path("data/processed/variants_model_int8.parquet"))
    parser.add_argument("--contract", type=Path, default=Path("config/features.yaml"))
    parser.add_argument("--report", type=Path, default=Path("data/processed/validation_report.json"))
    args = parser.parse_args()

    contract = load_contract(args.contract)
    feature_ids = [feature["id"] for feature in contract["features"]]
    frame = read_table(args.input)
    report = validate(frame, feature_ids)
    report["input_sha256"] = sha256(args.input)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
