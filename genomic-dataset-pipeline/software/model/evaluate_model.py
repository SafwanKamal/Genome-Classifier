from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch.utils.data import (
    DataLoader,
    TensorDataset,
)

from software.model.hardware_model import (
    HardwareQATModel,
    integer_forward_numpy,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def first(
    mapping: dict[str, Any],
    *names: str,
    default: Any = None,
) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]

    return default


def choose_device(
    requested: str,
) -> torch.device:
    if requested == "auto":
        return torch.device(
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

    device = torch.device(requested)

    if (
        device.type == "cuda"
        and not torch.cuda.is_available()
    ):
        raise SystemExit(
            "CUDA was requested, but "
            "torch.cuda.is_available() is false"
        )

    return device


def classification_metrics(
    labels: np.ndarray,
    folded_scores: np.ndarray,
) -> dict[str, object]:
    predictions = (
        folded_scores >= 0
    ).astype(np.int8)

    tn, fp, fn, tp = confusion_matrix(
        labels,
        predictions,
        labels=[0, 1],
    ).ravel()

    return {
        "roc_auc": float(
            roc_auc_score(
                labels,
                folded_scores,
            )
        ),
        "average_precision": float(
            average_precision_score(
                labels,
                folded_scores,
            )
        ),
        "accuracy": float(
            accuracy_score(
                labels,
                predictions,
            )
        ),
        "precision": float(
            precision_score(
                labels,
                predictions,
                zero_division=0,
            )
        ),
        "recall": float(
            recall_score(
                labels,
                predictions,
                zero_division=0,
            )
        ),
        "f1": float(
            f1_score(
                labels,
                predictions,
                zero_division=0,
            )
        ),
        "balanced_accuracy": float(
            balanced_accuracy_score(
                labels,
                predictions,
            )
        ),
        "matthews_correlation": float(
            matthews_corrcoef(
                labels,
                predictions,
            )
        ),
        "confusion_matrix": {
            "true_negative": int(tn),
            "false_positive": int(fp),
            "false_negative": int(fn),
            "true_positive": int(tp),
        },
    }


@torch.no_grad()
def predict_torch(
    model: HardwareQATModel,
    features: np.ndarray,
    device: torch.device,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()

    score_parts: list[np.ndarray] = []
    hidden_parts: list[np.ndarray] = []

    feature_tensor = torch.from_numpy(
        features.astype(
            np.float32,
            copy=False,
        )
    )

    loader = DataLoader(
        TensorDataset(feature_tensor),
        batch_size=batch_size,
        shuffle=False,
    )

    for (batch,) in loader:
        scores, hidden = (
            model.hardware_outputs(
                batch.to(device)
            )
        )

        score_parts.append(
            scores.cpu().numpy()
        )

        hidden_parts.append(
            hidden.cpu().numpy()
        )

    scores = np.rint(
        np.concatenate(score_parts)
    ).astype(np.int64)

    hidden = np.rint(
        np.concatenate(hidden_parts)
    ).astype(np.int64)

    return scores, hidden


def load_threshold(
    checkpoint: dict[str, Any],
    model_dir: Path,
) -> int:
    threshold = first(
        checkpoint,
        "validation_threshold",
        "threshold",
    )

    if threshold is not None:
        return int(threshold)

    manifest_path = (
        model_dir
        / "training_manifest.json"
    )

    if manifest_path.is_file():
        manifest = json.loads(
            manifest_path.read_text(
                encoding="utf-8"
            )
        )

        metrics = manifest.get(
            "validation_metrics",
            {},
        )

        threshold = first(
            manifest,
            "validation_threshold",
            "threshold",
            default=metrics.get(
                "threshold"
            ),
        )

        if threshold is not None:
            return int(threshold)

    raise ValueError(
        "Could not find the validation "
        "threshold in model_qat.pt or "
        "training_manifest.json"
    )


def parse_args() -> argparse.Namespace:
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
        default="auto",
        help=(
            "auto, cpu, cuda, or cuda:N"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.batch_size <= 0:
        raise SystemExit(
            "--batch-size must be positive"
        )

    if not args.input.is_file():
        raise FileNotFoundError(
            args.input
        )

    model_path = (
        args.model_dir
        / "model_qat.pt"
    )

    if not model_path.is_file():
        raise FileNotFoundError(
            model_path
        )

    device = choose_device(
        args.device
    )

    checkpoint = torch.load(
        model_path,
        map_location="cpu",
        weights_only=False,
    )

    if not isinstance(checkpoint, dict):
        raise TypeError(
            "model_qat.pt must contain "
            "a checkpoint dictionary"
        )

    state_dict = first(
        checkpoint,
        "model_state_dict",
        "state_dict",
        "model",
    )

    if not isinstance(state_dict, dict):
        raise KeyError(
            "Checkpoint does not contain "
            "model_state_dict, state_dict, or model"
        )

    if "weight1" not in state_dict:
        raise KeyError(
            "Model state dictionary "
            "does not contain weight1"
        )

    input_features = int(
        first(
            checkpoint,
            "input_features",
            "input_size",
            default=(
                state_dict[
                    "weight1"
                ].shape[1]
            ),
        )
    )

    hidden_features = int(
        first(
            checkpoint,
            "hidden_features",
            "hidden_size",
            default=(
                state_dict[
                    "weight1"
                ].shape[0]
            ),
        )
    )

    qshift = int(
        first(
            checkpoint,
            "qshift",
            default=4,
        )
    )

    logit_divisor = float(
        first(
            checkpoint,
            "logit_divisor",
            default=64.0,
        )
    )

    feature_order = list(
        first(
            checkpoint,
            "feature_order",
            "features",
            default=[],
        )
    )

    if not feature_order:
        raise ValueError(
            "Checkpoint does not contain "
            "feature_order"
        )

    if (
        len(feature_order)
        != input_features
    ):
        raise ValueError(
            "Checkpoint contains "
            f"{len(feature_order)} feature names, "
            "but the model expects "
            f"{input_features} inputs"
        )

    model = HardwareQATModel(
        input_features=input_features,
        hidden_features=hidden_features,
        qshift=qshift,
        logit_divisor=logit_divisor,
    )

    model.load_state_dict(
        state_dict
    )

    model = model.to(device)
    model.eval()

    frame = pd.read_parquet(
        args.input
    )

    required = {
        "variant_key",
        "gene",
        "label",
        "split",
        *feature_order,
    }

    missing = sorted(
        required - set(frame.columns)
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
            "The input table does not "
            "contain test rows"
        )

    if test[
        "variant_key"
    ].duplicated().any():
        raise ValueError(
            "The test split contains "
            "duplicate variant keys"
        )

    features = test[
        feature_order
    ].to_numpy(
        dtype=np.int64
    )

    labels = test[
        "label"
    ].to_numpy(
        dtype=np.int64
    )

    (
        torch_scores,
        torch_hidden,
    ) = predict_torch(
        model,
        features,
        device,
        args.batch_size,
    )

    integer_parameters = (
        model.integer_parameters()
    )

    (
        numpy_scores,
        numpy_hidden,
    ) = integer_forward_numpy(
        features,
        integer_parameters["weight1"],
        integer_parameters["bias1"],
        integer_parameters["weight2"],
        integer_parameters["bias2"],
        qshift=qshift,
    )

    if not np.array_equal(
        torch_scores,
        numpy_scores,
    ):
        mismatch_count = int(
            np.count_nonzero(
                torch_scores
                != numpy_scores
            )
        )

        raise RuntimeError(
            "PyTorch and NumPy output "
            "scores differ for "
            f"{mismatch_count} rows"
        )

    if not np.array_equal(
        torch_hidden,
        numpy_hidden,
    ):
        mismatch_count = int(
            np.count_nonzero(
                torch_hidden
                != numpy_hidden
            )
        )

        raise RuntimeError(
            "PyTorch and NumPy hidden "
            "activations differ at "
            f"{mismatch_count} positions"
        )

    threshold = load_threshold(
        checkpoint,
        args.model_dir,
    )

    folded_scores = (
        numpy_scores - threshold
    )

    predictions = (
        folded_scores >= 0
    ).astype(np.int8)

    metrics = classification_metrics(
        labels,
        folded_scores,
    )

    prediction_frame = test[
        [
            "variant_key",
            "gene",
            "label",
            "split",
        ]
    ].copy()

    prediction_frame[
        "hardware_score"
    ] = folded_scores.astype(
        np.int64
    )

    prediction_frame[
        "prediction"
    ] = predictions

    for hidden_index in range(
        hidden_features
    ):
        prediction_frame[
            f"hidden_{hidden_index}"
        ] = numpy_hidden[
            :,
            hidden_index,
        ].astype(np.int64)

    args.model_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    predictions_path = (
        args.model_dir
        / "test_predictions.parquet"
    )

    prediction_frame.to_parquet(
        predictions_path,
        index=False,
    )

    report = {
        "status":
            "pass",
        "evaluation_stage":
            "locked_test_set",
        "test_set_policy": (
            "Final evaluation only; "
            "not used for model selection or tuning."
        ),
        "model":
            str(model_path),
        "model_sha256":
            sha256(model_path),
        "input":
            str(args.input),
        "input_sha256":
            sha256(args.input),
        "architecture": (
            f"{input_features}-"
            f"{hidden_features}-1"
        ),
        "test_variants":
            int(len(test)),
        "unique_test_genes":
            int(
                test["gene"].nunique()
            ),
        "validation_threshold":
            int(threshold),
        "classification_rule":
            "folded_score >= 0",
        "pytorch_numpy_score_matches":
            int(len(test)),
        "pytorch_numpy_score_mismatches":
            0,
        **metrics,
        "predictions":
            str(predictions_path),
    }

    report_path = (
        args.model_dir
        / "test_metrics.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report[
        "predictions_sha256"
    ] = sha256(predictions_path)

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            report,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()