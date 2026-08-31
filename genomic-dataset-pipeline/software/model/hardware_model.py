from __future__ import annotations

import numpy as np
import torch
from torch import nn


INT8_MIN = -127
INT8_MAX = 127
HIDDEN_QSHIFT = 4


def ste_round(value: torch.Tensor) -> torch.Tensor:
    """Round during forward propagation; use identity gradient."""
    rounded = torch.round(value)
    return value + (rounded - value).detach()


def quantized_parameter(
    value: torch.Tensor,
    minimum: int,
    maximum: int,
) -> torch.Tensor:
    return ste_round(value).clamp(minimum, maximum)


def relu_requantize(
    accumulator: torch.Tensor,
    qshift: int = HIDDEN_QSHIFT,
) -> torch.Tensor:
    """
    Match ReLU_quantizer.sv:

        if accumulator <= 0:
            output = 0
        else:
            output = min((accumulator + 2^(qshift-1)) >> qshift, 127)
    """
    positive = torch.relu(accumulator)
    scaled = positive / float(1 << qshift)

    # Positive round-half-up with a straight-through gradient.
    rounded = torch.floor(scaled + 0.5)
    quantized = scaled + (rounded - scaled).detach()

    return quantized.clamp(0, INT8_MAX)


class HardwareQATModel(nn.Module):
    """Quantization-aware implementation of the FPGA 16-4-1 network."""

    def __init__(
        self,
        input_features: int = 16,
        hidden_features: int = 4,
        qshift: int = HIDDEN_QSHIFT,
        logit_divisor: float = 64.0,
    ) -> None:
        super().__init__()

        self.input_features = input_features
        self.hidden_features = hidden_features
        self.qshift = qshift
        self.logit_divisor = logit_divisor

        self.weight1 = nn.Parameter(
            torch.empty(hidden_features, input_features)
        )
        self.bias1 = nn.Parameter(
            torch.empty(hidden_features)
        )

        self.weight2 = nn.Parameter(
            torch.empty(1, hidden_features)
        )
        self.bias2 = nn.Parameter(
            torch.empty(1)
        )

        self.reset_parameters()

    def reset_parameters(self) -> None:
        # Small integer-scale initialization helps avoid immediate
        # hidden-layer saturation.
        nn.init.uniform_(self.weight1, -3.0, 3.0)
        nn.init.uniform_(self.bias1, -16.0, 16.0)
        nn.init.uniform_(self.weight2, -3.0, 3.0)
        nn.init.zeros_(self.bias2)

    def hardware_outputs(
        self,
        features: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Return the FPGA-equivalent output score and hidden INT8 values.

        Features must contain the signed INT8 values represented as a
        floating-point or integer tensor.
        """
        features = features.to(torch.float32)

        weight1 = quantized_parameter(
            self.weight1,
            INT8_MIN,
            INT8_MAX,
        )
        bias1 = ste_round(self.bias1)

        hidden_accumulator = (
            features @ weight1.transpose(0, 1)
            + bias1
        )

        hidden = relu_requantize(
            hidden_accumulator,
            self.qshift,
        )

        weight2 = quantized_parameter(
            self.weight2,
            INT8_MIN,
            INT8_MAX,
        )
        bias2 = ste_round(self.bias2)

        output_score = (
            hidden @ weight2.transpose(0, 1)
            + bias2
        ).squeeze(1)

        return output_score, hidden

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        output_score, _hidden = self.hardware_outputs(features)

        # The divisor exists only to give BCEWithLogitsLoss a useful
        # numerical range. It does not change the score's sign and is
        # not implemented in the FPGA.
        return output_score / self.logit_divisor

    def integer_parameters(self) -> dict[str, np.ndarray]:
        return {
            "weight1": (
                torch.round(self.weight1)
                .clamp(INT8_MIN, INT8_MAX)
                .to(torch.int64)
                .detach()
                .cpu()
                .numpy()
            ),
            "bias1": (
                torch.round(self.bias1)
                .to(torch.int64)
                .detach()
                .cpu()
                .numpy()
            ),
            "weight2": (
                torch.round(self.weight2)
                .clamp(INT8_MIN, INT8_MAX)
                .to(torch.int64)
                .detach()
                .cpu()
                .numpy()
            ),
            "bias2": (
                torch.round(self.bias2)
                .to(torch.int64)
                .detach()
                .cpu()
                .numpy()
            ),
        }

    def set_integer_parameters(
        self,
        weight1: np.ndarray,
        bias1: np.ndarray,
        weight2: np.ndarray,
        bias2: np.ndarray,
    ) -> None:
        with torch.no_grad():
            self.weight1.copy_(
                torch.as_tensor(weight1, dtype=torch.float32)
            )
            self.bias1.copy_(
                torch.as_tensor(bias1, dtype=torch.float32)
            )
            self.weight2.copy_(
                torch.as_tensor(weight2, dtype=torch.float32)
            )
            self.bias2.copy_(
                torch.as_tensor(bias2, dtype=torch.float32)
            )


def integer_forward_numpy(
    features: np.ndarray,
    weight1: np.ndarray,
    bias1: np.ndarray,
    weight2: np.ndarray,
    bias2: np.ndarray,
    qshift: int = HIDDEN_QSHIFT,
) -> tuple[np.ndarray, np.ndarray]:
    """Bit-exact NumPy reference for the current SystemVerilog datapath."""
    features = np.asarray(features, dtype=np.int64)
    weight1 = np.asarray(weight1, dtype=np.int64)
    bias1 = np.asarray(bias1, dtype=np.int64)
    weight2 = np.asarray(weight2, dtype=np.int64)
    bias2 = np.asarray(bias2, dtype=np.int64)

    hidden_accumulator = (
        features @ weight1.T
        + bias1
    )

    hidden = np.where(
        hidden_accumulator <= 0,
        0,
        (hidden_accumulator + (1 << (qshift - 1))) >> qshift,
    )

    hidden = np.clip(
        hidden,
        0,
        INT8_MAX,
    ).astype(np.int64)

    output_score = (
        hidden @ weight2.T
        + bias2
    ).reshape(-1)

    return output_score, hidden