from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence


FEATURE_NUMBER = 16


@dataclass(frozen=True, slots=True)
class VariantRecord:
    variant_key: str
    gene: str | None
    features: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.variant_key:
            raise ValueError("variant_key cannot be empty")

        if len(self.features) != FEATURE_NUMBER:
            raise ValueError(
                f"Expected {FEATURE_NUMBER} features, "
                f"received {len(self.features)}"
            )

        for feature_index, value in enumerate(self.features):
            if not isinstance(value, int):
                raise TypeError(
                    f"Feature {feature_index} is not an integer: {value!r}"
                )

            if not -128 <= value <= 127:
                raise ValueError(
                    f"Feature {feature_index} is outside signed INT8 range: "
                    f"{value}"
                )

    @classmethod
    def from_values(
        cls,
        variant_key: str,
        gene: str | None,
        features: Sequence[int],
    ) -> VariantRecord:
        return cls(
            variant_key=str(variant_key),
            gene=gene,
            features=tuple(int(value) for value in features),
        )


@dataclass(frozen=True, slots=True)
class ScoreResult:
    variant_key: str
    gene: str | None
    score: int
    prediction: int
    backend: str
    latency_ns: int
    reference_score: int | None = None

    def __post_init__(self) -> None:
        if not -(1 << 31) <= self.score <= (1 << 31) - 1:
            raise ValueError(
                f"Score is outside signed INT32 range: {self.score}"
            )

        expected_prediction = int(self.score >= 0)

        if self.prediction != expected_prediction:
            raise ValueError(
                "Prediction does not match the score >= 0 classification rule"
            )

        if self.latency_ns < 0:
            raise ValueError("latency_ns cannot be negative")

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["latency_us"] = self.latency_ns / 1000.0
        return result
