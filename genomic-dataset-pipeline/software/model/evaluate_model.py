from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from software.model.hardware_model import (
    HardwareQATModel,
    integer_forward_numpy,
)
from software.model.train_model import (
    classification_metrics,
    predict,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the frozen FPGA model "
            "on the untouched test genes."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "data/processed/"
            "variants_model_int8.parquet"
        ),
    )

    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=4096,
    )

    parser.add_argument(
        "--device",
        default="cpu",
    )

    args = parser.parse_args()

    model_path = (
        args.model_dir / "model_qat.pt"
    )

    if not model_path.exists():
        raise FileNotFoundError(model_path)

    checkpoint = torch.load(
        model_path,
        map_location=args.device,
        weights_only=False,
    )

    feature_order = checkpoint[
        "feature_order"
    ]

    threshold = int(
        checkpoint["validation_threshold"]
    )

    model = HardwareQATModel(
        input_features=len(feature_order),
        hidden_features=4,
        qshift=int(checkpoint["qshift"]),
        logit_divisor=float(
            checkpoint["logit_divisor"]
        ),
    )

    model.load_state_dict(
        checkpoint["state_dict"]
    )

    device = torch.device(args.device)
    model = model.to(device)

    frame = pd.read_parquet(args.input)

    missing = sorted(
        {
            "variant_key",
            "gene",
            "label",
            "split",
            *feature_order,
        }
        - set(frame.columns)
    )

    if missing:
        raise ValueError(
            "Model table is missing columns: "
            + ", ".join(missing)
        )

    test = frame.loc[
        frame["split"] == "test"
    ].copy()

    if test.empty:
        raise ValueError(
            "Test split is empty."
        )

    features = test[
        feature_order
    ].to_numpy(dtype=np.int64)

    labels = test[
        "label"
    ].to_numpy(dtype=np.int64)

    torch_scores, torch_hidden = predict(
        model,
        features.astype(np.float32),
        device,
        args.batch_size,
    )

    torch_scores = np.rint(
        torch_scores
    ).astype(np.int64)

    torch_hidden = np.rint(
        torch_hidden
    ).astype(np.int64)

    parameters = model.integer_parameters()

    integer_scores, integer_hidden = (
        integer_forward_numpy(
            features=features,
            weight1=parameters["weight1"],
            bias1=parameters["bias1"],
            weight2=parameters["weight2"],
            bias2=parameters["bias2"],
            qshift=int(
                checkpoint["qshift"]
            ),
        )
    )

    np.testing.assert_array_equal(
        torch_scores,
        integer_scores,
    )

    np.testing.assert_array_equal(
        torch_hidden,
        integer_hidden,
    )

    metrics = classification_metrics(
        labels,
        integer_scores,
        threshold,
    )

    metrics.update(
        {
            "split": "test",
            "rows": int(len(test)),
            "genes": int(
                test["gene"].nunique()
            ),
            "positive_fraction": float(
                labels.mean()
            ),
            "model_seed": int(
                checkpoint["seed"]
            ),
            "model_sha256": sha256(
                model_path
            ),
            "dataset_sha256": sha256(
                args.input
            ),
            "pytorch_integer_match": True,
            "hidden_active_fraction": float(
                (integer_hidden > 0).mean()
            ),
            "hidden_saturation_fraction":
                float(
                    (
                        integer_hidden
                        >= 127
                    ).mean()
                ),
            "score_minimum": int(
                integer_scores.min()
            ),
            "score_maximum": int(
                integer_scores.max()
            ),
        }
    )

    predictions = test[
        [
            "variant_key",
            "gene",
            "label",
            "split",
        ]
    ].copy()

    predictions[
        "hardware_score"
    ] = integer_scores

    predictions[
        "prediction"
    ] = (
        integer_scores >= threshold
    ).astype(np.int8)

    for index in range(4):
        predictions[
            f"hidden_{index}"
        ] = integer_hidden[:, index]

    report_path = (
        args.model_dir
        / "test_metrics.json"
    )

    prediction_path = (
        args.model_dir
        / "test_predictions.parquet"
    )

    report_path.write_text(
        json.dumps(
            metrics,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    predictions.to_parquet(
        prediction_path,
        index=False,
    )

    print(
        json.dumps(
            metrics,
            indent=2,
        )
    )

    print(
        "Saved test predictions to "
        f"{prediction_path}"
    )


if __name__ == "__main__":
    main()