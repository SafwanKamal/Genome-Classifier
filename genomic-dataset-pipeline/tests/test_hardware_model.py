from __future__ import annotations

import numpy as np
import torch

from software.model.hardware_model import (
    HardwareQATModel,
    integer_forward_numpy,
)


def test_torch_model_matches_integer_reference() -> None:
    rng = np.random.default_rng(42)

    features = rng.integers(
        -127,
        128,
        size=(128, 16),
        dtype=np.int16,
    ).astype(np.int64)

    weight1 = rng.integers(
        -8,
        9,
        size=(4, 16),
        dtype=np.int16,
    ).astype(np.int64)

    bias1 = rng.integers(
        -256,
        257,
        size=4,
        dtype=np.int32,
    ).astype(np.int64)

    weight2 = rng.integers(
        -8,
        9,
        size=(1, 4),
        dtype=np.int16,
    ).astype(np.int64)

    bias2 = rng.integers(
        -256,
        257,
        size=1,
        dtype=np.int32,
    ).astype(np.int64)

    expected_score, expected_hidden = integer_forward_numpy(
        features,
        weight1,
        bias1,
        weight2,
        bias2,
    )

    model = HardwareQATModel()
    model.set_integer_parameters(
        weight1,
        bias1,
        weight2,
        bias2,
    )

    with torch.no_grad():
        actual_score, actual_hidden = model.hardware_outputs(
            torch.from_numpy(features)
        )

    np.testing.assert_array_equal(
        actual_score.to(torch.int64).numpy(),
        expected_score,
    )

    np.testing.assert_array_equal(
        actual_hidden.to(torch.int64).numpy(),
        expected_hidden,
    )


def test_relu_rounding_and_saturation() -> None:
    features = np.zeros((8, 16), dtype=np.int64)
    features[:, 0] = np.array(
        [-1, 0, 1, 7, 8, 15, 16, 5000],
        dtype=np.int64,
    )

    weight1 = np.zeros((4, 16), dtype=np.int64)
    weight1[0, 0] = 1

    bias1 = np.zeros(4, dtype=np.int64)
    weight2 = np.zeros((1, 4), dtype=np.int64)
    bias2 = np.zeros(1, dtype=np.int64)

    _score, hidden = integer_forward_numpy(
        features,
        weight1,
        bias1,
        weight2,
        bias2,
    )

    np.testing.assert_array_equal(
        hidden[:, 0],
        np.array(
            [0, 0, 0, 0, 1, 1, 1, 127],
            dtype=np.int64,
        ),
    )