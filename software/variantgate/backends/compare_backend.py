from __future__ import annotations

from software.variantgate.backends.base import InferenceBackend
from software.variantgate.schemas import ScoreResult, VariantRecord


class ComparisonBackend(InferenceBackend):
    def __init__(
        self,
        candidate: InferenceBackend,
        reference: InferenceBackend,
    ) -> None:
        self._candidate = candidate
        self._reference = reference

    @property
    def name(self) -> str:
        return f"compare:{self._candidate.name}:{self._reference.name}"

    def open(self) -> None:
        self._reference.open()

        try:
            self._candidate.open()
        except Exception:
            self._reference.close()
            raise

    def close(self) -> None:
        try:
            self._candidate.close()
        finally:
            self._reference.close()

    def score_one(self, record: VariantRecord) -> ScoreResult:
        reference_result = self._reference.score_one(record)
        candidate_result = self._candidate.score_one(record)

        if candidate_result.score != reference_result.score:
            raise RuntimeError(
                "Bit-exact backend mismatch\n"
                f"Variant: {record.variant_key}\n"
                f"Features: {list(record.features)}\n"
                f"Candidate backend: {self._candidate.name}\n"
                f"Candidate score: {candidate_result.score}\n"
                f"Reference backend: {self._reference.name}\n"
                f"Reference score: {reference_result.score}"
            )

        return ScoreResult(
            variant_key=candidate_result.variant_key,
            gene=candidate_result.gene,
            score=candidate_result.score,
            prediction=candidate_result.prediction,
            backend=self.name,
            latency_ns=candidate_result.latency_ns,
            reference_score=reference_result.score,
        )
