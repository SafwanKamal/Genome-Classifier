from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


DEEP_REVIEW = "deep_review"
LIGHT_REVIEW = "light_review"


@dataclass(frozen=True, slots=True)
class RoutingStatistics:
    threshold: int
    target_pathogenic_recall: float
    achieved_pathogenic_recall: float
    variant_number: int
    pathogenic_number: int
    benign_number: int
    deep_review_number: int
    light_review_number: int
    deep_review_fraction: float
    light_review_fraction: float
    deep_pathogenic: int
    light_pathogenic: int
    deep_benign: int
    light_benign: int

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "target_pathogenic_recall": self.target_pathogenic_recall,
            "achieved_pathogenic_recall": (
                self.achieved_pathogenic_recall
            ),
            "variant_number": self.variant_number,
            "pathogenic_number": self.pathogenic_number,
            "benign_number": self.benign_number,
            "deep_review_number": self.deep_review_number,
            "light_review_number": self.light_review_number,
            "deep_review_fraction": self.deep_review_fraction,
            "light_review_fraction": self.light_review_fraction,
            "routing_confusion_matrix": {
                "deep_pathogenic": self.deep_pathogenic,
                "light_pathogenic": self.light_pathogenic,
                "deep_benign": self.deep_benign,
                "light_benign": self.light_benign,
            },
        }


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    path: Path
    format_version: int
    policy_name: str
    threshold: int
    target_pathogenic_recall: float
    achieved_pathogenic_recall: float
    selection_split: str
    model_manifest_sha256: str
    source_model_sha256: str
    architecture: str

    def route_score(self, score: int) -> str:
        return DEEP_REVIEW if score >= self.threshold else LIGHT_REVIEW

    def route_scores(self, scores: Sequence[int]) -> np.ndarray:
        score_array = _validated_score_array(scores)
        return np.where(
            score_array >= self.threshold,
            DEEP_REVIEW,
            LIGHT_REVIEW,
        )


def _validated_score_array(scores: Sequence[int]) -> np.ndarray:
    score_array = np.asarray(scores)

    if score_array.ndim != 1:
        raise ValueError("Scores must be a one-dimensional sequence")

    if score_array.size == 0:
        raise ValueError("At least one score is required")

    if not np.issubdtype(score_array.dtype, np.number):
        raise TypeError("Scores must be numeric")

    numeric_scores = score_array.astype(np.float64)

    if not np.isfinite(numeric_scores).all():
        raise ValueError("Scores contain a missing or non-finite value")

    if not np.equal(numeric_scores, np.floor(numeric_scores)).all():
        raise ValueError("Scores must be integers")

    if (
        (numeric_scores < -(1 << 31)).any()
        or (numeric_scores > (1 << 31) - 1).any()
    ):
        raise ValueError("A score is outside signed INT32 range")

    return numeric_scores.astype(np.int64)


def _validated_label_array(labels: Sequence[int]) -> np.ndarray:
    label_array = np.asarray(labels)

    if label_array.ndim != 1:
        raise ValueError("Labels must be a one-dimensional sequence")

    if label_array.size == 0:
        raise ValueError("At least one label is required")

    try:
        numeric_labels = label_array.astype(np.float64)
    except (TypeError, ValueError) as error:
        raise TypeError("Labels must be numeric") from error

    if not np.isfinite(numeric_labels).all():
        raise ValueError("Labels contain a missing or non-finite value")

    if not np.isin(numeric_labels, [0, 1]).all():
        raise ValueError("Labels must contain only 0 and 1")

    return numeric_labels.astype(np.int8)


