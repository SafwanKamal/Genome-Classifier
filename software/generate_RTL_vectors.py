from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


FEATURE_NUMBER = 16
VECTOR_WIDTH_BITS = 160


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def calculate_score(
    features: list[int],
    manifest: dict,
) -> int:
    feature_vector = np.asarray(
        features,
        dtype=np.int64,
    )

    hidden_weights = np.asarray(
        manifest["hidden_weights"],
        dtype=np.int64,
    )

    hidden_biases = np.asarray(
        manifest["hidden_biases"],
        dtype=np.int64,
    )

    output_weights = np.asarray(
        manifest["output_weights"],
        dtype=np.int64,
    )

    output_bias = int(
        manifest["folded_output_bias"]
    )

    qshift = int(manifest["qshift"])

    hidden_scores = (
        hidden_weights @ feature_vector
        + hidden_biases
    )

    rounded_scores = (
        hidden_scores
        + (1 << (qshift - 1))
    )

    quantized_scores = np.where(
        hidden_scores <= 0,
        0,
        np.minimum(
            rounded_scores >> qshift,
            127,
        ),
    )

    return int(
        output_weights @ quantized_scores
        + output_bias
    )


def encode_vector(
    features: list[int],
    score: int,
) -> str:
    if len(features) != FEATURE_NUMBER:
        raise ValueError(
            f"Expected {FEATURE_NUMBER} features"
        )

    if any(
        value < -128 or value > 127
        for value in features
    ):
        raise ValueError(
            "Feature outside signed INT8 range"
        )

    feature_hex = "".join(
        f"{value & 0xFF:02X}"
        for value in features
    )

    score_hex = f"{score & 0xFFFFFFFF:08X}"

    encoded = feature_hex + score_hex

    if len(encoded) != VECTOR_WIDTH_BITS // 4:
        raise RuntimeError(
            "Encoded vector has incorrect width"
        )

    return encoded


def edge_vectors() -> list[list[int]]:
    return [
        [0] * FEATURE_NUMBER,
        [1] * FEATURE_NUMBER,
        [127] * FEATURE_NUMBER,
        [-128] * FEATURE_NUMBER,
        [
            127 if index % 2 == 0 else -128
            for index in range(FEATURE_NUMBER)
        ],
        [
            -128 + 17 * index
            for index in range(FEATURE_NUMBER)
        ],
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate bit-exact FPGA core "
            "simulation vectors."
        )
    )

    parser.add_argument(
        "--data",
        type=Path,
        default=Path(
            "genomic-dataset-pipeline/"
            "data/processed/"
            "variants_model_int8.parquet"
        ),
    )

    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "genomic-dataset-pipeline/"
            "artifacts/model_v1_seed_21/"
            "fpga_export/export_manifest.json"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "simulation/model_v1_core_vectors.mem"
        ),
    )

    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(
            "simulation/model_v1_core_vectors.json"
        ),
    )

    parser.add_argument(
        "--count",
        type=int,
        default=1000,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.count < len(edge_vectors()):
        raise ValueError(
            f"Vector count must be at least "
            f"{len(edge_vectors())}"
        )

    manifest = json.loads(
        args.manifest.read_text(
            encoding="utf-8"
        )
    )

    feature_order = manifest["feature_order"]

    if len(feature_order) != FEATURE_NUMBER:
        raise ValueError(
            "Manifest must contain 16 features"
        )

    frame = pd.read_parquet(args.data)

    required_columns = {
        "split",
        *feature_order,
    }

    missing = sorted(
        required_columns - set(frame.columns)
    )

    if missing:
        raise ValueError(
            "Dataset is missing columns: "
            + ", ".join(missing)
        )

    test_frame = frame.loc[
        frame["split"] == "test"
    ]

    sample_count = (
        args.count - len(edge_vectors())
    )

    if sample_count > len(test_frame):
        raise ValueError(
            "Requested more held-out vectors "
            "than are available"
        )

    sampled = test_frame.sample(
        n=sample_count,
        random_state=args.seed,
        replace=False,
    )

    vectors = edge_vectors()

    vectors.extend([
        [
            int(row[feature_name])
            for feature_name in feature_order
        ]
        for _, row in sampled.iterrows()
    ])

    encoded_lines = []

    score_minimum = None
    score_maximum = None

    for features in vectors:
        score = calculate_score(
            features,
            manifest,
        )

        encoded_lines.append(
            encode_vector(
                features,
                score,
            )
        )

        score_minimum = (
            score
            if score_minimum is None
            else min(score_minimum, score)
        )

        score_maximum = (
            score
            if score_maximum is None
            else max(score_maximum, score)
        )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        "\n".join(encoded_lines) + "\n",
        encoding="ascii",
        newline="\n",
    )

    metadata = {
        "format_version": 1,
        "vector_count": len(vectors),
        "vector_width_bits": VECTOR_WIDTH_BITS,
        "feature_count": FEATURE_NUMBER,
        "feature_order": feature_order,
        "score_encoding": (
            "signed INT32 two's complement"
        ),
        "layout": (
            "feature[0] ... feature[15] "
            "expected_score"
        ),
        "edge_vector_count": len(
            edge_vectors()
        ),
        "held_out_vector_count": (
            sample_count
        ),
        "random_seed": args.seed,
        "minimum_expected_score": (
            score_minimum
        ),
        "maximum_expected_score": (
            score_maximum
        ),
        "dataset_sha256": sha256(
            args.data
        ),
        "manifest_sha256": sha256(
            args.manifest
        ),
        "memory_file_sha256": sha256(
            args.output
        ),
    }

    args.metadata.write_text(
        json.dumps(
            metadata,
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            metadata,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()