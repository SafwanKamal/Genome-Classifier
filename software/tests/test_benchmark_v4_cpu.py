import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_v4_cpu import benchmark, compare_fpga


class CPUBenchmarkTests(unittest.TestCase):
    def test_partial_batch_and_each_result_validation(self):
        class Model:
            def score(self, values, dtype):
                return values.sum(axis=1).astype(np.int64)
        features = np.array([[-8,7],[0,1],[10,-4]], dtype=np.float32)
        expected = np.array([-1,1,6])
        result = benchmark(Model(), features, expected, 2, 1, 2)
        self.assertEqual(len(result['seconds']), 2)
        self.assertTrue(result['all_scores_exact'])
        self.assertGreater(result['completed_variants_per_second'], 0)
        with self.assertRaises(AssertionError):
            benchmark(Model(), features, expected+1, 2, 1, 2)

    def test_comparison_rejects_failed_or_different_cohort(self):
        report = dict(requested=3,received=3,correct=3,missing=0,duplicates=0,
                      mismatches=[],error=None,split='test',completed_variants_per_second=100)
        self.assertEqual(compare_fpga(report,3,'test',200)['cpu_over_fpga'], 2)
        for change in (dict(correct=2),dict(requested=4),dict(split='validation'),
                       dict(duplicates=1),dict(completed_variants_per_second=None)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                compare_fpga({**report,**change},3,'test',200)


if __name__ == '__main__':
    unittest.main()
