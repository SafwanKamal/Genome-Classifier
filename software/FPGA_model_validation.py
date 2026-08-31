from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import serial


REQUEST_HEADER = 0x5A
RESPONSE_HEADER = 0xA5
FEATURE_NUMBER = 16


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF

    for byte in data:
        crc ^= byte << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


def build_request(features: list[int]) -> bytes:
    if len(features) != FEATURE_NUMBER:
        raise ValueError(
            f"Expected {FEATURE_NUMBER} features, got {len(features)}"
        )

    if any(value < -128 or value > 127 for value in features):
        raise ValueError("Feature outside signed INT8 range")

    payload = bytes(value & 0xFF for value in features)

    packet_without_crc = bytes([
        REQUEST_HEADER,
        FEATURE_NUMBER,
    ]) + payload

    crc = crc16_ccitt(packet_without_crc)

    return packet_without_crc + crc.to_bytes(
        2,
        byteorder="big",
    )


def read_exact(
    serial_port: serial.Serial,
    byte_count: int,
    timeout: float,
) -> bytes:
    received = bytearray()
    deadline = time.monotonic() + timeout

    while len(received) < byte_count:
        if time.monotonic() >= deadline:
            break

        chunk = serial_port.read(
            byte_count - len(received)
        )

        if chunk:
            received.extend(chunk)

    if len(received) != byte_count:
        raise TimeoutError(
            f"Expected {byte_count} response bytes, "
            f"received {len(received)}"
        )

    return bytes(received)


def read_response(
    serial_port: serial.Serial,
    timeout: float,
) -> tuple[int, bytes]:
    response = read_exact(
        serial_port,
        byte_count=5,
        timeout=timeout,
    )

    if response[0] != RESPONSE_HEADER:
        raise ValueError(
            f"Expected response header "
            f"{RESPONSE_HEADER:02X}, "
            f"received {response[0]:02X}"
        )

    score = int.from_bytes(
        response[1:5],
        byteorder="big",
        signed=True,
    )

    return score, response


def expected_score(
    features: list[int],
    manifest: dict,
) -> tuple[int, list[int], list[int]]:
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

    rounded = (
        hidden_scores
        + (1 << (qshift - 1))
    )

    quantized = np.where(
        hidden_scores <= 0,
        0,
        np.minimum(rounded >> qshift, 127),
    )

    score = int(
        output_weights @ quantized
        + output_bias
    )

    return (
        score,
        hidden_scores.tolist(),
        quantized.tolist(),
    )


def select_tests(
    frame: pd.DataFrame,
    limit: int | None,
    seed: int,
) -> pd.DataFrame:
    test_frame = frame.loc[
        frame["split"] == "test"
    ].copy()

    if limit is None or limit >= len(test_frame):
        return test_frame.reset_index(drop=True)

    positive = test_frame.loc[
        test_frame["label"] == 1
    ]

    negative = test_frame.loc[
        test_frame["label"] == 0
    ]

    positive_count = min(
        limit // 2,
        len(positive),
    )

    negative_count = min(
        limit - positive_count,
        len(negative),
    )

    selected = pd.concat([
        positive.sample(
            n=positive_count,
            random_state=seed,
        ),
        negative.sample(
            n=negative_count,
            random_state=seed + 1,
        ),
    ])

    return selected.sample(
        frac=1,
        random_state=seed + 2,
    ).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare FPGA UART scores against the "
            "bit-exact exported model."
        )
    )

    parser.add_argument(
        "--port",
        required=True,
    )

    parser.add_argument(
        "--baud-rate",
        type=int,
        default=115_200,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
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
        "--limit",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    manifest = json.loads(
        args.manifest.read_text(encoding="utf-8")
    )

    feature_order = manifest["feature_order"]

    if len(feature_order) != FEATURE_NUMBER:
        raise ValueError(
            "Export manifest does not contain "
            "exactly 16 ordered features"
        )

    frame = pd.read_parquet(args.data)

    required_columns = {
        "variant_key",
        "gene",
        "label",
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

    tests = select_tests(
        frame,
        limit=args.limit,
        seed=args.seed,
    )

    confusion = {
        "true_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
        "true_positive": 0,
    }

    print(
        f"Testing {len(tests):,} held-out variants "
        f"on {args.port}"
    )

    with serial.Serial(
        port=args.port,
        baudrate=args.baud_rate,
        timeout=0.05,
    ) as serial_port:
        time.sleep(0.25)
        serial_port.reset_input_buffer()

        for test_index, row in enumerate(
            tests.itertuples(index=False),
            start=1,
        ):
            row_values = row._asdict()

            features = [
                int(row_values[name])
                for name in feature_order
            ]

            expected, hidden, quantized = (
                expected_score(
                    features,
                    manifest,
                )
            )

            request = build_request(features)

            serial_port.reset_input_buffer()
            serial_port.write(request)
            serial_port.flush()

            try:
                fpga_score, response = read_response(
                    serial_port,
                    timeout=args.timeout,
                )
            except Exception as error:
                print(
                    f"\nUART failure on test {test_index}"
                )
                print(
                    "Variant:",
                    row_values["variant_key"],
                )
                print("Features:", features)
                print(
                    "Request:",
                    request.hex(" ").upper(),
                )
                raise RuntimeError(str(error)) from error

            if fpga_score != expected:
                print(
                    f"\nBIT-EXACT FAILURE "
                    f"on test {test_index}"
                )
                print(
                    "Variant:",
                    row_values["variant_key"],
                )
                print("Features:", features)
                print("Hidden scores:", hidden)
                print("Quantized hidden:", quantized)
                print("Expected score:", expected)
                print("FPGA score:", fpga_score)
                print(
                    "Response:",
                    response.hex(" ").upper(),
                )
                raise SystemExit(1)

            label = int(row_values["label"])
            prediction = int(fpga_score >= 0)

            if label == 0 and prediction == 0:
                confusion["true_negative"] += 1
            elif label == 0 and prediction == 1:
                confusion["false_positive"] += 1
            elif label == 1 and prediction == 0:
                confusion["false_negative"] += 1
            else:
                confusion["true_positive"] += 1

            if (
                test_index == 1
                or test_index % args.progress_every == 0
                or test_index == len(tests)
            ):
                print(
                    f"Passed {test_index:,}/"
                    f"{len(tests):,}"
                )

    tn = confusion["true_negative"]
    fp = confusion["false_positive"]
    fn = confusion["false_negative"]
    tp = confusion["true_positive"]

    accuracy = (
        (tn + tp) / len(tests)
        if len(tests)
        else 0.0
    )

    print(json.dumps({
        "status": "pass",
        "bit_exact_matches": len(tests),
        "bit_exact_mismatches": 0,
        "classification_rule": "score >= 0",
        "accuracy": accuracy,
        "confusion_matrix": confusion,
    }, indent=2))


if __name__ == "__main__":
    main()