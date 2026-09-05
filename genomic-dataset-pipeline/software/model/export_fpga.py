"""Export a trained single-hidden-layer QAT model to Vivado files."""

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
        for block in iter(
            lambda: stream.read(1024 * 1024),
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


def as_numpy(
    value: Any,
    name: str,
) -> np.ndarray:
    if value is None:
        raise KeyError(
            f"Integer parameters do not contain {name}"
        )

    if isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()

    return np.asarray(
        value,
        dtype=np.int64,
    )


def construct_model(
    checkpoint: dict[str, Any],
) -> HardwareQATModel:
    feature_order = first(
        checkpoint,
        "feature_order",
        "features",
        default=[],
    )

    feature_count = len(feature_order) or int(
        first(
            checkpoint,
            "input_features",
            "input_size",
            default=16,
        )
    )

    hidden_count = int(
        first(
            checkpoint,
            "hidden_features",
            "hidden_size",
            default=4,
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
            default=128.0,
        )
    )

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

    signature = inspect.signature(
        HardwareQATModel
    )

    kwargs: dict[str, Any] = {}
    missing: list[str] = []

    for name, parameter in signature.parameters.items():
        if name in known:
            kwargs[name] = known[name]
        elif parameter.default is inspect.Parameter.empty:
            missing.append(name)

    if missing:
        raise TypeError(
            "Unsupported HardwareQATModel "
            "constructor parameters: "
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
            "model_qat.pt must contain "
            "model_state_dict, state_dict, or model"
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
            "HardwareQATModel.integer_parameters() "
            "must return a dict"
        )

    hidden_weights = as_numpy(
        first(
            parameters,
            "hidden_weights",
            "hidden_weight",
            "weight1",
            "w1",
            "fc1_weight",
        ),
        "hidden weights",
    )

    hidden_biases = as_numpy(
        first(
            parameters,
            "hidden_biases",
            "hidden_bias",
            "bias1",
            "b1",
            "fc1_bias",
        ),
        "hidden biases",
    ).reshape(-1)

    output_weights = as_numpy(
        first(
            parameters,
            "output_weights",
            "output_weight",
            "weight2",
            "w2",
            "fc2_weight",
        ),
        "output weights",
    ).reshape(-1)

    output_bias = as_numpy(
        first(
            parameters,
            "output_bias",
            "output_biases",
            "bias2",
            "b2",
            "fc2_bias",
        ),
        "output bias",
    ).reshape(-1)

    input_features = int(
        model.input_features
    )

    hidden_features = int(
        model.hidden_features
    )

    if hidden_weights.shape == (
        input_features,
        hidden_features,
    ):
        hidden_weights = hidden_weights.T

    if hidden_weights.shape != (
        hidden_features,
        input_features,
    ):
        raise ValueError(
            "Expected hidden weights with shape "
            f"({hidden_features}, {input_features}); "
            f"got {hidden_weights.shape}"
        )

    if hidden_biases.shape != (
        hidden_features,
    ):
        raise ValueError(
            f"Expected {hidden_features} hidden biases; "
            f"got {hidden_biases.shape}"
        )

    if output_weights.shape != (
        hidden_features,
    ):
        raise ValueError(
            f"Expected {hidden_features} output weights; "
            f"got {output_weights.shape}"
        )

    if output_bias.shape != (1,):
        raise ValueError(
            "Expected one output bias; "
            f"got {output_bias.shape}"
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
    all_weights = np.concatenate(
        (
            hidden_weights.reshape(-1),
            output_weights,
        )
    )

    if (
        all_weights.min() < INT8_MIN
        or all_weights.max() > INT8_MAX
    ):
        raise ValueError(
            "Weight outside INT8 range: "
            f"{all_weights.min()}..{all_weights.max()}"
        )

    all_biases = np.append(
        hidden_biases,
        folded_output_bias,
    )

    if (
        all_biases.min() < -(1 << 31)
        or all_biases.max() > INT32_MAX
    ):
        raise ValueError(
            "Bias outside signed INT32 range"
        )

    hidden_bounds = (
        np.abs(hidden_biases)
        + 127
        * np.abs(hidden_weights).sum(axis=1)
    ).astype(np.int64)

    output_bound = int(
        abs(folded_output_bias)
        + 127
        * np.abs(output_weights).sum()
    )

    if (
        int(hidden_bounds.max()) > INT32_MAX
        or output_bound > INT32_MAX
    ):
        raise OverflowError(
            "Worst-case accumulator exceeds "
            "signed INT32"
        )

    return {
        "hidden_worst_case_absolute_accumulator":
            hidden_bounds.tolist(),
        "output_worst_case_absolute_accumulator":
            output_bound,
    }


def write_mem(
    path: Path,
    values: np.ndarray,
) -> None:
    path.write_text(
        "".join(
            f"{int(value) & 0xFF:02X}\n"
            for value in values.reshape(-1)
        ),
        encoding="ascii",
        newline="\n",
    )


def read_mem(
    path: Path,
) -> np.ndarray:
    unsigned = np.asarray(
        [
            int(line, 16)
            for line in path.read_text(
                encoding="ascii"
            ).splitlines()
        ],
        dtype=np.int16,
    )

    return np.where(
        unsigned >= 128,
        unsigned - 256,
        unsigned,
    ).astype(np.int64)


def signed_limits(
    width: int,
) -> tuple[int, int]:
    return (
        -(1 << (width - 1)),
        (1 << (width - 1)) - 1,
    )


def pack_lanes(
    values: list[int],
    lane_width: int,
) -> int:
    minimum, maximum = signed_limits(
        lane_width
    )

    mask = (
        1 << lane_width
    ) - 1

    packed = 0

    for value in values:
        if (
            value < minimum
            or value > maximum
        ):
            raise ValueError(
                f"Value {value} does not fit in "
                f"signed {lane_width} bits"
            )

        packed = (
            packed << lane_width
        ) | (value & mask)

    return packed


def write_dense_engine_memory(
    memory_dir: Path,
    weights: np.ndarray,
    biases: np.ndarray,
    *,
    name: str,
    mac_lanes: int,
    weight_width: int = 8,
    bias_width: int = 32,
) -> tuple[Path, Path]:
    weights = np.asarray(
        weights,
        dtype=np.int64,
    )

    biases = np.asarray(
        biases,
        dtype=np.int64,
    ).reshape(-1)

    if weights.ndim != 2:
        raise ValueError(
            "Weights must have shape "
            "[output_number, input_number]"
        )

    if mac_lanes <= 0:
        raise ValueError(
            "mac_lanes must be positive"
        )

    output_number, input_number = (
        weights.shape
    )

    if biases.shape != (
        output_number,
    ):
        raise ValueError(
            "Biases must have shape "
            f"({output_number},); "
            f"got {biases.shape}"
        )

    output_group_number = (
        output_number + mac_lanes - 1
    ) // mac_lanes

    weight_hex_digits = (
        mac_lanes * weight_width + 3
    ) // 4

    bias_hex_digits = (
        mac_lanes * bias_width + 3
    ) // 4

    weight_lines: list[str] = []
    bias_lines: list[str] = []

    for group_index in range(
        output_group_number
    ):
        for input_index in range(
            input_number
        ):
            lane_values: list[int] = []

            for lane in range(mac_lanes):
                output_index = (
                    group_index
                    * mac_lanes
                    + lane
                )

                if (
                    output_index
                    < output_number
                ):
                    value = int(
                        weights[
                            output_index,
                            input_index,
                        ]
                    )
                else:
                    value = 0

                lane_values.append(value)

            packed_weight = pack_lanes(
                lane_values,
                weight_width,
            )

            weight_lines.append(
                f"{packed_weight:0{weight_hex_digits}X}"
            )

        lane_biases: list[int] = []

        for lane in range(mac_lanes):
            output_index = (
                group_index
                * mac_lanes
                + lane
            )

            if (
                output_index
                < output_number
            ):
                value = int(
                    biases[output_index]
                )
            else:
                value = 0

            lane_biases.append(value)

        packed_bias = pack_lanes(
            lane_biases,
            bias_width,
        )

        bias_lines.append(f"{packed_bias:0{bias_hex_digits}X}")

    weight_path = (
        memory_dir
        / f"dense_{name}_weights.mem"
    )

    bias_path = (
        memory_dir
        / f"dense_{name}_biases.mem"
    )

    weight_path.write_text(
        "\n".join(weight_lines) + "\n",
        encoding="ascii",
        newline="\n",
    )

    bias_path.write_text(
        "\n".join(bias_lines) + "\n",
        encoding="ascii",
        newline="\n",
    )

    return (
        weight_path,
        bias_path,
    )


def sv_int32(
    value: int,
) -> str:
    if value < 0:
        return f"-32'sd{abs(value)}"

    return f"32'sd{value}"


def write_header(
    path: Path,
    hidden_biases: np.ndarray,
    output_bias: int,
    folded_output_bias: int,
    qshift: int,
    threshold: int,
) -> None:
    lines = [
        (
            "// Generated by "
            "software.model.export_fpga; "
            "do not edit manually."
        ),
        (
            "`ifndef "
            "GENOME_CLASSIFIER_MODEL_PARAMETERS_SVH"
        ),
        (
            "`define "
            "GENOME_CLASSIFIER_MODEL_PARAMETERS_SVH"
        ),
        "",
    ]

    for index, value in enumerate(
        hidden_biases
    ):
        lines.append(
            "localparam logic signed [31:0] "
            f"MODEL_BIAS_{index} = "
            f"{sv_int32(int(value))};"
        )

    lines.extend(
        [
            (
                "localparam logic signed [31:0] "
                "MODEL_OUTPUT_BIAS_ORIGINAL = "
                f"{sv_int32(output_bias)};"
            ),
            (
                "localparam logic signed [31:0] "
                "MODEL_OUTPUT_BIAS = "
                f"{sv_int32(folded_output_bias)};"
            ),
            (
                "localparam integer "
                f"MODEL_QSHIFT = {qshift};"
            ),
            (
                "localparam integer "
                "MODEL_ORIGINAL_THRESHOLD = "
                f"{threshold};"
            ),
            "",
            "`endif",
            "",
        ]
    )

    path.write_text(
        "\n".join(lines),
        encoding="ascii",
        newline="\n",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export the selected QAT model "
            "for the FPGA RTL."
        )
    )

    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--hidden-mac-lanes",
        type=int,
        default=4,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.hidden_mac_lanes <= 0:
        raise SystemExit(
            "--hidden-mac-lanes must be positive"
        )

    model_path = (
        args.model_dir
        / "model_qat.pt"
    )

    if not model_path.is_file():
        raise FileNotFoundError(
            model_path
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

    model = construct_model(
        checkpoint
    )

    (
        hidden_weights,
        hidden_biases,
        output_weights,
        output_bias_array,
    ) = extract_integer_parameters(
        model
    )

    threshold = int(
        first(
            checkpoint,
            "validation_threshold",
            "threshold",
            default=0,
        )
    )

    if threshold == 0:
        training_manifest_path = (
            args.model_dir
            / "training_manifest.json"
        )

        if training_manifest_path.is_file():
            training_manifest = (
                json.loads(
                    training_manifest_path.read_text(
                        encoding="utf-8"
                    )
                )
            )

            threshold = int(
                first(
                    training_manifest,
                    "validation_threshold",
                    "threshold",
                    default=0,
                )
            )

    if threshold == 0:
        raise ValueError(
            "Could not find the validation "
            "threshold in model_qat.pt or "
            "training_manifest.json"
        )

    qshift = int(
        first(
            checkpoint,
            "qshift",
            default=getattr(
                model,
                "qshift",
                4,
            ),
        )
    )

    original_output_bias = int(
        output_bias_array[0]
    )

    folded_output_bias = (
        original_output_bias
        - threshold
    )

    bounds = validate_ranges(
        hidden_weights,
        hidden_biases,
        output_weights,
        folded_output_bias,
    )

    output_dir = args.output_dir
    memory_dir = output_dir / "memory"

    memory_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    mem_paths: list[Path] = []

    # Preserve legacy per-neuron files.
    for index in range(
        hidden_weights.shape[0]
    ):
        path = (
            memory_dir
            / f"neuron_{index}_weights.mem"
        )

        write_mem(
            path,
            hidden_weights[index],
        )

        mem_paths.append(path)

    output_mem = (
        memory_dir
        / "output_neuron_weights.mem"
    )

    write_mem(
        output_mem,
        output_weights,
    )

    mem_paths.append(
        output_mem
    )

    # Packed hidden-layer parameters.
    dense_hidden_paths = (
        write_dense_engine_memory(
            memory_dir,
            hidden_weights,
            hidden_biases,
            name="hidden",
            mac_lanes=(
                args.hidden_mac_lanes
            ),
        )
    )

    mem_paths.extend(
        dense_hidden_paths
    )

    # Packed output-layer parameters.
    dense_output_paths = (
        write_dense_engine_memory(
            memory_dir,
            output_weights.reshape(
                1,
                -1,
            ),
            np.asarray(
                [folded_output_bias],
                dtype=np.int64,
            ),
            name="output",
            mac_lanes=1,
        )
    )

    mem_paths.extend(
        dense_output_paths
    )

    # Verify legacy per-neuron files.
    for index, path in enumerate(
        mem_paths[
            :hidden_weights.shape[0]
        ]
    ):
        if not np.array_equal(
            read_mem(path),
            hidden_weights[index],
        ):
            raise RuntimeError(
                f"Verification failed for {path}"
            )

    if not np.array_equal(
        read_mem(output_mem),
        output_weights,
    ):
        raise RuntimeError(
            "Verification failed for "
            f"{output_mem}"
        )

    header_path = (
        output_dir
        / "model_parameters.svh"
    )

    write_header(
        header_path,
        hidden_biases,
        original_output_bias,
        folded_output_bias,
        qshift,
        threshold,
    )

    feature_order = first(
        checkpoint,
        "feature_order",
        "features",
        default=[],
    )

    architecture = (
        f"{hidden_weights.shape[1]}-"
        f"{hidden_weights.shape[0]}-1"
    )

    manifest = {
        "format_version": 1,
        "source_model":
            str(model_path),
        "source_model_sha256":
            sha256(model_path),
        "architecture":
            architecture,
        "feature_order":
            list(feature_order),
        "qshift":
            qshift,
        "classification_rule":
            "folded_score >= 0",
        "original_validation_threshold":
            threshold,
        "hidden_weights":
            hidden_weights.tolist(),
        "hidden_biases":
            hidden_biases.tolist(),
        "output_weights":
            output_weights.tolist(),
        "original_output_bias":
            original_output_bias,
        "folded_output_bias":
            folded_output_bias,
        "accumulator_bounds":
            bounds,
        "dense_engine": {
            "weight_order":
                "output group, input, lane",
            "lane_packing":
                "lane 0 occupies the most-significant bits",
            "hidden_mac_lanes":
                args.hidden_mac_lanes,
            "output_mac_lanes":
                1,
        },
        "files": {},
    }

    for path in [
        *mem_paths,
        header_path,
    ]:
        relative_path = (
            path.relative_to(
                output_dir
            )
        )

        manifest["files"][
            str(relative_path)
        ] = sha256(path)

    manifest_path = (
        output_dir
        / "export_manifest.json"
    )

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = {
        "status":
            "pass",
        "output_dir":
            str(output_dir),
        "architecture":
            architecture,
        "threshold_fold": (
            f"{original_output_bias} "
            f"- {threshold} "
            f"= {folded_output_bias}"
        ),
        "qshift":
            qshift,
        "hidden_mac_lanes":
            args.hidden_mac_lanes,
        "memory_files": [
            str(path)
            for path in mem_paths
        ],
        "parameter_header":
            str(header_path),
        "manifest":
            str(manifest_path),
        "accumulator_bounds":
            bounds,
    }

    print(
        json.dumps(
            result,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()