from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from software.dataset.build_features import load_contract
from software.model.hardware_model import HardwareQATModel


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def choose_device(requested: str) -> torch.device:
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
            "CUDA was requested, but CUDA is unavailable."
        )

    return device


def best_f1_threshold(
    labels: np.ndarray,
    scores: np.ndarray,
) -> int:
    precision, recall, thresholds = (
        precision_recall_curve(labels, scores)
    )

    if thresholds.size == 0:
        return 0

    denominator = precision[:-1] + recall[:-1]

    f1 = np.divide(
        2.0 * precision[:-1] * recall[:-1],
        denominator,
        out=np.zeros_like(denominator),
        where=denominator > 0,
    )

    index = int(np.argmax(f1))

    return int(np.ceil(thresholds[index]))


def classification_metrics(
    labels: np.ndarray,
    scores: np.ndarray,
    threshold: int,
) -> dict[str, object]:
    predictions = (
        scores >= threshold
    ).astype(np.int64)

    tn, fp, fn, tp = confusion_matrix(
        labels,
        predictions,
        labels=[0, 1],
    ).ravel()

    return {
        "threshold": int(threshold),
        "roc_auc": float(
            roc_auc_score(labels, scores)
        ),
        "average_precision": float(
            average_precision_score(labels, scores)
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
def predict(
    model: HardwareQATModel,
    features: np.ndarray,
    device: torch.device,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    model.eval()

    score_parts = []
    hidden_parts = []

    tensor = torch.from_numpy(
        features.astype(
            np.float32,
            copy=False,
        )
    )

    loader = DataLoader(
        TensorDataset(tensor),
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
    )

    for (batch,) in loader:
        scores, hidden = model.hardware_outputs(
            batch.to(device)
        )

        score_parts.append(
            scores.cpu().numpy()
        )

        hidden_parts.append(
            hidden.cpu().numpy()
        )

    return (
        np.concatenate(score_parts),
        np.concatenate(hidden_parts),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Train the FPGA-exact 16-4-1 "
            "INT8 classifier."
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
        "--contract",
        type=Path,
        default=Path("config/features.yaml"),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/model_v1"),
    )

    parser.add_argument(
        "--epochs",
        type=int,
        default=150,
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=4096,
    )

    parser.add_argument(
        "--learning-rate",
        type=float,
        default=0.02,
    )

    parser.add_argument(
        "--weight-decay",
        type=float,
        default=1.0e-5,
    )

    parser.add_argument(
        "--patience",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--device",
        default="auto",
        help="auto, cpu, cuda, or cuda:N",
    )

    parser.add_argument(
        "--logit-divisor",
        type=float,
        default=64.0,
    )

    parser.add_argument(
        "--unweighted-loss",
        action="store_true",
        help=(
            "Disable balanced positive-class "
            "weighting."
        ),
    )

    args = parser.parse_args()

    if args.epochs <= 0:
        raise SystemExit("--epochs must be positive")

    if args.batch_size <= 0:
        raise SystemExit(
            "--batch-size must be positive"
        )

    if args.patience <= 0:
        raise SystemExit(
            "--patience must be positive"
        )

    set_seed(args.seed)

    device = choose_device(args.device)
    contract = load_contract(args.contract)

    feature_ids = [
        feature["id"]
        for feature in contract["features"]
    ]

    frame = pd.read_parquet(args.input)

    required = {
        "variant_key",
        "label",
        "split",
        *feature_ids,
    }

    missing = sorted(
        required - set(frame.columns)
    )

    if missing:
        raise ValueError(
            "Model table is missing columns: "
            + ", ".join(missing)
        )

    train = frame.loc[
        frame["split"] == "train"
    ]

    validation = frame.loc[
        frame["split"] == "validation"
    ]

    if train.empty or validation.empty:
        raise ValueError(
            "Train and validation splits "
            "must both be non-empty."
        )

    x_train = train[
        feature_ids
    ].to_numpy(dtype=np.float32)

    y_train = train[
        "label"
    ].to_numpy(dtype=np.float32)

    x_validation = validation[
        feature_ids
    ].to_numpy(dtype=np.float32)

    y_validation = validation[
        "label"
    ].to_numpy(dtype=np.int64)

    train_dataset = TensorDataset(
        torch.from_numpy(x_train),
        torch.from_numpy(y_train),
    )

    generator = torch.Generator()
    generator.manual_seed(args.seed)

    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        generator=generator,
        num_workers=0,
    )

    model = HardwareQATModel(
        logit_divisor=args.logit_divisor,
    ).to(device)

    positive_count = float(y_train.sum())
    negative_count = float(
        len(y_train) - positive_count
    )

    if args.unweighted_loss:
        positive_weight = 1.0
    else:
        positive_weight = (
            negative_count / positive_count
        )

    criterion = nn.BCEWithLogitsLoss(
        pos_weight=torch.tensor(
            positive_weight,
            dtype=torch.float32,
            device=device,
        )
    )

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.learning_rate,
        weight_decay=args.weight_decay,
    )

    history = []

    best_average_precision = -np.inf
    best_epoch = 0
    best_state = None
    epochs_without_improvement = 0

    print(
        f"device={device}; "
        f"train={len(train):,}; "
        f"validation={len(validation):,}; "
        f"positive_weight={positive_weight:.4f}"
    )

    for epoch in range(
        1,
        args.epochs + 1,
    ):
        model.train()

        total_loss = 0.0
        total_rows = 0

        for features, labels in train_loader:
            features = features.to(device)
            labels = labels.to(device)

            optimizer.zero_grad(
                set_to_none=True
            )

            logits = model(features)
            loss = criterion(logits, labels)

            loss.backward()

            torch.nn.utils.clip_grad_norm_(
                model.parameters(),
                max_norm=10.0,
            )

            optimizer.step()

            with torch.no_grad():
                model.weight1.clamp_(
                    -127.0,
                    127.0,
                )

                model.weight2.clamp_(
                    -127.0,
                    127.0,
                )

                model.bias1.clamp_(
                    -1_000_000.0,
                    1_000_000.0,
                )

                model.bias2.clamp_(
                    -1_000_000.0,
                    1_000_000.0,
                )

            total_loss += (
                float(loss.item())
                * len(features)
            )

            total_rows += len(features)

        validation_scores, validation_hidden = (
            predict(
                model,
                x_validation,
                device,
                args.batch_size,
            )
        )

        validation_ap = float(
            average_precision_score(
                y_validation,
                validation_scores,
            )
        )

        validation_auc = float(
            roc_auc_score(
                y_validation,
                validation_scores,
            )
        )

        training_loss = (
            total_loss / total_rows
        )

        active_fraction = float(
            (validation_hidden > 0).mean()
        )

        saturation_fraction = float(
            (validation_hidden >= 127).mean()
        )

        history.append(
            {
                "epoch": epoch,
                "training_loss": training_loss,
                "validation_average_precision":
                    validation_ap,
                "validation_roc_auc":
                    validation_auc,
                "hidden_active_fraction":
                    active_fraction,
                "hidden_saturation_fraction":
                    saturation_fraction,
            }
        )

        print(
            f"epoch={epoch:03d} "
            f"loss={training_loss:.6f} "
            f"val_ap={validation_ap:.6f} "
            f"val_auc={validation_auc:.6f} "
            f"hidden_active={active_fraction:.3f} "
            f"hidden_saturated="
            f"{saturation_fraction:.3f}",
            flush=True,
        )

        if (
            validation_ap
            > best_average_precision + 1.0e-6
        ):
            best_average_precision = (
                validation_ap
            )

            best_epoch = epoch

            best_state = copy.deepcopy(
                model.state_dict()
            )

            epochs_without_improvement = 0

        else:
            epochs_without_improvement += 1

            if (
                epochs_without_improvement
                >= args.patience
            ):
                print(
                    "Early stopping after "
                    f"epoch {epoch}"
                )
                break

    if best_state is None:
        raise RuntimeError(
            "Training did not produce "
            "a model state."
        )

    model.load_state_dict(best_state)

    validation_scores, validation_hidden = (
        predict(
            model,
            x_validation,
            device,
            args.batch_size,
        )
    )

    threshold = best_f1_threshold(
        y_validation,
        validation_scores,
    )

    validation_metrics = (
        classification_metrics(
            y_validation,
            validation_scores,
            threshold,
        )
    )

    validation_metrics["best_epoch"] = (
        best_epoch
    )

    validation_metrics[
        "hidden_active_fraction"
    ] = float(
        (validation_hidden > 0).mean()
    )

    validation_metrics[
        "hidden_saturation_fraction"
    ] = float(
        (validation_hidden >= 127).mean()
    )

    parameters = {
        key: value.tolist()
        for key, value
        in model.integer_parameters().items()
    }

    parameters[
        "validation_threshold"
    ] = int(threshold)

    parameters["qshift"] = int(
        model.qshift
    )

    parameters["feature_order"] = (
        feature_ids
    )

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    torch.save(
        {
            "state_dict":
                model.state_dict(),
            "feature_order":
                feature_ids,
            "qshift":
                model.qshift,
            "logit_divisor":
                model.logit_divisor,
            "validation_threshold":
                threshold,
            "seed":
                args.seed,
        },
        args.output_dir / "model_qat.pt",
    )

    (
        args.output_dir
        / "integer_parameters.json"
    ).write_text(
        json.dumps(
            parameters,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (
        args.output_dir
        / "training_history.json"
    ).write_text(
        json.dumps(
            history,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "input": str(args.input),
        "input_sha256": sha256(
            args.input
        ),
        "contract": str(args.contract),
        "device": str(device),
        "seed": args.seed,
        "epochs_completed": len(history),
        "best_epoch": best_epoch,
        "positive_weight": positive_weight,
        "validation_metrics":
            validation_metrics,
    }

    (
        args.output_dir
        / "training_manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            validation_metrics,
            indent=2,
        )
    )

    print(
        "Saved trained model to "
        f"{args.output_dir}"
    )


if __name__ == "__main__":
    main()