from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from .common import META_COLUMNS, read_table, require_columns, write_table


def load_contract(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    features = contract.get("features", [])
    indices = [feature["index"] for feature in features]
    if indices != list(range(16)):
        raise ValueError(f"Feature indices must be exactly 0..15, found {indices}")
    return contract


def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").astype(float)


def fit_feature(
    train: pd.DataFrame,
    feature: dict[str, Any],
) -> dict[str, float]:
    transform = feature["transform"]
    parameters: dict[str, float] = {}
    if transform in {"robust_percentile", "reverse_robust_percentile"}:
        values = numeric(train, feature["raw_columns"][0]).dropna()
        if values.empty:
            raise ValueError(f"Training data has no values for {feature['id']}")
        lower = float(values.quantile(float(feature["lower_percentile"])))
        upper = float(values.quantile(float(feature["upper_percentile"])))
        if upper <= lower:
            upper = lower + 1.0
        parameters.update(lower=lower, upper=upper)
    transformed = transform_feature(train, feature, parameters)
    if feature.get("missing") == "train_median":
        observed = transformed.dropna()
        if observed.empty:
            raise ValueError(f"Training data cannot fit an imputation value for {feature['id']}")
        parameters["imputation"] = float(observed.median())
    return parameters


def transform_feature(
    frame: pd.DataFrame,
    feature: dict[str, Any],
    parameters: dict[str, float],
) -> pd.Series:
    columns = feature["raw_columns"]
    transform = feature["transform"]

    if transform == "row_max":
        value = frame[columns].apply(pd.to_numeric, errors="coerce").max(axis=1, skipna=True)
        value[frame[columns].isna().all(axis=1)] = np.nan
        result = value
    elif transform == "domain_ternary":
        raw = frame[columns[0]]
        text = raw.astype("string").str.strip().str.lower()
        result = pd.Series(np.nan, index=frame.index, dtype=float)
        result[text.isin(["1", "true", "yes", "overlap", "hit"])] = 1.0
        result[text.isin(["0", "false", "no", "no_overlap", "none"])] = 0.0
        result = result.fillna(0.5)
    else:
        value = numeric(frame, columns[0])
        if transform == "identity":
            result = value
        elif transform == "reverse_identity":
            result = 1.0 - value
        elif transform == "clip_scale":
            result = value / float(feature["denominator"])
        elif transform == "log_rarity":
            floor = float(feature["floor"])
            denominator = float(feature["denominator"])
            valid = value.where(value >= 0)
            result = -np.log10(valid.clip(lower=floor)) / denominator
        elif transform == "reverse_range":
            minimum = float(feature["minimum"])
            maximum = float(feature["maximum"])
            result = (maximum - value) / (maximum - minimum)
        elif transform in {"robust_percentile", "reverse_robust_percentile"}:
            lower = parameters["lower"]
            upper = parameters["upper"]
            result = (value - lower) / (upper - lower)
            if transform == "reverse_robust_percentile":
                result = 1.0 - result
        else:
            raise ValueError(f"Unsupported transform: {transform}")
    return result.clip(lower=0.0, upper=1.0)


def build_feature_tables(
    annotated: pd.DataFrame,
    splits: pd.DataFrame,
    contract: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], pd.DataFrame]:
    require_columns(annotated, ["variant_key", "gene", "label"], "annotated variants")
    require_columns(splits, ["variant_key", "split"], "split assignments")
    required_raw = sorted({column for feature in contract["features"] for column in feature["raw_columns"]})
    require_columns(annotated, required_raw, "annotated variants")

    split_map = splits[["variant_key", "split"]].drop_duplicates()
    data = annotated.merge(split_map, on="variant_key", how="inner", validate="one_to_one")
    if len(data) != len(annotated):
        raise ValueError("Every annotated variant must have exactly one split assignment.")
    train = data[data["split"] == "train"]
    if train.empty:
        raise ValueError("Training split is empty.")

    float_features = pd.DataFrame(index=data.index)
    missing_audit = data[[column for column in META_COLUMNS if column in data.columns] + ["split"]].copy()
    preprocessing: dict[str, Any] = {
        "contract_version": contract["contract_version"],
        "assembly": contract["assembly"],
        "fit_split": "train",
        "encoding": contract["encoding"],
        "features": [],
    }

    for feature in contract["features"]:
        feature_id = feature["id"]
        parameters = fit_feature(train, feature)
        transformed = transform_feature(data, feature, parameters)
        raw_columns = feature["raw_columns"]
        if feature["transform"] == "domain_ternary":
            text = data[raw_columns[0]].astype("string").str.strip().str.lower()
            known = text.isin(
                ["1", "true", "yes", "overlap", "hit", "0", "false", "no", "no_overlap", "none"]
            )
            raw_missing = ~known
        else:
            numeric_raw = data[raw_columns].apply(pd.to_numeric, errors="coerce")
            raw_missing = numeric_raw.isna().all(axis=1)
        missing_audit[f"{feature_id}__missing"] = raw_missing
        if feature.get("missing") == "train_median":
            transformed = transformed.fillna(parameters["imputation"])
        elif transformed.isna().any():
            raise ValueError(f"Unresolved missing values remain in {feature_id}")
        float_features[feature_id] = transformed.astype("float32")
        preprocessing["features"].append(
            {
                "index": feature["index"],
                "id": feature_id,
                "raw_columns": feature["raw_columns"],
                "transform": feature["transform"],
                "parameters": parameters,
            }
        )

    metadata_columns = [column for column in META_COLUMNS if column in data.columns] + ["split"]
    float_output = pd.concat([data[metadata_columns].reset_index(drop=True), float_features.reset_index(drop=True)], axis=1)
    int8_values = np.clip(np.rint(254.0 * float_features - 127.0), -127, 127).astype(np.int8)
    int8_output = pd.concat([data[metadata_columns].reset_index(drop=True), int8_values.reset_index(drop=True)], axis=1)
    return float_output, int8_output, preprocessing, missing_audit.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit preprocessing on train genes and create float/INT8 model tables.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=Path("config/features.yaml"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    annotated = read_table(args.input)
    splits = read_table(args.splits)
    contract = load_contract(args.contract)
    float_frame, int8_frame, preprocessing, audit = build_feature_tables(annotated, splits, contract)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_table(float_frame, args.output_dir / "variants_model_float.parquet")
    write_table(int8_frame, args.output_dir / "variants_model_int8.parquet")
    write_table(int8_frame, args.output_dir / "variants_model.csv")
    write_table(audit, args.output_dir / "missingness_audit.parquet")
    (args.output_dir / "preprocessing.json").write_text(
        json.dumps(preprocessing, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Saved {len(float_frame):,} rows with 16 ordered model features to {args.output_dir}")


if __name__ == "__main__":
    main()
