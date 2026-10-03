"""Offline scalar/batched host benchmark and packed RTL vectors for an export."""
import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from software.variantgate.cli import load_records
from software.variantgate.backends.numpy_backend import NumPyBackend
from software.variantgate.manifest import load_model_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--split', default='validation')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--vectors', type=Path, required=True)
    args = parser.parse_args()
    manifest = load_model_manifest(args.manifest)
    records = load_records(args.input, manifest, args.split, None)
    backend = NumPyBackend(manifest)
    expected = np.array([backend.score_one(r).score for r in records])
    args.vectors.parent.mkdir(parents=True, exist_ok=True)
    args.vectors.write_text(''.join(
        bytes(v & 255 for v in r.features).hex() +
        int(s).to_bytes(4, 'big', signed=True).hex() + '\n'
        for r, s in zip(records, expected)), encoding='utf-8')

    timings = []
    for _ in range(5):
        start = time.perf_counter()
        scores = np.array([backend.score_one(r).score for r in records])
        timings.append(time.perf_counter() - start)
        np.testing.assert_array_equal(scores, expected)
    results = [{'backend': 'scalar_int64', 'seconds': timings,
                'variants_per_second': len(records) / statistics.median(timings)}]
    # Float32 BLAS is a faster host alternative; every result must still match
    # the integer model exactly. No I/O or feature preparation is timed.
    features = np.array([r.features for r in records], dtype=np.float32)
    weights = np.array(manifest.hidden_weights, dtype=np.float32).T.copy()
    biases = np.array(manifest.hidden_biases, dtype=np.float32)
    output = np.array(manifest.output_weights, dtype=np.float32)

    def infer(batch_size):
        scores = np.empty(len(records), dtype=np.int64)
        for begin in range(0, len(records), batch_size):
            end = min(begin + batch_size, len(records))
            hidden = features[begin:end] @ weights + biases
            hidden = np.clip(np.floor((hidden + (1 << (manifest.qshift - 1))) /
                                      (1 << manifest.qshift)), 0, 127)
            scores[begin:end] = hidden @ output + manifest.folded_output_bias
        return scores

    for threads in (1, 4):
        with threadpool_limits(limits=threads):
            for batch_size in (1, 64, 1024):
                np.testing.assert_array_equal(infer(batch_size), expected)
                timings = []
                for _ in range(5):
                    start = time.perf_counter()
                    scores = infer(batch_size)
                    timings.append(time.perf_counter() - start)
                    np.testing.assert_array_equal(scores, expected)
                results.append({'backend': 'batched_float32', 'threads': threads,
                                'batch_size': batch_size, 'seconds': timings,
                                'variants_per_second': len(records) / statistics.median(timings)})
    report = {'architecture': manifest.architecture, 'manifest_sha256': manifest.sha256,
              'split': args.split, 'count': len(records), 'all_scores_bit_exact': True,
              'timing_scope': 'prepared features to scores; excludes input/output I/O',
              'results': results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for result in results:
        print(result['backend'], result.get('batch_size', 1), result.get('threads', 1),
              f"{result['variants_per_second']:.1f} variants/s")


if __name__ == '__main__':
    main()
