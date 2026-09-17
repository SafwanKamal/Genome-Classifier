from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from software.variantgate.schemas import ScoreResult, VariantRecord


class InferenceBackend(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend identifier."""

    def open(self) -> None:
        """Acquire backend resources."""

    @abstractmethod
    def score_one(self, record: VariantRecord) -> ScoreResult:
        """Score one variant record."""

    def score_batch(
        self,
        records: Iterable[VariantRecord],
    ) -> list[ScoreResult]:
        return [self.score_one(record) for record in records]

    def close(self) -> None:
        """Release backend resources."""

    def __enter__(self) -> InferenceBackend:
        self.open()
        return self

    def __exit__(
        self,
        exception_type: object,
        exception_value: object,
        traceback: object,
    ) -> None:
        self.close()
