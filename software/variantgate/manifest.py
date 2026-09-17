from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelManifest:
    path: Path
    sha256: str
    source_model_sha256: str
    architecture: str
    feature_order: tuple[str, ...]
    qshift: int
    classification_rule: str
    original_validation_threshold: int
    folded_output_bias: int
    hidden_weights: tuple[tuple[int, ...], ...]
    hidden_biases: tuple[int, ...]
    output_weights: tuple[int, ...]

    @property
    def feature_number(self) -> int:
        return len(self.feature_order)

    @property
    def hidden_number(self) -> int:
        return len(self.hidden_weights)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def load_model_manifest(path: Path) -> ModelManifest:
    path = path.resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))

    required_fields = {
        "source_model_sha256",
        "architecture",
        "feature_order",
        "qshift",
        "classification_rule",
        "original_validation_threshold",
        "folded_output_bias",
        "hidden_weights",
        "hidden_biases",
        "output_weights",
    }

    missing_fields = sorted(required_fields - set(raw))

    if missing_fields:
        raise ValueError(
            "Export manifest is missing fields: " + ", ".join(missing_fields)
        )

    feature_order = tuple(str(name) for name in raw["feature_order"])
    hidden_weights = tuple(
        tuple(int(value) for value in neuron_weights)
        for neuron_weights in raw["hidden_weights"]
    )
    hidden_biases = tuple(int(value) for value in raw["hidden_biases"])
    output_weights = tuple(int(value) for value in raw["output_weights"])

    if len(feature_order) != 16:
        raise ValueError(
            "VariantGate currently requires exactly 16 input features"
        )

    if not hidden_weights:
        raise ValueError("Manifest contains no hidden neurons")

    for neuron_index, weights in enumerate(hidden_weights):
        if len(weights) != len(feature_order):
            raise ValueError(
                f"Hidden neuron {neuron_index} has {len(weights)} weights; "
                f"expected {len(feature_order)}"
            )

    if len(hidden_biases) != len(hidden_weights):
        raise ValueError("Hidden weight and bias counts differ")

    if len(output_weights) != len(hidden_weights):
        raise ValueError(
            "Output weight count does not match the hidden-layer width"
        )

    qshift = int(raw["qshift"])

    if qshift <= 0:
        raise ValueError("qshift must be greater than zero")

    classification_rule = str(raw["classification_rule"])

    if classification_rule != "folded_score >= 0":
        raise ValueError(
            f"Unsupported classification rule: {classification_rule}"
        )

    expected_architecture = f"{len(feature_order)}-{len(hidden_weights)}-1"
    architecture = str(raw["architecture"])

    if architecture != expected_architecture:
        raise ValueError(
            "Manifest architecture does not match its parameter dimensions: "
            f"{architecture} versus {expected_architecture}"
        )

    return ModelManifest(
        path=path,
        sha256=sha256_file(path),
        source_model_sha256=str(raw["source_model_sha256"]),
        architecture=architecture,
        feature_order=feature_order,
        qshift=qshift,
        classification_rule=classification_rule,
        original_validation_threshold=int(
            raw["original_validation_threshold"]
        ),
        folded_output_bias=int(raw["folded_output_bias"]),
        hidden_weights=hidden_weights,
        hidden_biases=hidden_biases,
        output_weights=output_weights,
    )
