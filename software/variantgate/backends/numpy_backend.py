from __future__ import annotations

import time

import numpy as np

from software.variantgate.backends.base import InferenceBackend
from software.variantgate.manifest import ModelManifest
from software.variantgate.schemas import ScoreResult, VariantRecord


class NumPyBackend(InferenceBackend):
    def __init__(self, manifest: ModelManifest) -> None:
        self._manifest = manifest
        self._hidden_weights = np.asarray(
            manifest.hidden_weights,
            dtype=np.int64,
        )
        self._hidden_biases = np.asarray(
            manifest.hidden_biases,
            dtype=np.int64,
        )
        self._output_weights = np.asarray(
            manifest.output_weights,
            dtype=np.int64,
        )

    @property
    def name(self) -> str:
        return "numpy"

    def score_one(self, record: VariantRecord) -> ScoreResult:
        start_ns = time.perf_counter_ns()

        features = np.asarray(record.features, dtype=np.int64)
        hidden_scores = (
            self._hidden_weights @ features + self._hidden_biases
        )
        rounding_offset = 1 << (self._manifest.qshift - 1)
        quantized_hidden = np.where(
            hidden_scores <= 0,
            0,
            np.minimum(
                (hidden_scores + rounding_offset) >> self._manifest.qshift,
                127,
            ),
        )
        score = int(
            self._output_weights @ quantized_hidden
            + self._manifest.folded_output_bias
        )

        end_ns = time.perf_counter_ns()

        return ScoreResult(
            variant_key=record.variant_key,
            gene=record.gene,
            score=score,
            prediction=int(score >= 0),
            backend=self.name,
            latency_ns=end_ns - start_ns,
        )
