"""Benchmark frozen V4 CPU inference without a training checkpoint or FPGA."""
import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_info, threadpool_limits

from batch_model_reference import BatchModelReference


def infer_batches(model, features, batch_size):
    return np.concatenate([model.score(features[i:i+batch_size], np.float32)
                           for i in range(0, len(features), batch_size)])


def benchmark(model, features, expected, batch_size, threads, repetitions):
    with threadpool_limits(limits=threads):
        # Warm the implementation and check correctness before measurement.
        np.testing.assert_array_equal(infer_batches(model, features, batch_size), expected)
        seconds = []
        for _ in range(repetitions):
            started = time.perf_counter()
            scores = infer_batches(model, features, batch_size)
            seconds.append(time.perf_counter()-started)
            # Validation is outside the timed interval, but every run must pass.
            np.testing.assert_array_equal(scores, expected)
        return dict(batch_size=batch_size, threads=threads, seconds=seconds,
                    median_seconds=statistics.median(seconds),
                    completed_variants_per_second=len(features)/statistics.median(seconds),
                    all_scores_exact=True, threadpools=threadpool_info())


def compare_fpga(report, cpu_count, split, cpu_rate):
    if (report.get('requested') != cpu_count or report.get('split') != split or
        report.get('correct') != cpu_count or report.get('received') != cpu_count or
        report.get('missing') != 0 or report.get('duplicates', 0) != 0 or
        report.get('mismatches') or report.get('error')):
        raise ValueError('FPGA comparison requires a passing report for the same count and split')
    rate = report.get('completed_variants_per_second')
    if not isinstance(rate, (float, int)) or not np.isfinite(rate) or rate <= 0:
        raise ValueError('FPGA report has no valid completed throughput')
    return dict(fpga_completed_variants_per_second=rate,
                cpu_completed_variants_per_second=cpu_rate,
                cpu_over_fpga=cpu_rate/rate, fpga_over_cpu=rate/cpu_rate,
                fpga_batch_size=report.get('batch_size'), fpga_window=report.get('window'),
                scope='CPU prepared-feature inference versus FPGA host-observed request/reply completion; '
                      'both exclude preparation. Run both on the same host and frozen model. '
                      'The FPGA report does not record a model hash or host identity, so those cannot be verified here.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('genomic-dataset-pipeline/artifacts/model_v4_batch_seed_7/export_manifest.json'))
    parser.add_argument('--input', type=Path, default=Path('genomic-dataset-pipeline/data/processed/variants_model_int8.parquet'))
    parser.add_argument('--split', default='test')
    parser.add_argument('--count', type=int, default=27477)
    parser.add_argument('--batch-sizes', type=int, nargs='+', default=[32, 64, 1024])
    parser.add_argument('--threads', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--fpga-report', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if min([args.count, args.repetitions]+args.batch_sizes+args.threads) < 1:
        parser.error('Count, repetitions, batch sizes and thread counts must be positive')
    started = time.perf_counter()
    model = BatchModelReference(args.manifest)
    data = pd.read_parquet(args.input)
    rows = data.loc[data['split'].eq(args.split)].head(args.count)
    features = rows[model.manifest['feature_order']].to_numpy(dtype=np.int64)
    if len(features) != args.count:
        parser.error('Not enough variants in requested split')
    if np.any(features < -128) or np.any(features > 127):
        raise ValueError('Features outside signed INT8 range')
    prepared = features.astype(np.float32)
    preparation_seconds = time.perf_counter()-started
    started = time.perf_counter()
    with threadpool_limits(limits=1):
        expected = np.concatenate([model.score(features[i:i+1024])
                                   for i in range(0, len(features), 1024)])
    reference_seconds = time.perf_counter()-started
    results = []
    for threads in args.threads:
        for size in args.batch_sizes:
            result = benchmark(model, prepared, expected, size, threads, args.repetitions)
            results.append(result)
            print(f'{threads} threads, batch {size}: {result["completed_variants_per_second"]:,.1f} variants/s; all scores exact', flush=True)
    best = max(results, key=lambda item: item['completed_variants_per_second'])
    report = dict(architecture=model.manifest['architecture'], split=args.split, count=args.count,
                  manifest=str(args.manifest), manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                  feature_sha256=hashlib.sha256(features.astype(np.int8).tobytes()).hexdigest(),
                  scores_sha256=hashlib.sha256(expected.astype('<i8').tobytes()).hexdigest(),
                  machine=dict(hostname=platform.node(), platform=platform.platform(), processor=platform.processor(),
                               logical_processors=os.cpu_count(), python=platform.python_version(), numpy=np.__version__),
                  preparation_seconds=preparation_seconds, integer_reference_seconds=reference_seconds,
                  repetitions=args.repetitions, warmups_per_configuration=1, all_scores_exact=True,
                  results=results,
                  best_configuration={key:best[key] for key in ('batch_size','threads','median_seconds','completed_variants_per_second')},
                  timing_scope='Prepared float32 features to all integer scores, including batch orchestration; '
                               'excludes file loading, feature conversion, integer reference, warmup and score validation. '
                               'Rates use median warmed time, not fastest repetition. No Ethernet or FPGA calls.')
    if args.fpga_report:
        report['fpga_comparison'] = compare_fpga(json.loads(args.fpga_report.read_text()), args.count,
                                               args.split, best['completed_variants_per_second'])
        report['fpga_comparison']['report'] = str(args.fpga_report)
        print(f'Best CPU / FPGA throughput: {report["fpga_comparison"]["cpu_over_fpga"]:.3f}x')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(f'Saved {args.report}')


if __name__ == '__main__':
    main()
