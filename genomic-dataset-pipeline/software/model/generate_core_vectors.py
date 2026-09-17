from __future__ import annotations

import argparse
import json
import string
from pathlib import Path

import pandas as pd


FEATURE_NUMBER = 16
FEATURE_WIDTH = 8
SCORE_WIDTH = 32
VECTOR_WIDTH = (
    FEATURE_NUMBER * FEATURE_WIDTH
    + SCORE_WIDTH
)
VECTOR_HEX_DIGITS = VECTOR_WIDTH // 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate packed V2 RTL core test vectors "
            "from the locked test predictions."
        )
    )

    parser.add_argument(
        "--predictions",
        type=Path,
        default=Path(
            "artifacts/model_v2_h8_seed_7/"
            "test_predictions.parquet"
        ),
    )

    parser.add_argument(
        "--data",
        type=Path,
        default=Path(
            "data/processed/"
            "variants_model_int8.parquet"
        ),
    )

    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "artifacts/model_v2_h8_seed_7/"
            "fpga_export/export_manifest.json"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "../simulation/"
            "model_v2_h8_core_vectors.mem"
        ),
    )

    parser.add_argument(
        "--test-number",
        type=int,
        default=1000,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=7,
    )

    return parser.parse_args()


def signed_from_bits(
    value: int,
    width: int,
) -> int:
    sign_bit = 1 << (width - 1)
    full_range = 1 << width

    if value & sign_bit:
        return value - full_range

    return value


def pack_vector(
    features: list[int],
    score: int,
) -> tuple[int, str]:
    if len(features) != FEATURE_NUMBER:
        raise ValueError(
            f"Expected {FEATURE_NUMBER} features, "
            f"received {len(features)}"
        )

    packed_value = 0

    for feature_index, feature_value in enumerate(
        features
    ):
        if not -128 <= feature_value <= 127:
            raise ValueError(
                f"Feature {feature_index} is outside "
                f"signed INT8 range: {feature_value}"
            )

        packed_value = (
            packed_value << FEATURE_WIDTH
        ) | (feature_value & 0xFF)

    if not -(1 << 31) <= score <= (1 << 31) - 1:
        raise ValueError(
            "Score is outside signed INT32 range: "
            f"{score}"
        )

    packed_value = (
        packed_value << SCORE_WIDTH
    ) | (score & 0xFFFFFFFF)

    packed_hex = (
        f"{packed_value:0{VECTOR_HEX_DIGITS}X}"
    )

    return packed_value, packed_hex


def validate_packed_vector(
    packed_value: int,
    packed_hex: str,
    expected_features: list[int],
    expected_score: int,
) -> None:
    if len(packed_hex) != VECTOR_HEX_DIGITS:
        raise ValueError(
            "Packed vector has incorrect length: "
            f"{len(packed_hex)} hexadecimal characters"
        )

    valid_characters = set(string.hexdigits)

    if any(
        character not in valid_characters
        for character in packed_hex
    ):
        raise ValueError(
            "Packed vector contains a non-hexadecimal "
            f"character: {packed_hex}"
        )

    decoded_features: list[int] = []

    for feature_index in range(FEATURE_NUMBER):
        shift = (
            SCORE_WIDTH
            + (
                FEATURE_NUMBER
                - 1
                - feature_index
            ) * FEATURE_WIDTH
        )

        feature_bits = (
            packed_value >> shift
        ) & 0xFF

        decoded_features.append(
            signed_from_bits(
                feature_bits,
                FEATURE_WIDTH,
            )
        )

    score_bits = packed_value & 0xFFFFFFFF

    decoded_score = signed_from_bits(
        score_bits,
        SCORE_WIDTH,
    )

    if decoded_features != expected_features:
        raise ValueError(
            "Feature decode validation failed.\n"
            f"Expected: {expected_features}\n"
            f"Decoded:  {decoded_features}"
        )

    if decoded_score != expected_score:
        raise ValueError(
            "Score decode validation failed. "
            f"Expected {expected_score}, "
            f"decoded {decoded_score}"
        )