def select_routing_threshold(
    scores: Sequence[int],
    labels: Sequence[int],
    target_pathogenic_recall: float,
) -> RoutingStatistics:
    if not 0.0 < target_pathogenic_recall <= 1.0:
        raise ValueError(
            "target_pathogenic_recall must be greater than 0 and at most 1"
        )

    score_array = _validated_score_array(scores)
    label_array = _validated_label_array(labels)

    if score_array.size != label_array.size:
        raise ValueError("Scores and labels must have the same length")

    pathogenic_scores = score_array[label_array == 1]

    if pathogenic_scores.size == 0:
        raise ValueError(
            "Cannot select a pathogenic-recall threshold without "
            "pathogenic validation variants"
        )

    required_pathogenic_number = math.ceil(
        target_pathogenic_recall * pathogenic_scores.size
    )
    sorted_pathogenic_scores = np.sort(pathogenic_scores)[::-1]
    threshold = int(
        sorted_pathogenic_scores[required_pathogenic_number - 1]
    )

    deep_mask = score_array >= threshold
    pathogenic_mask = label_array == 1
    benign_mask = ~pathogenic_mask

    deep_pathogenic = int(np.count_nonzero(deep_mask & pathogenic_mask))
    light_pathogenic = int(np.count_nonzero(~deep_mask & pathogenic_mask))
    deep_benign = int(np.count_nonzero(deep_mask & benign_mask))
    light_benign = int(np.count_nonzero(~deep_mask & benign_mask))
    variant_number = int(score_array.size)
    pathogenic_number = int(pathogenic_scores.size)
    benign_number = variant_number - pathogenic_number
    deep_review_number = int(np.count_nonzero(deep_mask))
    light_review_number = variant_number - deep_review_number
    achieved_recall = deep_pathogenic / pathogenic_number

    if achieved_recall < target_pathogenic_recall:
        raise RuntimeError(
            "Selected routing threshold does not satisfy the requested recall"
        )

    return RoutingStatistics(
        threshold=threshold,
        target_pathogenic_recall=target_pathogenic_recall,
        achieved_pathogenic_recall=achieved_recall,
        variant_number=variant_number,
        pathogenic_number=pathogenic_number,
        benign_number=benign_number,
        deep_review_number=deep_review_number,
        light_review_number=light_review_number,
        deep_review_fraction=deep_review_number / variant_number,
        light_review_fraction=light_review_number / variant_number,
        deep_pathogenic=deep_pathogenic,
        light_pathogenic=light_pathogenic,
        deep_benign=deep_benign,
        light_benign=light_benign,
    )


def load_routing_policy(path: Path) -> RoutingPolicy:
    path = path.resolve()
    raw = json.loads(path.read_text(encoding="utf-8"))

    required_fields = {
        "format_version",
        "policy_name",
        "rule",
        "selection",
        "model",
    }
    missing_fields = sorted(required_fields - set(raw))

    if missing_fields:
        raise ValueError(
            "Routing policy is missing fields: "
            + ", ".join(missing_fields)
        )

    if int(raw["format_version"]) != 1:
        raise ValueError(
            f"Unsupported routing-policy format: {raw['format_version']}"
        )

    rule = raw["rule"]
    selection = raw["selection"]
    model = raw["model"]

    if rule.get("deep_review") != "score >= threshold":
        raise ValueError("Unsupported deep-review routing rule")

    if rule.get("light_review") != "score < threshold":
        raise ValueError("Unsupported light-review routing rule")

    threshold = int(rule["threshold"])

    if not -(1 << 31) <= threshold <= (1 << 31) - 1:
        raise ValueError("Routing threshold is outside signed INT32 range")

    target_recall = float(selection["target_pathogenic_recall"])
    achieved_recall = float(selection["achieved_pathogenic_recall"])

    if not 0.0 < target_recall <= 1.0:
        raise ValueError("Invalid target pathogenic recall in routing policy")

    if not target_recall <= achieved_recall <= 1.0:
        raise ValueError("Invalid achieved pathogenic recall in routing policy")

    selection_split = str(selection["split"])

    if selection_split.lower() == "test":
        raise ValueError("A routing policy cannot be selected on the test split")

    return RoutingPolicy(
        path=path,
        format_version=1,
        policy_name=str(raw["policy_name"]),
        threshold=threshold,
        target_pathogenic_recall=target_recall,
        achieved_pathogenic_recall=achieved_recall,
        selection_split=selection_split,
        model_manifest_sha256=str(model["export_manifest_sha256"]),
        source_model_sha256=str(model["source_model_sha256"]),
        architecture=str(model["architecture"]),
    )
