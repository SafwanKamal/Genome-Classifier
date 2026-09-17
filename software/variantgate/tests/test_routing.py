from __future__ import annotations

import unittest

from software.variantgate.routing import (
    DEEP_REVIEW,
    LIGHT_REVIEW,
    select_routing_threshold,
)


class RoutingThresholdTest(unittest.TestCase):
    def test_selects_largest_threshold_satisfying_recall(self) -> None:
        statistics = select_routing_threshold(
            scores=[100, 80, 60, 40, 90, 50, -20],
            labels=[1, 1, 1, 1, 0, 0, 0],
            target_pathogenic_recall=0.75,
        )

        self.assertEqual(statistics.threshold, 60)
        self.assertEqual(statistics.deep_pathogenic, 3)
        self.assertEqual(statistics.light_pathogenic, 1)
        self.assertEqual(statistics.deep_benign, 1)
        self.assertEqual(statistics.light_benign, 2)
        self.assertEqual(statistics.achieved_pathogenic_recall, 0.75)

    def test_score_ties_can_exceed_target_recall(self) -> None:
        statistics = select_routing_threshold(
            scores=[10, 5, 5, 0, -10],
            labels=[1, 1, 1, 1, 0],
            target_pathogenic_recall=0.5,
        )

        self.assertEqual(statistics.threshold, 5)
        self.assertEqual(statistics.achieved_pathogenic_recall, 0.75)

    def test_full_recall_uses_lowest_pathogenic_score(self) -> None:
        statistics = select_routing_threshold(
            scores=[7, -3, 50, -20],
            labels=[1, 1, 0, 0],
            target_pathogenic_recall=1.0,
        )

        self.assertEqual(statistics.threshold, -3)
        self.assertEqual(statistics.light_pathogenic, 0)

    def test_threshold_boundary_routes_to_deep_review(self) -> None:
        statistics = select_routing_threshold(
            scores=[20, 10, 0, -10],
            labels=[1, 1, 0, 0],
            target_pathogenic_recall=0.5,
        )

        routes = [
            DEEP_REVIEW if score >= statistics.threshold else LIGHT_REVIEW
            for score in [21, 20, 19]
        ]

        self.assertEqual(
            routes,
            [DEEP_REVIEW, DEEP_REVIEW, LIGHT_REVIEW],
        )

    def test_rejects_missing_pathogenic_examples(self) -> None:
        with self.assertRaisesRegex(ValueError, "without pathogenic"):
            select_routing_threshold(
                scores=[10, 0, -10],
                labels=[0, 0, 0],
                target_pathogenic_recall=0.995,
            )

    def test_rejects_non_binary_labels(self) -> None:
        with self.assertRaisesRegex(ValueError, "only 0 and 1"):
            select_routing_threshold(
                scores=[10, 0, -10],
                labels=[1, 2, 0],
                target_pathogenic_recall=0.995,
            )


if __name__ == "__main__":
    unittest.main()