def load_and_merge(
    predictions_path: Path,
    data_path: Path,
    manifest: dict,
) -> pd.DataFrame:
    predictions = pd.read_parquet(
        predictions_path
    )

    data = pd.read_parquet(
        data_path
    )

    feature_order = manifest["feature_order"]

    if len(feature_order) != FEATURE_NUMBER:
        raise ValueError(
            "Manifest must contain exactly "
            f"{FEATURE_NUMBER} ordered features"
        )

    required_prediction_columns = {
        "variant_key",
        "hardware_score",
        "hidden_4",
        "hidden_5",
        "hidden_6",
        "hidden_7",
    }

    missing_prediction_columns = sorted(
        required_prediction_columns
        - set(predictions.columns)
    )

    if missing_prediction_columns:
        raise ValueError(
            "Prediction file is missing columns: "
            + ", ".join(missing_prediction_columns)
        )

    required_data_columns = {
        "variant_key",
        "split",
        *feature_order,
    }

    missing_data_columns = sorted(
        required_data_columns
        - set(data.columns)
    )

    if missing_data_columns:
        raise ValueError(
            "Dataset is missing columns: "
            + ", ".join(missing_data_columns)
        )

    if predictions["variant_key"].duplicated().any():
        duplicate_number = int(
            predictions["variant_key"]
            .duplicated()
            .sum()
        )

        raise ValueError(
            "Prediction file contains "
            f"{duplicate_number} duplicate variant keys"
        )

    test_data = data.loc[
        data["split"].eq("test"),
        [
            "variant_key",
            *feature_order,
        ],
    ].copy()

    if test_data["variant_key"].duplicated().any():
        duplicate_number = int(
            test_data["variant_key"]
            .duplicated()
            .sum()
        )

        raise ValueError(
            "Test dataset contains "
            f"{duplicate_number} duplicate variant keys"
        )

    merged = predictions.merge(
        test_data,
        on="variant_key",
        how="left",
        validate="one_to_one",
        indicator=True,
        sort=False,
    )

    unmatched_number = int(
        merged["_merge"].ne("both").sum()
    )

    if unmatched_number != 0:
        raise ValueError(
            f"{unmatched_number} prediction rows did "
            "not match the test dataset"
        )

    merged = merged.drop(
        columns="_merge"
    )

    missing_feature_number = int(
        merged[feature_order]
        .isna()
        .sum()
        .sum()
    )

    if missing_feature_number != 0:
        raise ValueError(
            "Merged vectors contain "
            f"{missing_feature_number} missing "
            "feature values"
        )

    if merged["hardware_score"].isna().any():
        raise ValueError(
            "Merged predictions contain missing "
            "hardware scores"
        )

    return merged


def select_vectors(
    merged: pd.DataFrame,
    test_number: int,
    seed: int,
) -> pd.DataFrame:
    if test_number <= 0:
        raise ValueError(
            "--test-number must be greater than zero"
        )

    if test_number > len(merged):
        raise ValueError(
            f"Requested {test_number} vectors, but only "
            f"{len(merged)} are available"
        )

    if test_number == len(merged):
        return merged.reset_index(drop=True)

    return merged.sample(
        n=test_number,
        random_state=seed,
        replace=False,
    ).reset_index(drop=True)


def generate_vectors(
    selected: pd.DataFrame,
    feature_order: list[str],
) -> list[str]:
    lines: list[str] = []

    for vector_index, row in selected.iterrows():
        features = [
            int(row[feature_name])
            for feature_name in feature_order
        ]

        score = int(row["hardware_score"])

        packed_value, packed_hex = pack_vector(
            features,
            score,
        )

        try:
            validate_packed_vector(
                packed_value=packed_value,
                packed_hex=packed_hex,
                expected_features=features,
                expected_score=score,
            )
        except ValueError as error:
            raise ValueError(
                "Packed-vector validation failed at "
                f"vector {vector_index}: {error}"
            ) from error

        lines.append(packed_hex)

    return lines


def validate_second_hidden_group(
    selected: pd.DataFrame,
) -> dict[str, int]:
    positive_counts: dict[str, int] = {}

    for hidden_index in range(4, 8):
        column = f"hidden_{hidden_index}"

        positive_count = int(
            selected[column].gt(0).sum()
        )

        positive_counts[column] = positive_count

        if positive_count == 0:
            raise ValueError(
                f"{column} is never positive in the "
                "selected vectors. Select a different "
                "seed or a larger test set."
            )

    return positive_counts


def main() -> None:
    args = parse_args()

    manifest = json.loads(
        args.manifest.read_text(
            encoding="utf-8"
        )
    )

    architecture = manifest.get(
        "architecture"
    )

    if architecture != "16-8-1":
        raise ValueError(
            "Expected a 16-8-1 V2 manifest, "
            f"received {architecture!r}"
        )

    if manifest.get("classification_rule") != (
        "folded_score >= 0"
    ):
        raise ValueError(
            "Unexpected classification rule in "
            "the export manifest"
        )

    merged = load_and_merge(
        predictions_path=args.predictions,
        data_path=args.data,
        manifest=manifest,
    )

    selected = select_vectors(
        merged=merged,
        test_number=args.test_number,
        seed=args.seed,
    )

    feature_order = list(
        manifest["feature_order"]
    )

    positive_counts = (
        validate_second_hidden_group(selected)
    )

    lines = generate_vectors(
        selected=selected,
        feature_order=feature_order,
    )

    if len(lines) != args.test_number:
        raise RuntimeError(
            "Generated vector count does not match "
            f"the requested count: {len(lines)} "
            f"versus {args.test_number}"
        )

    if len(set(lines)) != len(lines):
        duplicate_line_number = (
            len(lines) - len(set(lines))
        )

        print(
            "Warning: generated vectors contain "
            f"{duplicate_line_number} duplicate "
            "packed lines"
        )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        "\n".join(lines) + "\n",
        encoding="ascii",
        newline="\n",
    )

    result = {
        "status": "pass",
        "architecture": architecture,
        "source_prediction_rows": len(merged),
        "generated_vectors": len(lines),
        "vector_width_bits": VECTOR_WIDTH,
        "hex_characters_per_line":
            VECTOR_HEX_DIGITS,
        "selection_seed": args.seed,
        "score_min": int(
            selected["hardware_score"].min()
        ),
        "score_max": int(
            selected["hardware_score"].max()
        ),
        "second_hidden_group_positive_counts":
            positive_counts,
        "output": str(args.output),
    }

    print(
        json.dumps(
            result,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()