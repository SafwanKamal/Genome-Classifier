"""Export a trained 16-4-1 QAT model to Vivado-compatible files."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import numpy as np
import torch

from software.model.hardware_model import HardwareQATModel


INT8_MIN = -128
INT8_MAX = 127
INT32_MAX = (1 << 31) - 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def first(mapping: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def as_numpy(value: Any, name: str) -> np.ndarray:
    if value is None:
        raise KeyError(f"Integer parameters do not contain {name}")
    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()
    return np.asarray(value, dtype=np.int64)


def construct_model(checkpoint: dict[str, Any]) -> HardwareQATModel:
    feature_order = first(checkpoint, "feature_order", "features", default=[])
    feature_count = len(feature_order) or int(
        first(checkpoint, "input_features", "input_size", default=16)
    )
    hidden_count = int(
        first(checkpoint, "hidden_features", "hidden_size", default=4)
    )
    qshift = int(first(checkpoint, "qshift", default=4))
    logit_divisor = float(first(checkpoint, "logit_divisor", default=128.0))

    known = {
        "input_features": feature_count,
        "input_size": feature_count,
        "feature_count": feature_count,
        "num_features": feature_count,
        "hidden_features": hidden_count,
        "hidden_size": hidden_count,
        "hidden_count": hidden_count,
        "qshift": qshift,
        "logit_divisor": logit_divisor,
    }
    signature = inspect.signature(HardwareQATModel)
    kwargs: dict[str, Any] = {}
    missing: list[str] = []
    for name, parameter in signature.parameters.items():
        if name in known:
            kwargs[name] = known[name]
        elif parameter.default is inspect.Parameter.empty:
            missing.append(name)
    if missing:
        raise TypeError(
            "Unsupported HardwareQATModel constructor parameters: "
            + ", ".join(missing)
        )

    model = HardwareQATModel(**kwargs)
    state = first(
        checkpoint,
        "model_state_dict",
        "state_dict",
        "model",
    )
    if not isinstance(state, dict):
        raise KeyError(
            "model_qat.pt must contain model_state_dict, state_dict, or model"
        )
    model.load_state_dict(state)
    model.eval()
    return model


def extract_integer_parameters(
    model: HardwareQATModel,
) -> tuple[np.ndarray, ...]:
    parameters = model.integer_parameters()

    if not isinstance(parameters, dict):
        raise TypeError(
            "HardwareQATModel.integer_parameters() must return a dict"
        )

    hidden_weights = as_numpy(
        parameters.get("weight1"),
        "weight1",
    )

    hidden_biases = as_numpy(
        parameters.get("bias1"),
        "bias1",
    ).reshape(-1)

    output_weights = as_numpy(
        parameters.get("weight2"),
        "weight2",
    ).reshape(-1)

    output_bias = as_numpy(
        parameters.get("bias2"),
        "bias2",
    ).reshape(-1)

    if hidden_weights.shape == (16, 4):
        hidden_weights = hidden_weights.T

    if hidden_weights.shape != (4, 16):
        raise ValueError(
            "Expected weight1 shape (4, 16); "
            f"got {hidden_weights.shape}"
        )

    if hidden_biases.shape != (4,):
        raise ValueError(
            f"Expected bias1 shape (4,); got {hidden_biases.shape}"
        )

    if output_weights.shape != (4,):
        raise ValueError(
            f"Expected weight2 shape (1, 4); got "
            f"{parameters['weight2'].shape}"
        )

    if output_bias.shape != (1,):
        raise ValueError(
            f"Expected bias2 shape (1,); got {output_bias.shape}"
        )

    return (
        hidden_weights,
        hidden_biases,
        output_weights,
        output_bias,
    )


def validate_ranges(
    hidden_weights: np.ndarray,
    hidden_biases: np.ndarray,
    output_weights: np.ndarray,
    folded_output_bias: int,
) -> dict[str, Any]:
    all_weights = np.concatenate((hidden_weights.reshape(-1), output_weights))
    if all_weights.min() < INT8_MIN or all_weights.max() > INT8_MAX:
        raise ValueError(
            f"Weight outside INT8 range: {all_weights.min()}..{all_weights.max()}"
        )

    all_biases = np.append(hidden_biases, folded_output_bias)
    if all_biases.min() < -(1 << 31) or all_biases.max() > INT32_MAX:
        raise ValueError("Bias outside signed INT32 range")

    hidden_bounds = (
        np.abs(hidden_biases)
        + 127 * np.abs(hidden_weights).sum(axis=1)
    ).astype(np.int64)
    output_bound = int(
        abs(folded_output_bias) + 127 * np.abs(output_weights).sum()
    )
    if int(hidden_bounds.max()) > INT32_MAX or output_bound > INT32_MAX:
        raise OverflowError("Worst-case accumulator exceeds signed INT32")

    return {
        "hidden_worst_case_absolute_accumulator": hidden_bounds.tolist(),
        "output_worst_case_absolute_accumulator": output_bound,
    }


def write_mem(path: Path, values: np.ndarray) -> None:
    path.write_text(
        "".join(f"{int(value) & 0xFF:02X}\n" for value in values.reshape(-1)),
        encoding="ascii",
        newline="\n",
    )


def read_mem(path: Path) -> np.ndarray:
    unsigned = np.asarray(
        [int(line, 16) for line in path.read_text(encoding="ascii").splitlines()],
        dtype=np.int16,
    )
    return np.where(unsigned >= 128, unsigned - 256, unsigned).astype(np.int64)


def sv_int32(value: int) -> str:
    return f"-32'sd{abs(value)}" if value < 0 else f"32'sd{value}"


def write_header(
    path: Path,
    hidden_biases: np.ndarray,
    output_bias: int,
    folded_output_bias: int,
    qshift: int,
    threshold: int,
) -> None:
    lines = [
        "// Generated by software.model.export_fpga; do not edit manually.",
        "`ifndef GENOME_CLASSIFIER_MODEL_PARAMETERS_SVH",
        "`define GENOME_CLASSIFIER_MODEL_PARAMETERS_SVH",
        "",
    ]
    for index, value in enumerate(hidden_biases):
        lines.append(
            f"localparam logic signed [31:0] MODEL_BIAS_{index} = "
            f"{sv_int32(int(value))};"
        )
    lines.extend(
        [
            f"localparam logic signed [31:0] MODEL_OUTPUT_BIAS_ORIGINAL = {sv_int32(output_bias)};",
            f"localparam logic signed [31:0] MODEL_OUTPUT_BIAS = {sv_int32(folded_output_bias)};",
            f"localparam integer MODEL_QSHIFT = {qshift};",
            f"localparam integer MODEL_ORIGINAL_THRESHOLD = {threshold};",
            "",
            "`endif",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="ascii", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the selected QAT model for the FPGA RTL."
    )
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_path = args.model_dir / "model_qat.pt"
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, dict):
        raise TypeError("model_qat.pt must contain a checkpoint dictionary")

    model = construct_model(checkpoint)
    hidden_weights, hidden_biases, output_weights, output_bias_array = (
        extract_integer_parameters(model)
    )

    threshold = int(
        first(checkpoint, "validation_threshold", "threshold", default=0)
    )
    if threshold == 0:
        manifest_path = args.model_dir / "training_manifest.json"
        if manifest_path.is_file():
            training_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            threshold = int(
                first(training_manifest, "validation_threshold", "threshold", default=0)
            )
    if threshold == 0:
        raise ValueError(
            "Could not find the validation threshold in model_qat.pt or training_manifest.json"
        )

    qshift = int(first(checkpoint, "qshift", default=getattr(model, "qshift", 4)))
    original_output_bias = int(output_bias_array[0])
    folded_output_bias = original_output_bias - threshold
    bounds = validate_ranges(
        hidden_weights,
        hidden_biases,
        output_weights,
        folded_output_bias,
    )

    output_dir = args.output_dir
    memory_dir = output_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    mem_paths: list[Path] = []
    for index in range(4):
        path = memory_dir / f"neuron_{index}_weights.mem"
        write_mem(path, hidden_weights[index])
        mem_paths.append(path)
    output_mem = memory_dir / "output_neuron_weights.mem"
    write_mem(output_mem, output_weights)
    mem_paths.append(output_mem)

    for index, path in enumerate(mem_paths[:4]):
        if not np.array_equal(read_mem(path), hidden_weights[index]):
            raise RuntimeError(f"Verification failed for {path}")
    if not np.array_equal(read_mem(output_mem), output_weights):
        raise RuntimeError(f"Verification failed for {output_mem}")

    header_path = output_dir / "model_parameters.svh"
    write_header(
        header_path,
        hidden_biases,
        original_output_bias,
        folded_output_bias,
        qshift,
        threshold,
    )

    feature_order = first(checkpoint, "feature_order", "features", default=[])
    manifest = {
        "format_version": 1,
        "source_model": str(model_path),
        "source_model_sha256": sha256(model_path),
        "architecture": "16-4-1",
        "feature_order": list(feature_order),
        "qshift": qshift,
        "classification_rule": "folded_score >= 0",
        "original_validation_threshold": threshold,
        "hidden_weights": hidden_weights.tolist(),
        "hidden_biases": hidden_biases.tolist(),
        "output_weights": output_weights.tolist(),
        "original_output_bias": original_output_bias,
        "folded_output_bias": folded_output_bias,
        "accumulator_bounds": bounds,
        "files": {},
    }
    for path in [*mem_paths, header_path]:
        manifest["files"][str(path.relative_to(output_dir))] = sha256(path)

    manifest_path = output_dir / "export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(json.dumps({
        "status": "pass",
        "output_dir": str(output_dir),
        "threshold_fold": f"{original_output_bias} - {threshold} = {folded_output_bias}",
        "qshift": qshift,
        "memory_files": [str(path) for path in mem_paths],
        "parameter_header": str(header_path),
        "manifest": str(manifest_path),
        "accumulator_bounds": bounds,
    }, indent=2))


if __name__ == "__main__":
    main()
